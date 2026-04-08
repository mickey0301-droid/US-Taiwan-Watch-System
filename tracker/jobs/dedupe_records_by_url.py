from __future__ import annotations

from tracker.db import session_scope
from tracker.services.dedupe_cleanup_service import DedupeCleanupService


def run_dedupe_records_by_url() -> dict:
    with session_scope() as session:
        return DedupeCleanupService(session).cleanup_all()
