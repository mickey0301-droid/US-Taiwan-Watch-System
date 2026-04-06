from __future__ import annotations

from tracker.collectors.state_executive_official_pages import StateExecutiveOfficialPagesCollector


def run_sync_state_executive_official_pages() -> dict:
    collector = StateExecutiveOfficialPagesCollector()
    run_result = collector.sync()
    return {
        "job_name": run_result.job_name,
        "status": "failed" if run_result.errors else "success",
        "records_found": run_result.records_found,
        "records_created": run_result.records_created,
        "records_updated": run_result.records_updated,
        "records_deactivated": run_result.records_deactivated,
        "errors": run_result.errors,
        "metadata": run_result.metadata,
    }
