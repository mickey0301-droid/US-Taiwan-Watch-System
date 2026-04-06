from __future__ import annotations

from tracker.collectors.profile_background_enrichment import ProfileBackgroundEnrichmentCollector


def run_enrich_profiles() -> dict:
    run_result = ProfileBackgroundEnrichmentCollector().sync()
    return {
        "status": "failed" if run_result.errors else "success",
        "job_name": run_result.job_name,
        "records_found": run_result.records_found,
        "records_updated": run_result.records_updated,
        "errors": run_result.errors,
        "metadata": run_result.metadata,
    }
