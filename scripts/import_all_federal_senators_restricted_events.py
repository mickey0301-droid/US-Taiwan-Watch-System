from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from scripts.discover_restricted_source_events import (
    TAIWAN_KEYWORDS,
    _contains_any,
    _month_bounds,
    dedupe_hits,
    discover_cna,
    discover_mofa,
    discover_president,
)
from tracker.db import session_scope
from tracker.models import Alias, Appointment, Office, Person
from tracker.services.statements_service import StatementsService


USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"


def _ingest_with_retry(service: StatementsService, payload: dict[str, object], retries: int = 8):
    for attempt in range(retries):
        try:
            return service.ingest_statement(payload)
        except OperationalError as exc:
            message = str(exc).lower()
            if "database is locked" not in message or attempt == retries - 1:
                raise
            time.sleep(min(4.0, 0.5 * (2**attempt)))


def _ingest_payload_with_session_retry(payload: dict[str, object], retries: int = 8) -> bool:
    for attempt in range(retries):
        try:
            with session_scope() as session:
                service = StatementsService(session)
                _, is_created = _ingest_with_retry(service, payload, retries=1)
                return bool(is_created)
        except OperationalError as exc:
            message = str(exc).lower()
            if "database is locked" not in message or attempt == retries - 1:
                raise
            time.sleep(min(4.0, 0.5 * (2**attempt)))
    return False


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _clean_title(value: str) -> str:
    title = (value or "").strip()
    if " | " in title:
        title = title.split(" | ", 1)[0].strip()
    return title


def _source_type(source: str) -> str:
    return "official" if source in {"mofa.gov.tw", "president.gov.tw"} else "media"


def _is_primary(source: str) -> bool:
    return source in {"mofa.gov.tw", "president.gov.tw"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import restricted-source Taiwan events for all federal senators.")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--months", default="4,3,2,1", help="Comma-separated months")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--only-current", action="store_true")
    parser.add_argument("--start-index", type=int, default=0, help="Start from person index (0-based) for resume")
    parser.add_argument("--max-people", type=int, default=None, help="Limit people count after start-index")
    parser.add_argument("--output", default="data/raw/federal_senator_events_summary.json")
    args = parser.parse_args()

    months = [int(item.strip()) for item in str(args.months).split(",") if item.strip()]
    months = [m for m in months if 1 <= m <= 12]
    if not months:
        raise SystemExit("No valid months.")
    start, end = _month_bounds(args.year, months)

    with session_scope() as session:
        stmt = (
            select(Person.id, Person.full_name, Office.office_name, Appointment.role_title)
            .join(Appointment, Appointment.person_id == Person.id)
            .join(Office, Office.id == Appointment.office_id)
            .where(
                Office.level == "federal",
                Office.branch == "legislative",
                Office.chamber == "senate",
            )
        )
        if args.only_current:
            stmt = stmt.where(Appointment.status == "current")
        rows = session.execute(stmt).all()

        dedup: dict[int, tuple[int, str, str]] = {}
        for person_id, full_name, office_name, role_title in rows:
            title = (role_title or office_name or "").strip()
            if person_id not in dedup:
                dedup[person_id] = (person_id, full_name, title)
        people = sorted(dedup.values(), key=lambda x: (x[1] or "").lower())
        if args.start_index > 0:
            people = people[args.start_index :]
        if args.max_people is not None and args.max_people >= 0:
            people = people[: args.max_people]

        aliases_map: dict[int, list[str]] = {}
        for person_id, full_name, _title in people:
            aliases = session.execute(select(Alias.alias).where(Alias.person_id == person_id)).scalars().all()
            terms = [full_name] + [alias.strip() for alias in aliases if (alias or "").strip()]
            aliases_map[person_id] = list(dict.fromkeys(terms))

    summary_rows: list[dict[str, object]] = []
    source_errors: list[dict[str, str]] = []
    check_failures: list[dict[str, str]] = []

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        with httpx.Client(headers=headers, follow_redirects=True, verify=False) as insecure_client:
            for idx, (person_id, full_name, office_title) in enumerate(people, start=1):
                person_terms = aliases_map.get(person_id, [full_name])
                hits = []
                try:
                    hits += discover_cna(client, insecure_client, person_terms=person_terms, start=start, end=end)
                except Exception as exc:
                    source_errors.append({"person": full_name, "source": "cna", "error": str(exc)})
                try:
                    hits += discover_mofa(
                        client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=args.max_pages
                    )
                except Exception as exc:
                    source_errors.append({"person": full_name, "source": "mofa", "error": str(exc)})
                try:
                    hits += discover_president(
                        client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=args.max_pages
                    )
                except Exception as exc:
                    source_errors.append({"person": full_name, "source": "president", "error": str(exc)})

                discovered = dedupe_hits(hits)
                for hit in discovered:
                    merged = f"{hit.title} {hit.excerpt}".strip()
                    if not (_contains_any(merged, person_terms) and _contains_any(merged, TAIWAN_KEYWORDS)):
                        check_failures.append({"person": full_name, "url": hit.url, "title": hit.title})

                created = 0
                updated = 0
                for hit in discovered:
                    payload = {
                        "person_id": person_id,
                        "participant_ids": [person_id],
                        "title": _clean_title(hit.title) or hit.url,
                        "source_title": _clean_title(hit.title) or hit.url,
                        "date_published": _parse_date(hit.published_date),
                        "source_url": hit.url,
                        "source_type": _source_type(hit.source),
                        "statement_type": "statement",
                        "excerpt": hit.excerpt,
                        "full_text": hit.excerpt,
                        "raw_text": hit.excerpt,
                        "is_primary_source": _is_primary(hit.source),
                        "parser_identity": "restricted_direct_site_search_v1",
                        "raw_payload": {
                            "seeded_from": "restricted_direct_site_search_v1",
                            "source_domain": hit.source,
                            "person_terms": person_terms,
                            "search_year": args.year,
                            "search_months": sorted(months),
                            "batch": "federal_senator_all_strict",
                        },
                    }
                    is_created = _ingest_payload_with_session_retry(payload)
                    if is_created:
                        created += 1
                    else:
                        updated += 1

                summary_rows.append(
                    {
                        "index": idx,
                        "person_id": person_id,
                        "person": full_name,
                        "office": office_title,
                        "discovered": len(discovered),
                        "created": created,
                        "updated": updated,
                    }
                )
                print(
                    json.dumps(
                        {"progress": idx, "person": full_name, "discovered": len(discovered), "created": created, "updated": updated},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    result = {
        "year": args.year,
        "months": sorted(months),
        "people": len(summary_rows),
        "total_discovered": sum(int(row["discovered"]) for row in summary_rows),
        "total_created": sum(int(row["created"]) for row in summary_rows),
        "total_updated": sum(int(row["updated"]) for row in summary_rows),
        "check_failures": len(check_failures),
        "source_errors": len(source_errors),
        "rows": summary_rows,
        "failed_items": check_failures,
        "source_error_items": source_errors,
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"status": "done", "output": args.output, **{k: result[k] for k in ("people", "total_discovered", "total_created", "total_updated", "check_failures", "source_errors")}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
