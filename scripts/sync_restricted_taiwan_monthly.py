from __future__ import annotations

import argparse
import calendar
import json
import re
from datetime import date
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit

from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import Tracker, TrackerTarget
from tracker.services.tracker_sync_service import TrackerSyncService


RESTRICTED_DOMAIN_MARKERS = (
    "site%3Acna.com.tw",
    "site%3Apresident.gov.tw",
    "site%3Amofa.gov.tw",
    "site:cna.com.tw",
    "site:president.gov.tw",
    "site:mofa.gov.tw",
)


def _month_range(year: int, month: int) -> tuple[str, str]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


def _is_restricted_target(url: str) -> bool:
    lowered = (url or "").lower()
    unquoted = unquote(lowered)
    unquoted_twice = unquote(unquoted)
    return any(
        marker in lowered or marker in unquoted or marker in unquoted_twice
        for marker in RESTRICTED_DOMAIN_MARKERS
    )


def _retarget_url_to_month(url: str, year: int, month: int) -> str:
    start_iso, end_iso = _month_range(year, month)
    split = urlsplit(url)
    params = parse_qs(split.query, keep_blank_values=True)
    q_values = params.get("q")
    if not q_values:
        return url
    q = q_values[0]
    q = re.sub(r"after:\d{4}-\d{2}-\d{2}", f"after:{start_iso}", q)
    q = re.sub(r"before:\d{4}-\d{2}-\d{2}", f"before:{end_iso}", q)
    if "after:" not in q:
        q = f"{q} after:{start_iso}".strip()
    if "before:" not in q:
        q = f"{q} before:{end_iso}".strip()
    params["q"] = [q]
    new_query = urlencode(params, doseq=True)
    return urlunsplit((split.scheme, split.netloc, split.path, new_query, split.fragment))


def _sync_one_month(
    year: int,
    month: int,
    start_tracker_id: int | None = None,
    max_trackers: int | None = None,
    report_every: int = 100,
) -> dict[str, object]:
    with session_scope() as session:
        tracker_id_rows = session.execute(
            select(Tracker.id)
            .join(TrackerTarget, TrackerTarget.tracker_id == Tracker.id)
            .where(
                Tracker.status == "active",
                Tracker.name == f"{year} Taiwan monitor",
                TrackerTarget.is_active.is_(True),
                TrackerTarget.target_type == "rss_feed",
            )
            .group_by(Tracker.id)
            .order_by(Tracker.id.asc())
        ).all()
        tracker_ids = [row[0] for row in tracker_id_rows]
        if start_tracker_id:
            tracker_ids = [tid for tid in tracker_ids if tid >= start_tracker_id]
        if max_trackers:
            tracker_ids = tracker_ids[: max(1, max_trackers)]

        sync_service = TrackerSyncService(session)
        total_found = 0
        total_created = 0
        total_updated = 0
        total_failed = 0
        processed = 0

        for tracker_id in tracker_ids:
            tracker = session.get(Tracker, tracker_id)
            if not tracker:
                continue
            targets = session.execute(
                select(TrackerTarget).where(
                    TrackerTarget.tracker_id == tracker.id,
                    TrackerTarget.is_active.is_(True),
                )
            ).scalars().all()
            changed = 0
            restricted_count = 0
            for target in targets:
                is_restricted_rss = target.target_type == "rss_feed" and _is_restricted_target(target.target_url)
                if not is_restricted_rss:
                    if target.is_active:
                        target.is_active = False
                        changed += 1
                    continue
                restricted_count += 1
                new_url = _retarget_url_to_month(target.target_url, year=year, month=month)
                if new_url != target.target_url:
                    target.target_url = new_url
                    changed += 1
                target.parser_identity = f"google_news_taiwan_y{year}m{month:02d}_restricted_v1"
            if changed:
                session.flush()
            if restricted_count == 0:
                processed += 1
                continue

            result = sync_service.sync_tracker(tracker)
            total_found += result.records_found
            total_created += result.records_created
            total_updated += result.records_updated
            if result.errors:
                total_failed += 1

            processed += 1
            if processed % max(1, report_every) == 0:
                session.commit()
                print(
                    json.dumps(
                        {
                            "year": year,
                            "month": month,
                            "progress": f"{processed}/{len(tracker_ids)}",
                            "records_created": total_created,
                            "records_updated": total_updated,
                            "failed_trackers": total_failed,
                            "last_tracker_id": tracker_id,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

        session.commit()
        return {
            "year": year,
            "month": month,
            "month_name": calendar.month_name[month],
            "trackers_total": len(tracker_ids),
            "trackers_processed": processed,
            "trackers_failed": total_failed,
            "records_found": total_found,
            "records_created": total_created,
            "records_updated": total_updated,
            "last_tracker_id": tracker_ids[-1] if tracker_ids else None,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync restricted Taiwan event trackers month-by-month for a target year.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--months", default="4,3,2,1", help="Comma-separated month list, e.g. 4,3,2,1")
    parser.add_argument("--start-tracker-id", type=int, default=None)
    parser.add_argument("--max-trackers", type=int, default=None)
    parser.add_argument("--report-every", type=int, default=100)
    args = parser.parse_args()

    months = [int(item.strip()) for item in str(args.months).split(",") if item.strip()]
    months = [month for month in months if 1 <= month <= 12]
    if not months:
        raise SystemExit("No valid months provided.")

    results: list[dict[str, object]] = []
    for month in months:
        print(json.dumps({"status": "running", "year": args.year, "month": month}, ensure_ascii=False), flush=True)
        month_result = _sync_one_month(
            year=args.year,
            month=month,
            start_tracker_id=args.start_tracker_id,
            max_trackers=args.max_trackers,
            report_every=max(1, args.report_every),
        )
        results.append(month_result)
        print(json.dumps({"status": "month_done", **month_result}, ensure_ascii=False), flush=True)

    print(json.dumps({"status": "done", "year": args.year, "months": months, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
