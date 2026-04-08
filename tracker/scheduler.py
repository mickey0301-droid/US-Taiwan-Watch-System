from __future__ import annotations

from importlib import import_module
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from tracker.config import get_settings
from tracker.logging_utils import get_logger


logger = get_logger(__name__)

def _lazy_job(module_name: str, function_name: str, job_name: str) -> Callable[[], dict]:
    def _runner() -> dict:
        try:
            module = import_module(module_name)
            handler = getattr(module, function_name)
            return handler()
        except Exception as exc:
            logger.exception("Job %s failed during dynamic import/execute", job_name)
            return {"status": "failed", "job_name": job_name, "error": f"{type(exc).__name__}: {exc}"}

    return _runner


JOB_TARGETS: dict[str, tuple[str, str]] = {
    "sync_congress_taiwan": ("tracker.jobs.sync_congress_taiwan", "run_sync_congress_taiwan"),
    "sync_officials": ("tracker.jobs.sync_officials", "run_sync_officials"),
    "sync_officials_wikipedia_only": ("tracker.jobs.sync_officials_wikipedia_only", "run_sync_officials_wikipedia_only"),
    "sync_federal_department_wikipedia": ("tracker.jobs.sync_federal_department_wikipedia", "run_sync_federal_department_wikipedia"),
    "sync_federal_house_wikipedia": ("tracker.jobs.sync_federal_house_wikipedia", "run_sync_federal_house_wikipedia"),
    "sync_federal_senators_wikipedia": ("tracker.jobs.sync_federal_senators_wikipedia", "run_sync_federal_senators_wikipedia"),
    "sync_state_department_wikipedia": ("tracker.jobs.sync_state_department_wikipedia", "run_sync_state_department_wikipedia"),
    "sync_combatant_command_official_pages": ("tracker.jobs.sync_combatant_command_official_pages", "run_sync_combatant_command_official_pages"),
    "sync_federal_military_official_pages": ("tracker.jobs.sync_federal_military_official_pages", "run_sync_federal_military_official_pages"),
    "sync_state_executive_official_pages": ("tracker.jobs.sync_state_executive_official_pages", "run_sync_state_executive_official_pages"),
    "sync_state_executives_wikipedia": ("tracker.jobs.sync_state_executives_wikipedia", "run_sync_state_executives_wikipedia"),
    "sync_state_legislatures": ("tracker.jobs.sync_state_legislatures", "run_sync_state_legislatures"),
    "sync_state_senators_wikipedia": ("tracker.jobs.sync_state_senators_wikipedia", "run_sync_state_senators_wikipedia"),
    "sync_state_representatives_wikipedia": ("tracker.jobs.sync_state_representatives_wikipedia", "run_sync_state_representatives_wikipedia"),
    "sync_territory_officials_wikipedia": ("tracker.jobs.sync_territory_officials_wikipedia", "run_sync_territory_officials_wikipedia"),
    "enrich_profiles": ("tracker.jobs.enrich_profiles", "run_enrich_profiles"),
    "enrich_current_federal_backgrounds": ("tracker.jobs.enrich_current_federal_backgrounds", "run_enrich_current_federal_backgrounds"),
    "enrich_congress_bill_details": ("tracker.jobs.enrich_congress_bill_details", "run_enrich_congress_bill_details"),
    "enrich_congress_bills_official_links": ("tracker.jobs.enrich_congress_bills_official_links", "run_enrich_congress_bills_official_links"),
    "enrich_congress_bills_sponsors": ("tracker.jobs.enrich_congress_bills_sponsors", "run_enrich_congress_bills_sponsors"),
    "export_google_sheet_data": ("tracker.jobs.export_google_sheet_data", "run_export_google_sheet_data"),
    "import_congress_bills_excel": ("tracker.jobs.import_congress_bills_excel", "run_import_congress_bills_excel"),
    "import_google_sheet_data": ("tracker.jobs.import_google_sheet_data", "run_import_google_sheet_data"),
    "seed_historical_rosters": ("tracker.jobs.seed_historical_rosters", "run_seed_historical_rosters"),
    "seed_current_legislator_x_candidates": ("tracker.jobs.seed_current_legislator_x_candidates", "run_seed_current_legislator_x_candidates"),
    "seed_biden_era_former_people": ("tracker.jobs.seed_biden_era_former_people", "run_seed_biden_era_former_people"),
    "seed_taiwan_2021_2024_sample_events": ("tracker.jobs.seed_taiwan_2021_2024_sample_events", "run_seed_taiwan_2021_2024_sample_events"),
    "seed_taiwan_2025_sample_events": ("tracker.jobs.seed_taiwan_2025_sample_events", "run_seed_taiwan_2025_sample_events"),
    "seed_taiwan_legislation_sample": ("tracker.jobs.seed_taiwan_legislation_sample", "run_seed_taiwan_legislation_sample"),
    "seed_arizona_taiwan_legislation": ("tracker.jobs.seed_arizona_taiwan_legislation", "run_seed_arizona_taiwan_legislation"),
    "seed_taiwan_governor_sample_events": ("tracker.jobs.seed_taiwan_governor_sample_events", "run_seed_taiwan_governor_sample_events"),
    "discover_official_sources": ("tracker.jobs.discover_official_sources", "run_discover_official_sources"),
    "discover_official_x_accounts": ("tracker.jobs.discover_official_x_accounts", "run_discover_official_x_accounts"),
    "discover_current_legislator_x_candidates": ("tracker.jobs.discover_current_legislator_x_candidates", "run_discover_current_legislator_x_candidates"),
    "seed_wikipedia_predecessors": ("tracker.jobs.seed_wikipedia_predecessors", "run_seed_wikipedia_predecessors"),
    "seed_taiwan_2026_sample_events": ("tracker.jobs.seed_taiwan_2026_sample_events", "run_seed_taiwan_2026_sample_events"),
    "backfill_portraits": ("tracker.jobs.backfill_portraits", "run_backfill_portraits"),
    "bootstrap_current_taiwan_2026": ("tracker.jobs.bootstrap_current_taiwan_2026", "run_bootstrap_current_taiwan_2026"),
    "bootstrap_existing_people_taiwan_2025_2026": ("tracker.jobs.bootstrap_existing_people_taiwan_2025_2026", "run_bootstrap_existing_people_taiwan_2025_2026"),
    "bootstrap_taiwan_chinese_sources": ("tracker.jobs.bootstrap_taiwan_chinese_sources", "run_bootstrap_taiwan_chinese_sources"),
    "sync_trackers": ("tracker.jobs.sync_trackers", "run_sync_trackers"),
    "sync_media": ("tracker.jobs.sync_media", "run_sync_media"),
    "cleanup": ("tracker.jobs.cleanup", "run_cleanup"),
    "cleanup_malformed_legislation_people": ("tracker.jobs.cleanup_malformed_legislation_people", "run_cleanup_malformed_legislation_people"),
    "run_scheduled_collections": ("tracker.jobs.run_scheduled_collections", "run_scheduled_collections"),
    "dedupe_records_by_url": ("tracker.jobs.dedupe_records_by_url", "run_dedupe_records_by_url"),
}

JOB_REGISTRY = {job_name: _lazy_job(module_name, function_name, job_name) for job_name, (module_name, function_name) in JOB_TARGETS.items()}


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
    if settings.scheduler_enabled:
        scheduler.add_job(
            JOB_REGISTRY["run_scheduled_collections"],
            trigger="interval",
            minutes=10,
            id="run_scheduled_collections",
            replace_existing=True,
        )
    return scheduler
