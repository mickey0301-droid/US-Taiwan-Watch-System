from __future__ import annotations

from typing import Callable

from tracker.collectors.federal_department_main_wikipedia import (
    FEDERAL_DEPARTMENT_WIKIPEDIA_PAGES,
    FederalDepartmentMainWikipediaCollector,
)
from tracker.collectors.federal_department_linked_units_wikipedia import (
    FederalDepartmentLinkedUnitsWikipediaCollector,
)
from tracker.collectors.federal_department_units_wikipedia import (
    FEDERAL_DEPARTMENT_UNIT_PAGES,
    FederalDepartmentUnitsWikipediaCollector,
)
from tracker.collectors.justice_department_wikipedia import JusticeDepartmentWikipediaCollector
from tracker.collectors.state_department_wikipedia import (
    StateDepartmentAssistantSecretariesWikipediaCollector,
    StateDepartmentOrganizationWikipediaCollector,
    StateDepartmentUnderSecretariesWikipediaCollector,
)
from tracker.collectors.treasury_department_wikipedia import TreasuryDepartmentWikipediaCollector


def _collector_result(run_result) -> dict:
    return {
        "job_name": run_result.job_name,
        "status": "failed" if run_result.errors else "success",
        "records_found": run_result.records_found,
        "records_created": run_result.records_created,
        "records_updated": run_result.records_updated,
        "errors": run_result.errors,
        "metadata": run_result.metadata,
    }


def _run_filtered_main_department(department_name: str) -> dict:
    collector = FederalDepartmentMainWikipediaCollector()
    pages = [item for item in FEDERAL_DEPARTMENT_WIKIPEDIA_PAGES if item["department_name"] == department_name]
    collector.fetch = lambda: pages  # type: ignore[method-assign]
    return _collector_result(collector.sync())


def _run_filtered_department_units(department_name: str) -> dict:
    collector = FederalDepartmentUnitsWikipediaCollector()
    pages = [item for item in FEDERAL_DEPARTMENT_UNIT_PAGES if item["department_name"] == department_name]
    collector.fetch = lambda: pages  # type: ignore[method-assign]
    return _collector_result(collector.sync())


def _run_filtered_linked_department_units(department_name: str) -> dict:
    collector = FederalDepartmentLinkedUnitsWikipediaCollector()
    discovered = [item for item in collector.fetch() if item["department_name"] == department_name]
    collector.fetch = lambda: discovered  # type: ignore[method-assign]
    return _collector_result(collector.sync())


DEPARTMENT_RUNNERS: dict[str, list[Callable[[], dict]]] = {
    "Department of State": [
        lambda: _run_filtered_main_department("Department of State"),
        lambda: _run_filtered_department_units("Department of State"),
        lambda: _run_filtered_linked_department_units("Department of State"),
        lambda: _collector_result(StateDepartmentOrganizationWikipediaCollector().sync()),
        lambda: _collector_result(StateDepartmentUnderSecretariesWikipediaCollector().sync()),
        lambda: _collector_result(StateDepartmentAssistantSecretariesWikipediaCollector().sync()),
    ],
    "Department of the Treasury": [
        lambda: _run_filtered_main_department("Department of the Treasury"),
        lambda: _run_filtered_department_units("Department of the Treasury"),
        lambda: _run_filtered_linked_department_units("Department of the Treasury"),
        lambda: _collector_result(TreasuryDepartmentWikipediaCollector().sync()),
    ],
    "Department of Justice": [
        lambda: _run_filtered_main_department("Department of Justice"),
        lambda: _run_filtered_department_units("Department of Justice"),
        lambda: _run_filtered_linked_department_units("Department of Justice"),
        lambda: _collector_result(JusticeDepartmentWikipediaCollector().sync()),
    ],
}


def run_sync_single_department_wikipedia(department_name: str) -> dict:
    runners = DEPARTMENT_RUNNERS.get(
        department_name,
        [
            lambda: _run_filtered_main_department(department_name),
            lambda: _run_filtered_department_units(department_name),
            lambda: _run_filtered_linked_department_units(department_name),
        ],
    )
    results = [runner() for runner in runners]
    failed = [item for item in results if item["status"] == "failed"]
    return {
        "status": "partial_failure" if failed else "success",
        "department_name": department_name,
        "results": results,
    }
