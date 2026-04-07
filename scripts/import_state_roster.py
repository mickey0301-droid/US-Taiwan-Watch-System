from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tracker.db import session_scope
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a state roster JSON into the database.")
    parser.add_argument("--input", required=True, help="Roster JSON from extract_state_roster_wikipedia.")
    parser.add_argument("--state-code", required=True, help="State code slug (e.g., ma, az).")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = Path.cwd() / input_path
    data = json.loads(input_path.read_text(encoding="utf-8"))
    parser_identity = f"wikipedia_state_roster_{args.state_code.lower()}_v1"

    state_name = data.get("state") or "Unknown"
    senate_url = data.get("senate_url") or ""
    house_url = data.get("house_url") or ""

    with session_scope() as session:
        service = OfficialsService(session)
        usa = service.get_or_create_jurisdiction("United States", "country", code="US")
        state = service.get_or_create_jurisdiction(state_name, "state", code=state_name, parent_id=usa.id)

        import_officials(service, state, data.get("officials", []), parser_identity)
        import_legislators(service, state, data.get("senate_members", []), "senate", senate_url, "Senator", parser_identity)
        import_legislators(service, state, data.get("house_members", []), "house", house_url, "Representative", parser_identity)

    print(
        f"Imported {state_name} roster "
        f"(officials={len(data.get('officials', []))}, "
        f"senate={len(data.get('senate_members', []))}, "
        f"house={len(data.get('house_members', []))})"
    )


def import_officials(
    service: OfficialsService,
    state: Any,
    officials: list[dict[str, Any]],
    parser_identity: str,
) -> None:
    for item in officials:
        name = item.get("incumbent")
        if not name:
            continue
        office_label = item.get("office_label") or "State official"
        source_url = item.get("office_url") or ""
        try:
            person, created = service.upsert_person(
                {
                    "full_name": name,
                    "source_url": item.get("incumbent_url") or source_url,
                    "source_type": "wikipedia",
                    "seed_source_type": "wikipedia",
                    "profile_status": "seeded",
                    "parser_identity": parser_identity,
                    "verification_status": "unverified",
                    "raw_payload": item,
                }
            )
        except InvalidPersonNameError:
            continue

        service.ensure_alias(person.id, name, item.get("incumbent_url") or source_url, "wikipedia")
        office = service.get_or_create_office(
            office_name=office_label,
            level="state",
            branch="executive",
            chamber=None,
            jurisdiction_id=state.id,
            source_url=source_url,
            source_type="wikipedia",
        )
        service.upsert_appointment(
            person,
            office,
            state.id,
            {
                "role_title": office_label,
                "status": "current",
                "source_url": source_url,
                "source_type": "wikipedia",
                "parser_identity": parser_identity,
                "is_current": True,
                "raw_payload": item,
            },
        )


def import_legislators(
    service: OfficialsService,
    state: Any,
    members: list[dict[str, Any]],
    chamber: str,
    source_url: str,
    role_title: str,
    parser_identity: str,
) -> None:
    office = service.get_or_create_office(
        office_name=f"{state.name} {role_title}",
        level="state",
        branch="legislative",
        chamber=chamber,
        jurisdiction_id=state.id,
        source_url=source_url,
        source_type="wikipedia",
    )
    for item in members:
        name = item.get("name")
        if not name:
            continue
        member_source_url = item.get("wikipedia_url") or item.get("source_url") or source_url
        member_source_type = item.get("source_type") or "wikipedia"
        try:
            person, created = service.upsert_person(
                {
                    "full_name": name,
                    "source_url": member_source_url,
                    "source_type": member_source_type,
                    "seed_source_type": "wikipedia",
                    "profile_status": "seeded",
                    "parser_identity": parser_identity,
                    "verification_status": "unverified",
                    "raw_payload": item,
                }
            )
        except InvalidPersonNameError:
            continue
        service.ensure_alias(person.id, name, member_source_url, member_source_type)
        service.upsert_appointment(
            person,
            office,
            state.id,
            {
                "role_title": role_title,
                "district": item.get("district"),
                "status": "current",
                "source_url": member_source_url,
                "source_type": member_source_type,
                "parser_identity": parser_identity,
                "is_current": True,
                "raw_payload": item,
            },
        )


if __name__ == "__main__":
    main()
