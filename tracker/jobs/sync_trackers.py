from __future__ import annotations

from tracker.db import session_scope
from tracker.services.tracker_sync_service import TrackerSyncService


def run_sync_trackers() -> dict:
    with session_scope() as session:
        return TrackerSyncService(session).sync_all_active_trackers()
