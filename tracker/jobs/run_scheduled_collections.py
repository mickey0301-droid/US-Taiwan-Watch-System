from __future__ import annotations

from tracker.db import session_scope
from tracker.services.scheduled_collection_service import ScheduledCollectionService


def run_scheduled_collections() -> dict:
    with session_scope() as session:
        results = ScheduledCollectionService(session).run_due_schedules()
        return {
            "status": "success",
            "job_name": "run_scheduled_collections",
            "records_found": len(results),
            "results": results,
        }
