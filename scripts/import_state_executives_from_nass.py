from __future__ import annotations

import argparse
import json
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup, Tag

from tracker.db import session_scope
from tracker.services.officials_service import OfficialsService


NASS_URL = "https://www.nass.org/memberships/secretaries-statelieutenant-governors"
USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"
PARSER_IDENTITY = "nass_state_executives_v1"

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


def _clean_state_heading(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    cleaned = re.sub(r"\s*\(.*?\)\s*$", "", cleaned).strip()
    cleaned = cleaned.replace("*", "").strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned


def _clean_person_name(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    cleaned = re.sub(r"\s*\([A-Z]+\)\s*$", "", cleaned).strip()
    cleaned = re.sub(r"^(Hon\.?|The Honorable)\s+", "", cleaned, flags=re.I).strip()
    return cleaned


def _office_from_line(office_line: str) -> tuple[str, str] | None:
    line = (office_line or "").strip().lower()
    if "lt. governor" in line or "lieutenant governor" in line:
        return ("Lieutenant Governor", "Lieutenant Governor")
    if "secretary of state" in line or "secretary of the commonwealth" in line:
        return ("Secretary of State", "Secretary of State")
    return None


def _extract_records(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict[str, str]] = []

    for heading in soup.find_all("h2"):
        state_name = _clean_state_heading(heading.get_text(" ", strip=True))
        if state_name not in US_STATE_NAMES:
            continue

        p1 = heading.find_next_sibling("p")
        p2 = p1.find_next_sibling("p") if p1 else None
        if not p1 or not p2:
            continue
        if not isinstance(p1, Tag) or not isinstance(p2, Tag):
            continue

        person_name = _clean_person_name(p1.get_text(" ", strip=True))
        office_line = p2.get_text(" ", strip=True)
        office_role = _office_from_line(office_line)
        if not person_name or not office_role:
            continue
        office_name, role_title = office_role
        records.append(
            {
                "state": state_name,
                "person_name": person_name,
                "office_name": office_name,
                "role_title": role_title,
            }
        )
    return records


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
                office_name=item["office_name"],
                level="state",
                branch="executive",
                chamber=None,
                jurisdiction_id=state.id,
                source_url=NASS_URL,
                source_type="official",
            )
            person_payload = {
                "full_name": item["person_name"],
                "source_url": NASS_URL,
                "source_type": "official",
                "profile_status": "officially_enriched",
                "canonical_official_url": NASS_URL,
                "parser_identity": PARSER_IDENTITY,
                "raw_payload": {"state": item["state"], "source": "nass"},
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
                    "role_title": item["role_title"],
                    "status": "current",
                    "source_url": NASS_URL,
                    "source_type": "official",
                    "parser_identity": PARSER_IDENTITY,
                    "is_current": True,
                    "raw_payload": {"state": item["state"], "source": "nass"},
                },
            )
            if item["role_title"] == "Lieutenant Governor":
                service.ensure_alias(person.id, f"Lt. Gov. {item['person_name']}", NASS_URL, "official")
            if item["role_title"] == "Secretary of State":
                service.ensure_alias(person.id, f"Secretary {item['person_name']}", NASS_URL, "official")
    return {
        "records_found": len(records),
        "records_created": created,
        "records_updated": updated,
        "sample": records[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import state executive officials from NASS roster page.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    resp = httpx.get(
        NASS_URL,
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    records = _extract_records(resp.text)
    outcome = _upsert_records(records, dry_run=args.dry_run)
    result = {
        "status": "dry_run" if args.dry_run else "success",
        "source_url": NASS_URL,
        "started_at_utc": datetime.utcnow().isoformat(),
        **outcome,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
