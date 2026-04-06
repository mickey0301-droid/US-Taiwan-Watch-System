from __future__ import annotations

from tracker.collectors.territory_officials_wikipedia import TerritoryOfficialsWikipediaCollector


def run_sync_territory_officials_wikipedia() -> dict:
    collector = TerritoryOfficialsWikipediaCollector()
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
