from __future__ import annotations

import argparse
import json
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from tracker.db import session_scope
from tracker.services.officials_service import OfficialsService


NASACT_DIRECTORY_URL = "https://www.nasact.org/AF_MemberDirectory.asp"
NAST_TREASURER_URL = "https://nast.org/find-your-state-treasurer/"
USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"
PARSER_IDENTITY = "nasact_state_treasurer_v1"

STATE_CODE_TO_NAME = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _fetch_nast_state_links() -> dict[str, str]:
    response = httpx.get(
        NAST_TREASURER_URL,
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    links: dict[str, str] = {}
    valid_states = set(STATE_CODE_TO_NAME.values())
    for anchor in soup.select("a[href]"):
        state_name = _clean_text(anchor.get_text(" ", strip=True))
        if state_name not in valid_states:
            continue
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        links[state_name] = href
    return links


def _fetch_nasact_entries() -> list[dict[str, str]]:
    response = httpx.post(
        NASACT_DIRECTORY_URL,
        data={"Dlist": "3", "Page": "1", "Page2": "1", "keyword": ""},
        timeout=30.0,
        headers={"User-Agent": USER_AGENT, "Referer": "https://www.nasact.org/"},
        follow_redirects=True,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    rows: list[dict[str, str]] = []
    for cell in soup.select('td[width="328"]'):
        parts = [_clean_text(chunk) for chunk in cell.stripped_strings]
        if len(parts) < 2:
            continue

        first = parts[0]
        match = re.match(r"^([A-Z]{2})\s+(.+)$", first)
        if not match:
            continue
        state_code = match.group(1).strip()
        state_name = STATE_CODE_TO_NAME.get(state_code)
        if not state_name:
            continue

        person_name = parts[1]
        if not person_name or any(person_name.startswith(prefix) for prefix in ("P.O. Box", "Phone:", "Fax:", "Website:")):
            continue

        rows.append(
            {
                "state_code": state_code,
                "state": state_name,
                "organization": match.group(2).strip(),
                "person_name": person_name,
            }
        )

    deduped: dict[str, dict[str, str]] = {}
    for item in rows:
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
            official_site = item.get("official_site_url") or NAST_TREASURER_URL
            office = service.get_or_create_office(
                office_name="State Treasurer",
                level="state",
                branch="executive",
                chamber=None,
                jurisdiction_id=state.id,
                source_url=official_site,
                source_type="official",
            )
            person_payload = {
                "full_name": item["person_name"],
                "source_url": official_site,
                "source_type": "official",
                "profile_status": "officially_enriched",
                "canonical_official_url": official_site,
                "parser_identity": PARSER_IDENTITY,
                "raw_payload": {
                    "state": item["state"],
                    "state_code": item["state_code"],
                    "organization": item["organization"],
                    "source": "nasact_dlist3",
                    "state_site_source": NAST_TREASURER_URL,
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
                    "role_title": "State Treasurer",
                    "status": "current",
                    "source_url": official_site,
                    "source_type": "official",
                    "parser_identity": PARSER_IDENTITY,
                    "is_current": True,
                    "raw_payload": {
                        "state": item["state"],
                        "state_code": item["state_code"],
                        "organization": item["organization"],
                        "source": "nasact_dlist3",
                        "state_site_source": NAST_TREASURER_URL,
                    },
                },
            )
            service.ensure_alias(person.id, f"State Treasurer {item['person_name']}", official_site, "official")

    return {
        "records_found": len(records),
        "records_created": created,
        "records_updated": updated,
        "sample": records[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import U.S. state treasurers from NASACT Dlist=3 plus NAST official links.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    official_links = _fetch_nast_state_links()
    records = _fetch_nasact_entries()
    for item in records:
        item["official_site_url"] = official_links.get(item["state"], "")

    outcome = _upsert_records(records, dry_run=args.dry_run)
    result = {
        "status": "dry_run" if args.dry_run else "success",
        "source_url": NASACT_DIRECTORY_URL,
        "started_at_utc": datetime.utcnow().isoformat(),
        "records_with_state_official_site": sum(1 for item in records if item.get("official_site_url")),
        **outcome,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
