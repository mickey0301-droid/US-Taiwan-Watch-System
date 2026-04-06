from __future__ import annotations

from datetime import datetime
from urllib.parse import quote_plus

from sqlalchemy import select

from tracker.collectors.base import BaseCollector, CollectorRunResult
from tracker.db import session_scope
from tracker.models import Appointment, Person, SyncRun


class GovTrackEnrichmentCollector(BaseCollector):
    collector_name = "govtrack_enrichment"
    source_name = "GovTrack secondary enrichment"
    source_url = "https://www.govtrack.us/"

    def fetch(self) -> list[Person]:
        with session_scope() as session:
            stmt = (
                select(Person)
                .join(Appointment, Appointment.person_id == Person.id)
                .where(Appointment.role_title.in_(["Senator", "Representative"]))
                .distinct()
            )
            return session.execute(stmt).scalars().all()

    def parse(self, payload: list[Person]) -> list[Person]:
        return payload

    def sync(self) -> CollectorRunResult:
        result = CollectorRunResult(job_name=self.collector_name, source_name=self.source_name, started_at=datetime.utcnow())
        with session_scope() as session:
            sync_run = SyncRun(job_name=self.collector_name, job_type="collector", source_name=self.source_name)
            session.add(sync_run)
            session.flush()
            people = self.parse(
                session.execute(
                    select(Person)
                    .join(Appointment, Appointment.person_id == Person.id)
                    .where(Appointment.role_title.in_(["Senator", "Representative"]))
                    .distinct()
                ).scalars().all()
            )
            for person in people:
                raw = dict(person.raw_payload or {})
                secondary = dict(raw.get("secondary_sources") or {})
                secondary["govtrack_search_url"] = f"https://www.govtrack.us/congress/members?q={quote_plus(person.full_name)}"
                raw["secondary_sources"] = secondary
                person.raw_payload = raw
                result.records_found += 1
                result.records_updated += 1
            sync_run.status = "success"
            result.ended_at = datetime.utcnow()
            sync_run.started_at = result.started_at
            sync_run.ended_at = result.ended_at
            sync_run.records_found = result.records_found
            sync_run.records_updated = result.records_updated
        return result
