from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import func, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tracker.collectors.state_executive_official_pages import StateExecutiveOfficialPagesCollector
from tracker.collectors.state_executive_wikipedia import StateExecutiveWikipediaCollector
from tracker.collectors.state_legislatures import StateLegislaturesCollector
from tracker.config import get_source_registry
from tracker.db import session_scope
from tracker.models import Appointment, Jurisdiction, Person


def _state_choices() -> list[str]:
    registry = get_source_registry()
    names = {
        source.get("state")
        for source in registry.get("state_legislature_sources", [])
        if source.get("state")
    }
    names.update(
        source.get("state")
        for source in registry.get("state_executive_official_sources", [])
        if source.get("state")
    )
    names.update(
        {
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
    )
    return sorted(names)


def _state_counts(state_name: str) -> dict[str, int]:
    with session_scope() as session:
        jurisdiction = session.execute(
            select(Jurisdiction).where(
                Jurisdiction.name == state_name,
                Jurisdiction.type == "state",
            )
        ).scalar_one_or_none()
        if not jurisdiction:
            return {"people": 0, "current_appointments": 0}
        people_count = session.execute(
            select(func.count(func.distinct(Person.id)))
            .select_from(Person)
            .join(Appointment, Appointment.person_id == Person.id)
            .where(Appointment.jurisdiction_id == jurisdiction.id)
        ).scalar_one()
        appointment_count = session.execute(
            select(func.count(Appointment.id)).where(
                Appointment.jurisdiction_id == jurisdiction.id,
                Appointment.is_current.is_(True),
            )
        ).scalar_one()
        return {
            "people": int(people_count or 0),
            "current_appointments": int(appointment_count or 0),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync people data for a single U.S. state.")
    parser.add_argument("state", choices=_state_choices(), help="State name to sync.")
    parser.add_argument("--skip-legislature", action="store_true", help="Skip legislature roster sync.")
    parser.add_argument("--skip-executive-wikipedia", action="store_true", help="Skip statewide elected officials sync from Wikipedia.")
    parser.add_argument("--skip-executive-official-pages", action="store_true", help="Skip executive official page sync.")
    args = parser.parse_args()

    before_counts = _state_counts(args.state)
    runs: list[dict[str, object]] = []

    if not args.skip_legislature:
        result = StateLegislaturesCollector(state_filter=args.state).sync()
        runs.append({"collector": result.job_name, "result": result.__dict__})
    if not args.skip_executive_wikipedia:
        result = StateExecutiveWikipediaCollector(state_filter=args.state).sync()
        runs.append({"collector": result.job_name, "result": result.__dict__})
    if not args.skip_executive_official_pages:
        result = StateExecutiveOfficialPagesCollector(state_filter=args.state).sync()
        runs.append({"collector": result.job_name, "result": result.__dict__})

    after_counts = _state_counts(args.state)
    print(
        json.dumps(
            {
                "state": args.state,
                "before": before_counts,
                "after": after_counts,
                "runs": runs,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
