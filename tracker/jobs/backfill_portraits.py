from __future__ import annotations

from tracker.db import session_scope
from tracker.services.portrait_backfill_service import PortraitBackfillService


def run_backfill_portraits() -> dict:
    with session_scope() as session:
        result = PortraitBackfillService(session).backfill_all()
    return {
        "status": "success" if not result.errors else "partial_failure",
        "people_scanned": result.people_scanned,
        "portraits_updated": result.portraits_updated,
        "source_counts": result.source_counts,
        "errors": result.errors[:20],
        "error_count": len(result.errors),
    }
