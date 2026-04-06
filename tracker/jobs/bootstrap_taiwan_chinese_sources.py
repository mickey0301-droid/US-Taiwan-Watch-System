from __future__ import annotations

from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import Alias, Appointment, Office, Person, Tracker, TrackerTarget
from tracker.utils.taiwan_sources import build_taiwan_source_targets


def run_bootstrap_taiwan_chinese_sources(limit: int = 150) -> dict:
    people_scanned = 0
    trackers_created = 0
    targets_added = 0

    with session_scope() as session:
        people = (
            session.execute(
                select(Person)
                .join(Appointment, Appointment.person_id == Person.id)
                .join(Office, Office.id == Appointment.office_id)
                .where(Office.level == "federal", Appointment.status == "current")
                .order_by(Person.updated_at.desc())
                .limit(limit)
            )
            .scalars()
            .unique()
            .all()
        )

        for person in people:
            people_scanned += 1
            chinese_alias = session.execute(
                select(Alias.alias)
                .where(Alias.person_id == person.id, Alias.alias_type == "chinese_name")
                .order_by(Alias.updated_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            tracker = session.execute(
                select(Tracker).where(Tracker.person_id == person.id).order_by(Tracker.updated_at.desc())
            ).scalars().first()
            if not tracker:
                tracker = Tracker(
                    person_id=person.id,
                    name="Taiwan source tracker",
                    status="active",
                    include_primary_sources=True,
                    include_media_reports=True,
                )
                session.add(tracker)
                session.flush()
                trackers_created += 1

            existing = {
                (item.target_type, item.target_url)
                for item in session.execute(select(TrackerTarget).where(TrackerTarget.tracker_id == tracker.id)).scalars().all()
            }
            for target in build_taiwan_source_targets(person.full_name, chinese_alias):
                key = (target["target_type"], target["target_url"])
                if key in existing:
                    continue
                session.add(
                    TrackerTarget(
                        tracker_id=tracker.id,
                        target_name=target["target_name"],
                        target_type=target["target_type"],
                        target_url=target["target_url"],
                        parser_identity=target["parser_identity"],
                        is_active=True,
                    )
                )
                targets_added += 1

    return {
        "status": "success",
        "job_name": "bootstrap_taiwan_chinese_sources",
        "people_scanned": people_scanned,
        "records_created": trackers_created,
        "targets_added": targets_added,
        "limit": limit,
        "scope": "current_federal_people",
    }
