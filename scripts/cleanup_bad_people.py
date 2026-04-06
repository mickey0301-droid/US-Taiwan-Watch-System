from __future__ import annotations

import re

from sqlalchemy import delete, select

from tracker.db import session_scope
from tracker.models import Alias, Appointment, NotificationLog, Person, Statement, StatementMention, StatementSource, Tracker, TrackerTarget


BAD_NAME_PATTERNS = [
    re.compile(r"^[A-Z][a-z]+ \d+(st|nd|rd|th)$"),
    re.compile(r"^[A-Z][a-z]+ [A-Z][a-z]+ \d+(st|nd|rd|th)$"),
]


def looks_like_bad_person_name(name: str) -> bool:
    normalized = " ".join(name.split())
    return any(pattern.match(normalized) for pattern in BAD_NAME_PATTERNS)


def main() -> None:
    with session_scope() as session:
        people = session.execute(select(Person).order_by(Person.id.asc())).scalars().all()
        bad_people = [person for person in people if looks_like_bad_person_name(person.full_name)]
        bad_ids = [person.id for person in bad_people]
        if not bad_ids:
            print("No bad people found.")
            return

        statement_ids = session.execute(select(Statement.id).where(Statement.person_id.in_(bad_ids))).scalars().all()
        tracker_ids = session.execute(select(Tracker.id).where(Tracker.person_id.in_(bad_ids))).scalars().all()

        if statement_ids:
            session.execute(delete(StatementMention).where(StatementMention.statement_id.in_(statement_ids)))
            session.execute(delete(StatementSource).where(StatementSource.statement_id.in_(statement_ids)))
        if tracker_ids:
            session.execute(delete(TrackerTarget).where(TrackerTarget.tracker_id.in_(tracker_ids)))

        session.execute(delete(Alias).where(Alias.person_id.in_(bad_ids)))
        session.execute(delete(Appointment).where(Appointment.person_id.in_(bad_ids)))
        session.execute(delete(Tracker).where(Tracker.person_id.in_(bad_ids)))
        session.execute(delete(Statement).where(Statement.person_id.in_(bad_ids)))
        session.execute(delete(NotificationLog).where(NotificationLog.target_identifier.in_([str(person_id) for person_id in bad_ids])))
        session.execute(delete(Person).where(Person.id.in_(bad_ids)))
        print(f"Deleted {len(bad_ids)} bad people.")
        for person in bad_people[:20]:
            print(f"- {person.id}: {person.full_name}")


if __name__ == "__main__":
    main()
