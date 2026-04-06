from __future__ import annotations

from tracker.collectors.state_executive_wikipedia import StateExecutiveWikipediaCollector


def run_sync_state_executives_wikipedia() -> dict:
    collector = StateExecutiveWikipediaCollector()
    run_result = collector.sync()
    return {
        "job_name": run_result.job_name,
        "status": "failed" if run_result.errors else "success",
        "records_found": run_result.records_found,
        "records_created": run_result.records_created,
        "records_updated": run_result.records_updated,
        "errors": run_result.errors,
        "metadata": run_result.metadata,
    }
