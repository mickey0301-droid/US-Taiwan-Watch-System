from __future__ import annotations

import json

from tracker.services.google_sheets_service import GoogleSheetsConfigurationError, GoogleSheetsService


EXPECTED_TABS = ["People", "Events", "Legislation"]


def main() -> None:
    service = GoogleSheetsService()
    try:
        tabs = service.list_worksheets()
        tab_names = [tab.title for tab in tabs]
        result: dict[str, object] = {
            "status": "success",
            "sheet_id": service.settings.google_sheet_id,
            "tabs": [
                {
                    "title": tab.title,
                    "row_count": tab.row_count,
                    "col_count": tab.col_count,
                }
                for tab in tabs
            ],
            "expected_tabs_present": {name: (name in tab_names) for name in EXPECTED_TABS},
            "headers": {},
        }
        for tab_name in EXPECTED_TABS:
            if tab_name in tab_names:
                result["headers"][tab_name] = service.read_header_row(tab_name)
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
