from __future__ import annotations

from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import HistoricalRoster
from tracker.services.roster_service import RosterService


def run_seed_historical_rosters() -> dict:
    with session_scope() as session:
        before = session.execute(select(HistoricalRoster.id)).scalars().all()
        service = RosterService(session)
        service.seed_default_rosters()
        after = session.execute(select(HistoricalRoster.id)).scalars().all()

    return {
        "status": "success",
        "job_name": "seed_historical_rosters",
        "records_created": max(0, len(after) - len(before)),
        "records_found": len(after),
        "metadata": {
            "congress_range": "101-119",
            "presidential_terms": "41-47",
        },
    }
