from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from tracker.collectors.base import BaseCollector, CollectorRunResult
from tracker.db import session_scope
from tracker.logging_utils import get_logger
from tracker.models import Person, SyncRun
from tracker.services.officials_service import OfficialsService
from tracker.services.profile_enrichment_service import ProfileEnrichmentService


logger = get_logger(__name__)


class ProfileBackgroundEnrichmentCollector(BaseCollector):
    collector_name = "profile_background_enrichment"
    source_name = "Congress.gov / GovTrack / Wikipedia profile enrichment"

    def fetch(self) -> list[Person]:
        with session_scope() as session:
            return session.execute(select(Person).order_by(Person.updated_at.desc())).scalars().all()

    def parse(self, payload: list[Person]) -> list[Person]:
        return payload

    def sync(self) -> CollectorRunResult:
        result = CollectorRunResult(job_name=self.collector_name, source_name=self.source_name, started_at=datetime.utcnow())
        with session_scope() as session:
            sync_run = SyncRun(job_name=self.collector_name, job_type="collector", source_name=self.source_name)
            session.add(sync_run)
            session.flush()
            try:
                service = ProfileEnrichmentService(OfficialsService(session))
                people = session.execute(select(Person).order_by(Person.updated_at.desc())).scalars().all()
                result.records_found = len(people)
                for person in people:
                    enrichment = service.enrich_person(person)
                    if enrichment.updated_fields:
                        result.records_updated += 1
                    if enrichment.errors:
                        result.metadata.setdefault("errors_by_person", {})[str(person.id)] = enrichment.errors
                sync_run.status = "success"
            except Exception as exc:
                logger.exception("Profile background enrichment failed.")
                result.errors.append(str(exc))
                sync_run.status = "failed"
                sync_run.error_message = str(exc)
            finally:
                result.ended_at = datetime.utcnow()
                sync_run.started_at = result.started_at
                sync_run.ended_at = result.ended_at
                sync_run.records_found = result.records_found
                sync_run.records_updated = result.records_updated
                sync_run.meta = {"errors": result.errors, **result.metadata}
        return result
