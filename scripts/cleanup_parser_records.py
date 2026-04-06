from __future__ import annotations

import argparse

from sqlalchemy import delete, func, select

from tracker.db import session_scope
from tracker.models import Alias, Appointment, Office, Person, RosterMembership, Statement, Tracker


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete records produced by a specific parser identity.")
    parser.add_argument("parser_identity")
    args = parser.parse_args()

    with session_scope() as session:
        appointment_rows = session.execute(
            select(Appointment.id, Appointment.person_id, Appointment.office_id).where(
                Appointment.parser_identity == args.parser_identity
            )
        ).all()
        membership_rows = session.execute(
            select(RosterMembership.id, RosterMembership.person_id, RosterMembership.office_id).where(
                RosterMembership.parser_identity == args.parser_identity
            )
        ).all()

        appointment_ids = [row.id for row in appointment_rows]
        membership_ids = [row.id for row in membership_rows]
        affected_person_ids = sorted({row.person_id for row in appointment_rows + membership_rows if row.person_id is not None})
        affected_office_ids = sorted({row.office_id for row in appointment_rows + membership_rows if row.office_id is not None})

        if membership_ids:
            session.execute(delete(RosterMembership).where(RosterMembership.id.in_(membership_ids)))
        if appointment_ids:
            session.execute(delete(Appointment).where(Appointment.id.in_(appointment_ids)))

        deleted_people = 0
        if affected_person_ids:
            for person_id in affected_person_ids:
                remaining_appointments = session.scalar(
                    select(func.count()).select_from(Appointment).where(Appointment.person_id == person_id)
                ) or 0
                remaining_trackers = session.scalar(
                    select(func.count()).select_from(Tracker).where(Tracker.person_id == person_id)
                ) or 0
                remaining_statements = session.scalar(
                    select(func.count()).select_from(Statement).where(Statement.person_id == person_id)
                ) or 0
                if remaining_appointments or remaining_trackers or remaining_statements:
                    continue
                session.execute(delete(Alias).where(Alias.person_id == person_id))
                session.execute(delete(Person).where(Person.id == person_id))
                deleted_people += 1

        deleted_offices = 0
        if affected_office_ids:
            for office_id in affected_office_ids:
                remaining_appointments = session.scalar(
                    select(func.count()).select_from(Appointment).where(Appointment.office_id == office_id)
                ) or 0
                remaining_memberships = session.scalar(
                    select(func.count()).select_from(RosterMembership).where(RosterMembership.office_id == office_id)
                ) or 0
                if remaining_appointments or remaining_memberships:
                    continue
                session.execute(delete(Office).where(Office.id == office_id))
                deleted_offices += 1

        print(
            {
                "parser_identity": args.parser_identity,
                "appointments_deleted": len(appointment_ids),
                "memberships_deleted": len(membership_ids),
                "people_deleted": deleted_people,
                "offices_deleted": deleted_offices,
            }
        )


if __name__ == "__main__":
    main()
