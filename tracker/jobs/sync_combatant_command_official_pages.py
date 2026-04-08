from __future__ import annotations

from tracker.collectors.federal_subcabinet import FederalSubcabinetCollector
from tracker.config import get_source_registry


def run_sync_combatant_command_official_pages() -> dict:
    sources = get_source_registry().get("federal_subcabinet_sources", [])
    command_departments = [
        str(item.get("department_name") or "")
        for item in sources
        if str(item.get("parser_type") or "") in {
            "pacom_leadership",
            "combatant_command_leadership",
            "military_high_command_leadership",
        }
    ]
    results = []
    for department_name in command_departments:
        collector = FederalSubcabinetCollector(department_filter=department_name)
        run_result = collector.sync()
        results.append(
            {
                "department_name": department_name,
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
