from __future__ import annotations

import argparse
import json
from datetime import datetime

from tracker.db import session_scope
from tracker.services.officials_service import OfficialsService
from tracker.services.statements_service import StatementsService

from scripts.discover_restricted_source_events import EventHit, dedupe_hits, discover_cna, discover_mofa, discover_president, _month_bounds

import httpx


USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
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
    parser = argparse.ArgumentParser(description="Discover and import restricted source events (CNA/MOFA/President).")
    parser.add_argument("--person", required=True, help="Primary person name, e.g. Donald Trump")
    parser.add_argument("--aliases", default="川普,Trump,特朗普,美國總統川普", help="Comma-separated aliases")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--months", default="4,3,2,1", help="Comma-separated months")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    months = [int(item.strip()) for item in str(args.months).split(",") if item.strip()]
    months = [month for month in months if 1 <= month <= 12]
    if not months:
        raise SystemExit("No valid months.")

    person_terms = [args.person.strip()] + [item.strip() for item in str(args.aliases).split(",") if item.strip()]
    person_terms = list(dict.fromkeys(person_terms))
    start, end = _month_bounds(args.year, months)

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        with httpx.Client(headers=headers, follow_redirects=True, verify=False) as insecure_client:
            discovered = dedupe_hits(
                discover_cna(client, insecure_client, person_terms=person_terms, start=start, end=end)
                + discover_mofa(client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=args.max_pages)
                + discover_president(client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=args.max_pages)
            )

    with session_scope() as session:
        officials_service = OfficialsService(session)
        statements_service = StatementsService(session)
        person = officials_service.find_person(args.person)
        if not person:
            person, _ = officials_service.upsert_person(
                {
                    "full_name": args.person,
                    "source_url": "https://www.whitehouse.gov/",
                    "source_type": "official",
                    "profile_status": "seeded",
                    "verification_status": "unverified",
                    "raw_payload": {
                        "manual_seed": True,
                        "seed_context": "restricted_source_import",
                    },
                }
            )

        created = 0
        updated = 0
        imported_items: list[dict[str, object]] = []

        for hit in discovered:
            payload = {
                "person_id": person.id,
                "participant_ids": [person.id],
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
                },
            }
            if args.dry_run:
                imported_items.append(
                    {
                        "source": hit.source,
                        "url": hit.url,
                        "title": payload["title"],
                        "published_date": hit.published_date,
                    }
                )
                continue
            _, is_created = statements_service.ingest_statement(payload)
            if is_created:
                created += 1
            else:
                updated += 1

        result = {
            "status": "dry_run" if args.dry_run else "success",
            "person": args.person,
            "person_id": person.id,
            "year": args.year,
            "months": sorted(months),
            "discovered": len(discovered),
            "created": created,
            "updated": updated,
            "items": imported_items if args.dry_run else [],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
