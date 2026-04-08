from __future__ import annotations

import argparse
import json
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from tracker.db import session_scope
from tracker.services.officials_service import OfficialsService


DIRECTORY_URL = "https://www.nasact.org/AF_MemberDirectory.asp"
USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"
PARSER_IDENTITY = "nasact_auditor_comptroller_v1"

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

LEGISLATIVE_MARKERS = (
    "legislative",
    "joint legislative",
    "division of legislative audit",
    "legislative audit",
    "post audit",
    "budget assistant",
)


def _fetch_directory_entries(directory_id: int) -> list[dict[str, str]]:
    response = httpx.post(
        DIRECTORY_URL,
        data={"Dlist": str(directory_id), "Page": "1", "Page2": "1", "keyword": ""},
        timeout=30.0,
        headers={"User-Agent": USER_AGENT, "Referer": "https://www.nasact.org/"},
        follow_redirects=True,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    entries: list[dict[str, str]] = []
    for cell in soup.select('td[width="328"]'):
        parts = [re.sub(r"\s+", " ", chunk).strip() for chunk in cell.stripped_strings]
        if len(parts) < 2:
            continue
        first = parts[0]
        match = re.match(r"^([A-Z]{2})\s+(.+)$", first)
        if not match:
            continue
        state_code = match.group(1).strip()
        organization = match.group(2).strip()
        if state_code not in STATE_CODE_TO_NAME:
            continue
        person_name = parts[1].strip()
        if any(person_name.startswith(prefix) for prefix in ("P.O. Box", "Phone:", "Fax:", "Website:")):
            continue
        entries.append(
            {
                "state_code": state_code,
                "state_name": STATE_CODE_TO_NAME[state_code],
                "organization": organization,
                "person_name": person_name,
                "directory_id": str(directory_id),
            }
        )
    return entries


def _looks_executive_org(org: str) -> bool:
    lowered = (org or "").lower()
    if any(marker in lowered for marker in LEGISLATIVE_MARKERS):
        return False
    return any(
        token in lowered
        for token in (
            "comptroller",
            "controller",
            "office of accounts",
            "department of accounts",
            "auditor",
        )
    )


def _role_from_org(org: str) -> tuple[str, str]:
    lowered = (org or "").lower()
    if "comptroller" in lowered:
        return ("State Comptroller", "State Comptroller")
    if "controller" in lowered:
        return ("State Controller", "State Controller")
    if "auditor" in lowered:
        return ("State Auditor", "State Auditor")
    return ("State Comptroller", "State Comptroller")


def _dedupe_best(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    # Prefer Comptroller/Controller keywords over generic finance/accounting orgs.
    rank_tokens = [
        "comptroller",
        "controller",
        "state auditor",
        "auditor",
        "accounts",
    ]
    by_state: dict[str, dict[str, str]] = {}
    for item in entries:
        state = item["state_name"]
        org = item["organization"].lower()
        rank = 99
        for idx, token in enumerate(rank_tokens):
            if token in org:
                rank = idx
                break
        current = by_state.get(state)
        if current is None:
            by_state[state] = {**item, "_rank": str(rank)}
            continue
        if rank < int(current["_rank"]):
            by_state[state] = {**item, "_rank": str(rank)}
    return [{k: v for k, v in row.items() if k != "_rank"} for row in by_state.values()]


def _upsert(entries: list[dict[str, str]], dry_run: bool) -> dict[str, object]:
    if dry_run:
        return {
            "records_found": len(entries),
            "records_created": 0,
            "records_updated": 0,
            "sample": entries[:25],
        }

    created = 0
    updated = 0
    with session_scope() as session:
        service = OfficialsService(session)
        usa = service.get_or_create_jurisdiction("United States", "country", code="US")
        for item in entries:
            office_name, role_title = _role_from_org(item["organization"])
            state = service.get_or_create_jurisdiction(item["state_name"], "state", code=item["state_name"], parent_id=usa.id)
            office = service.get_or_create_office(
                office_name=office_name,
                level="state",
                branch="executive",
                chamber=None,
                jurisdiction_id=state.id,
                source_url="https://www.nasact.org/af_memberdirectory.asp",
                source_type="official",
            )
            person_payload = {
                "full_name": item["person_name"],
                "source_url": "https://www.nasact.org/af_memberdirectory.asp",
                "source_type": "official",
                "profile_status": "officially_enriched",
                "canonical_official_url": "https://www.nasact.org/af_memberdirectory.asp",
                "parser_identity": PARSER_IDENTITY,
                "raw_payload": {
                    "source": "nasact",
                    "organization": item["organization"],
                    "state_code": item["state_code"],
                    "directory_id": item["directory_id"],
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
                    "role_title": role_title,
                    "status": "current",
                    "source_url": "https://www.nasact.org/af_memberdirectory.asp",
                    "source_type": "official",
                    "parser_identity": PARSER_IDENTITY,
                    "is_current": True,
                    "raw_payload": {
                        "source": "nasact",
                        "organization": item["organization"],
                        "state_code": item["state_code"],
                        "directory_id": item["directory_id"],
                    },
                },
            )
            service.ensure_alias(person.id, f"{role_title} {item['person_name']}", "https://www.nasact.org/af_memberdirectory.asp", "official")
    return {
        "records_found": len(entries),
        "records_created": created,
        "records_updated": updated,
        "sample": entries[:25],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import executive-state auditor/comptroller records from NASACT directory.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    entries = _fetch_directory_entries(1) + _fetch_directory_entries(2)
    filtered = [item for item in entries if _looks_executive_org(item["organization"])]
    deduped = _dedupe_best(filtered)
    outcome = _upsert(deduped, dry_run=args.dry_run)

    result = {
        "status": "dry_run" if args.dry_run else "success",
        "started_at_utc": datetime.utcnow().isoformat(),
        "records_raw": len(entries),
        "records_filtered": len(filtered),
        **outcome,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
