from __future__ import annotations

from tracker.services.google_sheet_export_service import GoogleSheetExportService


def run_export_google_sheet_data() -> dict:
    service = GoogleSheetExportService()
    people = service.export_people()
    events = service.export_events()
    legislation = service.export_legislation()
    return {
        "job_name": "export_google_sheet_data",
        "status": "success",
        "records_found": 0,
        "records_created": 0,
        "records_updated": people.people_exported + events.events_exported + legislation.legislation_exported,
        "metadata": {
            "people_exported": people.people_exported,
            "events_exported": events.events_exported,
            "legislation_exported": legislation.legislation_exported,
        },
        "errors": [],
    }
