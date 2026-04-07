from __future__ import annotations

from datetime import datetime

from tracker.db import session_scope
from tracker.models import SyncRun
from tracker.services.current_taiwan_collection_service import CurrentTaiwanCollectionService


def run_bootstrap_existing_people_taiwan_2025_2026(limit: int | None = None) -> dict:
    years = [2025, 2026]
    with session_scope() as session:
        service = CurrentTaiwanCollectionService(session)
        sync_run = SyncRun(
            job_name="bootstrap_existing_people_taiwan_2025_2026",
            job_type="tracker_bootstrap",
            source_name="existing_public_people",
        )
        session.add(sync_run)
        session.flush()
        result = service.bootstrap_existing_people_trackers_for_years(years=years, limit=limit)
        sync_run.ended_at = datetime.utcnow()
        sync_run.status = "success"
        sync_run.records_found = result.people_scanned
        sync_run.records_created = result.trackers_created
        sync_run.records_updated = result.trackers_updated
        sync_run.meta = {
            "targets_added": result.targets_added,
            "years": years,
            "news_queries_per_person_per_year": 3,
            "limit": limit,
        }
        return {
            "status": "success",
            "job_name": "bootstrap_existing_people_taiwan_2025_2026",
            "people_scanned": result.people_scanned,
            "records_created": result.trackers_created,
            "records_updated": result.trackers_updated,
            "targets_added": result.targets_added,
            "metadata": {
                "years": years,
                "news_queries_per_person_per_year": 3,
                "limit": limit,
            },
        }

