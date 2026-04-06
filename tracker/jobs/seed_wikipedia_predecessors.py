from __future__ import annotations

from tracker.db import session_scope
from tracker.services.wikipedia_predecessor_service import WikipediaPredecessorService


def run_seed_wikipedia_predecessors() -> dict:
    with session_scope() as session:
        result = WikipediaPredecessorService(session).seed_from_current_people()
    return {
        "status": "failed" if result.errors else "success",
        "job_name": "seed_wikipedia_predecessors",
        "people_scanned": result.people_scanned,
        "records_found": result.predecessors_found,
        "records_created": result.records_created,
        "records_updated": result.records_updated,
        "errors": result.errors,
        "metadata": {
            "note": "Seeded former people from predecessor links on current officials' Wikipedia pages.",
        },
    }
