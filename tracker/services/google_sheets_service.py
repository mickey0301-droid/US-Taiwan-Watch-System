from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from tracker.config import get_settings


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsConfigurationError(RuntimeError):
    pass


class ProxyBypassGoogleSheetsConfigurationError(GoogleSheetsConfigurationError):
    pass


@dataclass
class GoogleSheetTabInfo:
    title: str
    row_count: int
    col_count: int


class GoogleSheetsService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _require_config(self) -> tuple[str | None, str | None, str]:
        service_account_file = self.settings.google_service_account_file
        service_account_json = self.settings.google_service_account_json
        sheet_id = self.settings.google_sheet_id
        if not sheet_id:
            raise GoogleSheetsConfigurationError("GOOGLE_SHEET_ID is not configured.")
        if service_account_json:
            return None, service_account_json, sheet_id
        if not service_account_file:
            raise GoogleSheetsConfigurationError(
                "Configure GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON."
            )
        path = Path(service_account_file)
        if not path.exists():
            raise GoogleSheetsConfigurationError(f"Google service account file does not exist: {path}")
        return str(path), None, sheet_id

    def _client(self):  # type: ignore[no-untyped-def]
        try:
            import gspread
            from gspread.http_client import HTTPClient
            from google.oauth2.service_account import Credentials
            from google.auth.transport.requests import AuthorizedSession
        except ImportError as exc:  # pragma: no cover
            raise GoogleSheetsConfigurationError(
                "Google Sheets dependencies are not installed. Run `pip install -r requirements.txt`."
            ) from exc

        class ProxyBypassHTTPClient(HTTPClient):
            def __init__(self, auth, session=None):  # type: ignore[no-untyped-def]
                self.auth = auth
                self.session = AuthorizedSession(auth)
                self.session.trust_env = False
                if getattr(self.session, "_auth_request_session", None) is not None:
                    self.session._auth_request_session.trust_env = False
                self.timeout = None

        service_account_file, service_account_json, _sheet_id = self._require_config()
        if service_account_json:
            credentials = Credentials.from_service_account_info(json.loads(service_account_json.strip()), scopes=SCOPES)
        else:
            credentials = Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
        return gspread.authorize(credentials, http_client=ProxyBypassHTTPClient)

    def open_sheet(self):  # type: ignore[no-untyped-def]
        client = self._client()
        _service_account_file, service_account_json, sheet_id = self._require_config()
        service_account_email = self._service_account_email(service_account_json)
        try:
            return client.open_by_key(sheet_id.strip())
        except Exception as exc:
            message = str(exc)
            if "SpreadsheetNotFound" in type(exc).__name__ or "not found" in message.lower():
                hint = f" Share the sheet with {service_account_email}." if service_account_email else ""
                raise GoogleSheetsConfigurationError(
                    f"Google Sheet '{sheet_id}' was not found or is not shared with the service account.{hint}"
                ) from exc
            raise GoogleSheetsConfigurationError(f"Unable to open Google Sheet '{sheet_id}': {message}") from exc

    def list_worksheets(self) -> list[GoogleSheetTabInfo]:
        spreadsheet = self.open_sheet()
        tabs: list[GoogleSheetTabInfo] = []
        for worksheet in spreadsheet.worksheets():
            tabs.append(
                GoogleSheetTabInfo(
                    title=worksheet.title,
                    row_count=worksheet.row_count,
                    col_count=worksheet.col_count,
                )
            )
        return tabs

    def read_header_row(self, worksheet_title: str) -> list[str]:
        spreadsheet = self.open_sheet()
        worksheet = spreadsheet.worksheet(worksheet_title)
        values = worksheet.row_values(1)
        return [str(value).strip() for value in values if str(value).strip()]

    def worksheet_exists(self, worksheet_title: str) -> bool:
        titles = {tab.title for tab in self.list_worksheets()}
        return worksheet_title in titles

    def worksheet_preview(self, worksheet_title: str, limit: int = 5) -> list[dict[str, Any]]:
        spreadsheet = self.open_sheet()
        worksheet = spreadsheet.worksheet(worksheet_title)
        rows = worksheet.get_all_records(head=1, default_blank="")
        return rows[:limit]

    def read_records(self, worksheet_title: str) -> list[dict[str, Any]]:
        spreadsheet = self.open_sheet()
        try:
            worksheet = spreadsheet.worksheet(worksheet_title)
        except Exception as exc:
            message = str(exc)
            raise GoogleSheetsConfigurationError(
                f"Worksheet '{worksheet_title}' is missing in Google Sheet '{self.settings.google_sheet_id}': {message}"
            ) from exc
        return worksheet.get_all_records(head=1, default_blank="")

    def _service_account_email(self, service_account_json: str | None) -> str | None:
        if not service_account_json:
            return None
        try:
            payload = json.loads(service_account_json.strip())
        except Exception:
            return None
        email = str(payload.get("client_email") or "").strip()
        return email or None

    def ensure_header_row(self, worksheet_title: str, headers: list[str]) -> dict[str, Any]:
        spreadsheet = self.open_sheet()
        worksheet = spreadsheet.worksheet(worksheet_title)
        current = worksheet.row_values(1)
        normalized_current = [str(value).strip() for value in current]
        normalized_expected = [str(value).strip() for value in headers]
        if normalized_current == normalized_expected:
            return {
                "worksheet": worksheet_title,
                "status": "unchanged",
                "header_count": len(normalized_expected),
            }
        worksheet.update("1:1", [normalized_expected])
        return {
            "worksheet": worksheet_title,
            "status": "updated",
            "header_count": len(normalized_expected),
        }

    def replace_rows(self, worksheet_title: str, headers: list[str], rows: list[list[Any]]) -> dict[str, Any]:
        spreadsheet = self.open_sheet()
        worksheet = spreadsheet.worksheet(worksheet_title)
        self.ensure_header_row(worksheet_title, headers)
        worksheet.batch_clear(["A2:ZZ"])
        if rows:
            worksheet.update("A2", rows)
        return {
            "worksheet": worksheet_title,
            "status": "updated",
            "row_count": len(rows),
        }
