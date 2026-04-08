from __future__ import annotations

from tracker.db import session_scope
from tracker.services.person_taiwan_event_monitor_service import PersonTaiwanEventMonitorService


def run_person_taiwan_monitors() -> dict:
    with session_scope() as session:
        result = PersonTaiwanEventMonitorService(session).run_due_monitors()
        return {
            "status": "success",
            "job_name": "run_person_taiwan_monitors",
            **result,
        }

