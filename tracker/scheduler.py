from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from tracker.config import get_settings
from tracker.jobs.backfill_portraits import run_backfill_portraits
from tracker.jobs.bootstrap_current_taiwan_2026 import run_bootstrap_current_taiwan_2026
from tracker.jobs.bootstrap_existing_people_taiwan_2025_2026 import run_bootstrap_existing_people_taiwan_2025_2026
from tracker.jobs.bootstrap_taiwan_chinese_sources import run_bootstrap_taiwan_chinese_sources
from tracker.jobs.cleanup import run_cleanup
from tracker.jobs.cleanup_malformed_legislation_people import run_cleanup_malformed_legislation_people
from tracker.jobs.discover_official_x_accounts import run_discover_official_x_accounts
from tracker.jobs.discover_current_legislator_x_candidates import run_discover_current_legislator_x_candidates
from tracker.jobs.discover_official_sources import run_discover_official_sources
from tracker.jobs.enrich_profiles import run_enrich_profiles
from tracker.jobs.enrich_current_federal_backgrounds import run_enrich_current_federal_backgrounds
from tracker.jobs.enrich_congress_bill_details import run_enrich_congress_bill_details
from tracker.jobs.enrich_congress_bills_official_links import run_enrich_congress_bills_official_links
from tracker.jobs.enrich_congress_bills_sponsors import run_enrich_congress_bills_sponsors
from tracker.jobs.export_google_sheet_data import run_export_google_sheet_data
from tracker.jobs.import_congress_bills_excel import run_import_congress_bills_excel
from tracker.jobs.import_google_sheet_data import run_import_google_sheet_data
from tracker.jobs.seed_historical_rosters import run_seed_historical_rosters
from tracker.jobs.seed_current_legislator_x_candidates import run_seed_current_legislator_x_candidates
from tracker.jobs.seed_taiwan_2025_sample_events import run_seed_taiwan_2025_sample_events
from tracker.jobs.seed_taiwan_2021_2024_sample_events import run_seed_taiwan_2021_2024_sample_events
from tracker.jobs.seed_biden_era_former_people import run_seed_biden_era_former_people
from tracker.jobs.seed_taiwan_governor_sample_events import run_seed_taiwan_governor_sample_events
from tracker.jobs.seed_taiwan_legislation_sample import run_seed_taiwan_legislation_sample
from tracker.jobs.seed_arizona_taiwan_legislation import run_seed_arizona_taiwan_legislation
from tracker.jobs.seed_wikipedia_predecessors import run_seed_wikipedia_predecessors
from tracker.jobs.seed_taiwan_2026_sample_events import run_seed_taiwan_2026_sample_events
from tracker.jobs.sync_media import run_sync_media
from tracker.jobs.sync_federal_department_wikipedia import run_sync_federal_department_wikipedia
try:
    from tracker.jobs.sync_federal_house_wikipedia import run_sync_federal_house_wikipedia
except Exception:  # optional job should not block app startup
    run_sync_federal_house_wikipedia = None
from tracker.jobs.sync_federal_senators_wikipedia import run_sync_federal_senators_wikipedia
from tracker.jobs.sync_officials import run_sync_officials
from tracker.jobs.sync_officials_wikipedia_only import run_sync_officials_wikipedia_only
from tracker.jobs.sync_state_department_wikipedia import run_sync_state_department_wikipedia
from tracker.jobs.sync_state_executive_official_pages import run_sync_state_executive_official_pages
from tracker.jobs.sync_state_executives_wikipedia import run_sync_state_executives_wikipedia
from tracker.jobs.sync_state_legislatures import run_sync_state_legislatures
from tracker.jobs.sync_state_representatives_wikipedia import run_sync_state_representatives_wikipedia
from tracker.jobs.sync_state_senators_wikipedia import run_sync_state_senators_wikipedia
from tracker.jobs.sync_territory_officials_wikipedia import run_sync_territory_officials_wikipedia
from tracker.jobs.sync_trackers import run_sync_trackers
from tracker.jobs.sync_congress_taiwan import run_sync_congress_taiwan
from tracker.logging_utils import get_logger


