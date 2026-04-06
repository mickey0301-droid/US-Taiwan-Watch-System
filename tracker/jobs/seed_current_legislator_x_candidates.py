from __future__ import annotations

from datetime import datetime

from tracker.db import session_scope
from tracker.models import SyncRun
from tracker.services.x_candidate_service import XCandidateService


def run_seed_current_legislator_x_candidates() -> dict:
    with session_scope() as session:
        service = XCandidateService(session)
        sync_run = SyncRun(
            job_name="seed_current_legislator_x_candidates",
            job_type="profile_enrichment",
            source_name="google_x_candidate_search",
        )
        session.add(sync_run)
        session.flush()
        result = service.seed_current_legislator_x_searches()
        sync_run.ended_at = datetime.utcnow()
        sync_run.status = "success"
        sync_run.records_found = result.people_scanned
        sync_run.records_updated = result.records_updated
        sync_run.meta = {"mode": "current_legislators", "platform": "x"}
        return {
            "status": "success",
            "job_name": "seed_current_legislator_x_candidates",
            "people_scanned": result.people_scanned,
            "records_updated": result.records_updated,
            "metadata": {"mode": "current_legislators", "platform": "x"},
        }
