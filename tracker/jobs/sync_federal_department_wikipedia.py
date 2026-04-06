from __future__ import annotations

from tracker.collectors.cabinet_level_wikipedia import CabinetLevelWikipediaCollector
from tracker.collectors.current_federal_executive_appointments_wikipedia import CurrentFederalExecutiveAppointmentsWikipediaCollector
from tracker.collectors.current_federal_executive_wikipedia import CurrentFederalExecutiveWikipediaCollector
from tracker.collectors.federal_department_main_wikipedia import FederalDepartmentMainWikipediaCollector
from tracker.collectors.federal_department_units_wikipedia import FederalDepartmentUnitsWikipediaCollector
from tracker.collectors.justice_department_wikipedia import JusticeDepartmentWikipediaCollector
from tracker.collectors.state_department_wikipedia import (
    StateDepartmentAssistantSecretariesWikipediaCollector,
    StateDepartmentOrganizationWikipediaCollector,
    StateDepartmentUnderSecretariesWikipediaCollector,
)
from tracker.collectors.treasury_department_wikipedia import TreasuryDepartmentWikipediaCollector
from tracker.collectors.white_house_wikipedia import WhiteHouseWikipediaCollector


def run_sync_federal_department_wikipedia() -> dict:
    collectors = [
        CurrentFederalExecutiveWikipediaCollector(),
        CurrentFederalExecutiveAppointmentsWikipediaCollector(),
        FederalDepartmentMainWikipediaCollector(),
        FederalDepartmentUnitsWikipediaCollector(),
        StateDepartmentOrganizationWikipediaCollector(),
        StateDepartmentUnderSecretariesWikipediaCollector(),
        StateDepartmentAssistantSecretariesWikipediaCollector(),
        TreasuryDepartmentWikipediaCollector(),
        JusticeDepartmentWikipediaCollector(),
        WhiteHouseWikipediaCollector(),
        CabinetLevelWikipediaCollector(),
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