logger = get_logger(__name__)

JOB_REGISTRY = {
    "sync_congress_taiwan": run_sync_congress_taiwan,
    "sync_officials": run_sync_officials,
    "sync_officials_wikipedia_only": run_sync_officials_wikipedia_only,
    "sync_federal_department_wikipedia": run_sync_federal_department_wikipedia,
    "sync_federal_senators_wikipedia": run_sync_federal_senators_wikipedia,
    "sync_state_department_wikipedia": run_sync_state_department_wikipedia,
    "sync_state_executive_official_pages": run_sync_state_executive_official_pages,
    "sync_state_executives_wikipedia": run_sync_state_executives_wikipedia,
    "sync_state_legislatures": run_sync_state_legislatures,
    "sync_state_senators_wikipedia": run_sync_state_senators_wikipedia,
    "sync_state_representatives_wikipedia": run_sync_state_representatives_wikipedia,
    "sync_territory_officials_wikipedia": run_sync_territory_officials_wikipedia,
    "enrich_profiles": run_enrich_profiles,
    "enrich_current_federal_backgrounds": run_enrich_current_federal_backgrounds,
    "enrich_congress_bill_details": run_enrich_congress_bill_details,
    "enrich_congress_bills_official_links": run_enrich_congress_bills_official_links,
    "enrich_congress_bills_sponsors": run_enrich_congress_bills_sponsors,
    "export_google_sheet_data": run_export_google_sheet_data,
    "import_congress_bills_excel": run_import_congress_bills_excel,
    "import_google_sheet_data": run_import_google_sheet_data,
    "seed_historical_rosters": run_seed_historical_rosters,
    "seed_current_legislator_x_candidates": run_seed_current_legislator_x_candidates,
    "seed_biden_era_former_people": run_seed_biden_era_former_people,
    "seed_taiwan_2021_2024_sample_events": run_seed_taiwan_2021_2024_sample_events,
    "seed_taiwan_2025_sample_events": run_seed_taiwan_2025_sample_events,
    "seed_taiwan_legislation_sample": run_seed_taiwan_legislation_sample,
    "seed_arizona_taiwan_legislation": run_seed_arizona_taiwan_legislation,
    "seed_taiwan_governor_sample_events": run_seed_taiwan_governor_sample_events,
    "discover_official_sources": run_discover_official_sources,
    "discover_official_x_accounts": run_discover_official_x_accounts,
    "discover_current_legislator_x_candidates": run_discover_current_legislator_x_candidates,
    "seed_wikipedia_predecessors": run_seed_wikipedia_predecessors,
    "seed_taiwan_2026_sample_events": run_seed_taiwan_2026_sample_events,
    "backfill_portraits": run_backfill_portraits,
    "bootstrap_current_taiwan_2026": run_bootstrap_current_taiwan_2026,
    "bootstrap_existing_people_taiwan_2025_2026": run_bootstrap_existing_people_taiwan_2025_2026,
    "bootstrap_taiwan_chinese_sources": run_bootstrap_taiwan_chinese_sources,
    "sync_trackers": run_sync_trackers,
    "sync_media": run_sync_media,
    "cleanup": run_cleanup,
    "cleanup_malformed_legislation_people": run_cleanup_malformed_legislation_people,
}

if run_sync_federal_house_wikipedia is not None:
    JOB_REGISTRY["sync_federal_house_wikipedia"] = run_sync_federal_house_wikipedia


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone)
    for job_id, job_settings in settings.scheduler_jobs.items():
        if not job_settings.get("enabled", False):
            continue
        if job_id not in JOB_REGISTRY:
            logger.warning("Skipping unknown scheduler job: %s", job_id)
            continue
        scheduler.add_job(
            JOB_REGISTRY[job_id],
            trigger="interval",
            minutes=job_settings.get("minutes", 60),
            id=job_id,
            replace_existing=True,
        )
    return scheduler
