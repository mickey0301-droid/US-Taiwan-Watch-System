from __future__ import annotations

from tracker.collectors.cabinet_level_wikipedia import CabinetLevelWikipediaCollector
from tracker.collectors.congress_api_members import CongressApiMembersCollector
from tracker.collectors.congress_house import HouseCollector
from tracker.collectors.congress_senate import SenateCollector
from tracker.collectors.current_federal_executive_appointments_wikipedia import CurrentFederalExecutiveAppointmentsWikipediaCollector
from tracker.collectors.current_federal_executive_wikipedia import CurrentFederalExecutiveWikipediaCollector
from tracker.collectors.federal_department_main_wikipedia import FederalDepartmentMainWikipediaCollector
from tracker.collectors.federal_department_units_wikipedia import FederalDepartmentUnitsWikipediaCollector
from tracker.collectors.federal_executive import FederalExecutiveCollector
from tracker.collectors.federal_subcabinet import FederalSubcabinetCollector
from tracker.collectors.former_federal_executive import FormerFederalExecutiveCollector
from tracker.collectors.former_senate_seed import FormerSenateSeedCollector
from tracker.collectors.govtrack_enrichment import GovTrackEnrichmentCollector
from tracker.collectors.governors import GovernorsCollector
from tracker.collectors.justice_department_wikipedia import JusticeDepartmentWikipediaCollector
from tracker.collectors.profile_background_enrichment import ProfileBackgroundEnrichmentCollector
from tracker.collectors.senate_seed import SenateSeedCollector
from tracker.collectors.state_department_wikipedia import (
    StateDepartmentAssistantSecretariesWikipediaCollector,
    StateDepartmentOrganizationWikipediaCollector,
    StateDepartmentUnderSecretariesWikipediaCollector,
)
from tracker.collectors.state_executive_wikipedia import StateExecutiveWikipediaCollector
from tracker.collectors.state_executive_official_pages import StateExecutiveOfficialPagesCollector
from tracker.collectors.state_legislatures import StateLegislaturesCollector
from tracker.collectors.state_representatives_wikipedia import StateRepresentativesWikipediaCollector
from tracker.collectors.state_senators_wikipedia import StateSenatorsWikipediaCollector
from tracker.collectors.territory_officials_wikipedia import TerritoryOfficialsWikipediaCollector
from tracker.collectors.treasury_department_wikipedia import TreasuryDepartmentWikipediaCollector
from tracker.collectors.white_house_wikipedia import WhiteHouseWikipediaCollector


def run_sync_officials() -> dict:
    collectors = [
        CongressApiMembersCollector(current_member=True),
        SenateCollector(),
        SenateSeedCollector(),
        FormerSenateSeedCollector(),
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
        FederalExecutiveCollector(),
        FederalSubcabinetCollector(),
        FormerFederalExecutiveCollector(),
        HouseCollector(),
        GovernorsCollector(),
        StateExecutiveWikipediaCollector(),
        StateExecutiveOfficialPagesCollector(),
        StateSenatorsWikipediaCollector(),
        StateRepresentativesWikipediaCollector(),
        TerritoryOfficialsWikipediaCollector(),
        StateLegislaturesCollector(),
        GovTrackEnrichmentCollector(),
        ProfileBackgroundEnrichmentCollector(),
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
