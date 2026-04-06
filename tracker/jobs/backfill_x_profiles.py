from __future__ import annotations

from datetime import datetime

from tracker.db import session_scope
from tracker.models import SyncRun
from tracker.services.social_profile_backfill_service import SocialProfileBackfillService


def run_backfill_x_profiles(limit: int | None = None) -> dict:
    with session_scope() as session:
        service = SocialProfileBackfillService(session)
        sync_run = SyncRun(
            job_name="backfill_x_profiles",
            job_type="profile_enrichment",
            source_name="official_and_wikipedia_social",
        )
        session.add(sync_run)
        session.flush()

        result = service.backfill_x_profiles(limit=limit)
        sync_run.ended_at = datetime.utcnow()
        sync_run.status = "partial_failure" if result.errors else "success"
        sync_run.records_found = result.people_scanned
        sync_run.records_created = result.x_profiles_added
        sync_run.records_updated = result.people_updated
        sync_run.meta = {
            "social_targets_added": result.social_targets_added,
            "errors": result.errors,
            "limit": limit,
        }
        return {
            "status": sync_run.status,
            "job_name": "backfill_x_profiles",
            "people_scanned": result.people_scanned,
            "records_created": result.x_profiles_added,
            "records_updated": result.people_updated,
            "social_targets_added": result.social_targets_added,
            "error_count": len(result.errors or []),
            "errors": result.errors or [],
            "metadata": {"limit": limit, "platform": "x"},
        }
