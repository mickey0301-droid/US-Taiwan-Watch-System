from __future__ import annotations

import json

from tracker.services.google_sheet_export_service import GoogleSheetExportService
from tracker.services.google_sheets_service import GoogleSheetsConfigurationError


def main() -> None:
    try:
        result = GoogleSheetExportService().export_legislation()
        print(
            json.dumps(
                {
                    "status": result.status,
                    "worksheet": result.worksheet,
                    "legislation_exported": result.legislation_exported,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except GoogleSheetsConfigurationError as exc:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
