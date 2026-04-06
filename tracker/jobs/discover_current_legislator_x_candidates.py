from __future__ import annotations

from datetime import datetime

from tracker.db import session_scope
from tracker.models import SyncRun
from tracker.services.x_candidate_discovery_service import XCandidateDiscoveryService


def run_discover_current_legislator_x_candidates(limit: int | None = None) -> dict:
    with session_scope() as session:
        service = XCandidateDiscoveryService(session)
        sync_run = SyncRun(
            job_name="discover_current_legislator_x_candidates",
            job_type="profile_enrichment",
            source_name="duckduckgo_x_candidate_search",
        )
        session.add(sync_run)
        session.flush()
        result = service.discover_current_legislator_candidates(limit=limit)
        sync_run.ended_at = datetime.utcnow()
        sync_run.status = "partial_failure" if result.errors else "success"
        sync_run.records_found = result.people_scanned
        sync_run.records_updated = result.records_updated
        sync_run.meta = {
            "high_confidence_found": result.high_confidence_found,
            "needs_review_found": result.needs_review_found,
            "rejected_found": result.rejected_found,
            "errors": result.errors,
            "limit": limit,
        }
        return {
            "status": sync_run.status,
            "job_name": "discover_current_legislator_x_candidates",
            "people_scanned": result.people_scanned,
            "records_updated": result.records_updated,
            "high_confidence_found": result.high_confidence_found,
            "needs_review_found": result.needs_review_found,
            "rejected_found": result.rejected_found,
            "error_count": len(result.errors or []),
            "errors": result.errors or [],
            "metadata": {"limit": limit, "platform": "x", "search_backend": "duckduckgo_html"},
        }
