from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from tracker.db import session_scope
from tracker.services.officials_service import OfficialsService


NAAG_FIND_MY_AG_URL = "https://www.naag.org/find-my-ag/"
USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"
PARSER_IDENTITY = "naag_state_attorneys_general_v1"

US_STATE_NAMES = {
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
}


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _clean_name(value: str) -> str:
    cleaned = _clean_text(value)
    cleaned = re.sub(r"^(Hon\.?|The Honorable)\s+", "", cleaned, flags=re.I)
    return cleaned


def _extract_records(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict[str, str]] = []

    for card in soup.select("div.fl-post-column"):
        name_link = card.select_one('h2.fl-post-title a[href*="/attorney-general/"]')
        state_node = card.select_one("div.fl-post-meta")
        if not name_link or not state_node:
            continue

        state_name = _clean_text(state_node.get_text(" ", strip=True))
        if state_name not in US_STATE_NAMES:
            continue

        person_name = _clean_name(name_link.get_text(" ", strip=True))
        profile_url = urljoin(NAAG_FIND_MY_AG_URL, (name_link.get("href") or "").strip())
        if not person_name or not profile_url:
            continue

        records.append(
            {
                "state": state_name,
                "person_name": person_name,
                "profile_url": profile_url,
                "source_url": NAAG_FIND_MY_AG_URL,
            }
        )

    deduped: dict[str, dict[str, str]] = {}
    for item in records:
        deduped.setdefault(item["state"], item)
    return list(deduped.values())


def _upsert_records(records: list[dict[str, str]], dry_run: bool) -> dict[str, object]:
    if dry_run:
        return {
            "records_found": len(records),
            "records_created": 0,
            "records_updated": 0,
            "sample": records[:20],
        }

    created = 0
    updated = 0
    with session_scope() as session:
        service = OfficialsService(session)
        usa = service.get_or_create_jurisdiction("United States", "country", code="US")
        for item in records:
            state = service.get_or_create_jurisdiction(item["state"], "state", code=item["state"], parent_id=usa.id)
            office = service.get_or_create_office(
                office_name="Attorney General",
                level="state",
                branch="executive",
                chamber=None,
                jurisdiction_id=state.id,
                source_url=NAAG_FIND_MY_AG_URL,
                source_type="official",
            )
            person_payload = {
                "full_name": item["person_name"],
                "source_url": item["profile_url"],
                "source_type": "official",
                "profile_status": "officially_enriched",
                "canonical_official_url": item["profile_url"],
                "parser_identity": PARSER_IDENTITY,
                "raw_payload": {
                    "state": item["state"],
                    "source": "naag",
                    "listing_url": NAAG_FIND_MY_AG_URL,
                },
            }
            person, is_created = service.upsert_person(person_payload)
            if is_created:
                created += 1
            else:
                updated += 1
            service.upsert_appointment(
                person=person,
                office=office,
                jurisdiction_id=state.id,
                payload={
                    "role_title": "Attorney General",
                    "status": "current",
                    "source_url": item["profile_url"],
                    "source_type": "official",
                    "parser_identity": PARSER_IDENTITY,
                    "is_current": True,
                    "raw_payload": {
                        "state": item["state"],
                        "source": "naag",
                        "listing_url": NAAG_FIND_MY_AG_URL,
                    },
                },
            )
            service.ensure_alias(person.id, f"Attorney General {item['person_name']}", item["profile_url"], "official")

    return {
        "records_found": len(records),
        "records_created": created,
        "records_updated": updated,
        "sample": records[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import current U.S. state attorneys general from NAAG.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    resp = httpx.get(
        NAAG_FIND_MY_AG_URL,
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    records = _extract_records(resp.text)
    outcome = _upsert_records(records, dry_run=args.dry_run)
    result = {
        "status": "dry_run" if args.dry_run else "success",
        "source_url": NAAG_FIND_MY_AG_URL,
        "started_at_utc": datetime.utcnow().isoformat(),
        **outcome,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
