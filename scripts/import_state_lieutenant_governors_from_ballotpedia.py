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


SOURCE_URL = "https://ballotpedia.org/Lieutenant_Governor_(state_executive_office)"
USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"
PARSER_IDENTITY = "ballotpedia_state_lieutenant_governor_v1"

US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
    "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri",
    "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
}


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _extract_state_from_office_label(label: str) -> str | None:
    text = _clean_text(label)
    m = re.match(r"^Lieutenant Governor of (.+)$", text, flags=re.I)
    if m:
        candidate = _clean_text(m.group(1))
        return candidate if candidate in US_STATES else None
    m = re.match(r"^(.+?) Lieutenant Governor$", text, flags=re.I)
    if m:
        candidate = _clean_text(m.group(1))
        return candidate if candidate in US_STATES else None
    return None


def _extract_records(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    target_table = None
    for table in soup.select("table"):
        headers = [_clean_text(th.get_text(" ", strip=True)).lower() for th in table.select("tr th")[:8]]
        if {"office", "name", "party"}.issubset(set(headers)):
            target_table = table
            break
    if target_table is None:
        return []

    records: list[dict[str, str]] = []
    for row in target_table.select("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 3:
            continue
        office_label = _clean_text(cells[0].get_text(" ", strip=True))
        if "lieutenant governor" not in office_label.lower():
            continue
        state_name = _extract_state_from_office_label(office_label)
        if not state_name:
            continue

        person_name = _clean_text(cells[1].get_text(" ", strip=True))
        party = _clean_text(cells[2].get_text(" ", strip=True))
        if not person_name:
            continue

        office_link = cells[0].find("a", href=True)
        source_url = urljoin(SOURCE_URL, office_link["href"]) if office_link else SOURCE_URL

        records.append(
            {
                "state": state_name,
                "person_name": person_name,
                "party": party,
                "source_url": source_url,
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
                office_name="Lieutenant Governor",
                level="state",
                branch="executive",
                chamber=None,
                jurisdiction_id=state.id,
                source_url=item["source_url"],
                source_type="media",
            )
            payload = {
                "full_name": item["person_name"],
                "source_url": item["source_url"],
                "source_type": "media",
                "profile_status": "seeded",
                "canonical_official_url": item["source_url"],
                "parser_identity": PARSER_IDENTITY,
                "raw_payload": {"state": item["state"], "source": "ballotpedia"},
            }
            person, is_created = service.upsert_person(payload)
            if is_created:
                created += 1
            else:
                updated += 1
            service.upsert_appointment(
                person=person,
                office=office,
                jurisdiction_id=state.id,
                payload={
                    "role_title": "Lieutenant Governor",
                    "party": item.get("party") or None,
                    "status": "current",
                    "source_url": item["source_url"],
                    "source_type": "media",
                    "parser_identity": PARSER_IDENTITY,
                    "is_current": True,
                    "raw_payload": {"state": item["state"], "source": "ballotpedia"},
                },
            )
            service.ensure_alias(person.id, f"Lt. Gov. {item['person_name']}", item["source_url"], "media")

    return {
        "records_found": len(records),
        "records_created": created,
        "records_updated": updated,
        "sample": records[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import current state lieutenant governors from Ballotpedia.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    resp = httpx.get(SOURCE_URL, timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    records = _extract_records(resp.text)
    outcome = _upsert_records(records, dry_run=args.dry_run)
    result = {
        "status": "dry_run" if args.dry_run else "success",
        "source_url": SOURCE_URL,
        "started_at_utc": datetime.utcnow().isoformat(),
        **outcome,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
