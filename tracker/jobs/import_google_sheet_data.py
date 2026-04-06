from __future__ import annotations

from tracker.db import session_scope
from tracker.services.google_sheet_import_service import GoogleSheetImportService


def run_import_google_sheet_data() -> dict:
    with session_scope() as session:
        service = GoogleSheetImportService(session)
        result = service.import_all()
        return {
            "job_name": "import_google_sheet_data",
            "status": "success" if not result.errors else "partial_success",
            "records_found": result.people_found + result.events_found + result.legislation_found,
            "records_created": result.people_created + result.events_created + result.legislation_created,
            "records_updated": result.people_updated + result.events_updated + result.legislation_updated,
            "metadata": {
                "people": {
                    "found": result.people_found,
                    "created": result.people_created,
                    "updated": result.people_updated,
                },
                "events": {
                    "found": result.events_found,
                    "created": result.events_created,
                    "updated": result.events_updated,
                },
                "legislation": {
                    "found": result.legislation_found,
                    "created": result.legislation_created,
                    "updated": result.legislation_updated,
                },
            },
            "errors": result.errors[:100],
        }
