from __future__ import annotations

from tracker.collectors.state_department_wikipedia import (
    StateDepartmentAssistantSecretariesWikipediaCollector,
    StateDepartmentOrganizationWikipediaCollector,
    StateDepartmentUnderSecretariesWikipediaCollector,
)


def run_sync_state_department_wikipedia() -> dict:
    collectors = [
        StateDepartmentOrganizationWikipediaCollector(),
        StateDepartmentUnderSecretariesWikipediaCollector(),
        StateDepartmentAssistantSecretariesWikipediaCollector(),
    ]
    results = []
    for collector in collectors:
        run_result = collector.sync()
        results.append(
            {
                "job_name": run_result.job_name,
                "status": "failed" if run_result.errors else "success",
                "records_found": run_result.records_found,
                "records_created": run_result.records_created,
                "records_updated": run_result.records_updated,
                "errors": run_result.errors,
                "metadata": run_result.metadata,
            }
        )
    failed = [item for item in results if item["status"] == "failed"]
    return {
        "status": "partial_failure" if failed else "success",
        "results": results,
    }
