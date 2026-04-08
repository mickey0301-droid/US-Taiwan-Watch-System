from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from urllib.parse import quote
from typing import Any

from tracker.config import get_settings


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
logger = logging.getLogger(__name__)
GOOGLE_SHEET_DISABLED_MESSAGE = "Google Sheet integration is permanently disabled in this deployment."


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
        raise GoogleSheetsConfigurationError(GOOGLE_SHEET_DISABLED_MESSAGE)
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
            credentials = Credentials.from_service_account_info(self._load_service_account_info(service_account_json), scopes=SCOPES)
        else:
            credentials = Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
        try:
            return gspread.authorize(credentials)
        except Exception as exc:
            logger.warning("Standard Google Sheets client failed, retrying with proxy-bypass client: %s", exc)
            try:
                return gspread.authorize(credentials, http_client=ProxyBypassHTTPClient)
            except Exception as fallback_exc:
                raise GoogleSheetsConfigurationError(
                    f"Failed to initialize Google Sheets client: {type(fallback_exc).__name__}: {fallback_exc}"
                ) from fallback_exc

    def _credentials(self):  # type: ignore[no-untyped-def]
        try:
            from google.oauth2.service_account import Credentials
        except ImportError as exc:  # pragma: no cover
            raise GoogleSheetsConfigurationError(
                "Google Sheets dependencies are not installed. Run `pip install -r requirements.txt`."
            ) from exc

        service_account_file, service_account_json, _sheet_id = self._require_config()
        if service_account_json:
            credentials = Credentials.from_service_account_info(
                self._load_service_account_info(service_account_json),
                scopes=SCOPES,
            )
        else:
            credentials = Credentials.from_service_account_file(service_account_file, scopes=SCOPES)

        # Prefer self-signed JWT access on cloud to avoid fragile token-exchange streams.
        if hasattr(credentials, "with_always_use_jwt_access"):
            credentials = credentials.with_always_use_jwt_access(True)
        return credentials

    def _api_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            from google.auth.transport.requests import AuthorizedSession
        except ImportError as exc:  # pragma: no cover
            raise GoogleSheetsConfigurationError(
                "Google Sheets dependencies are not installed. Run `pip install -r requirements.txt`."
            ) from exc

        _service_account_file, service_account_json, sheet_id = self._require_config()
        service_account_email = self._service_account_email(service_account_json)
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id.strip()}{path}"
        credentials = self._credentials()
        try:
            session = AuthorizedSession(credentials)
            response = session.get(url, params=params, timeout=30)
        except Exception as exc:
            raise GoogleSheetsConfigurationError(
                f"Google Sheets API request failed: {type(exc).__name__}: {repr(exc)}"
            ) from exc

        if response.status_code == 404:
            hint = f" Share the sheet with {service_account_email}." if service_account_email else ""
            raise GoogleSheetsConfigurationError(
                f"Google Sheet '{sheet_id}' was not found or is not shared with the service account.{hint}"
            )
        if response.status_code == 403:
            hint = f" Share the sheet with {service_account_email}." if service_account_email else ""
            raise GoogleSheetsConfigurationError(f"Google Sheets access denied.{hint}")
        if response.status_code >= 400:
            raise GoogleSheetsConfigurationError(
                f"Google Sheets API error {response.status_code}: {response.text[:300]}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise GoogleSheetsConfigurationError("Google Sheets API returned an unexpected response.")
        return payload

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
        payload = self._api_get("", params={"fields": "sheets.properties"})
        tabs: list[GoogleSheetTabInfo] = []
        for sheet in payload.get("sheets", []):
            properties = sheet.get("properties", {})
            grid_properties = properties.get("gridProperties", {})
            tabs.append(
                GoogleSheetTabInfo(
                    title=str(properties.get("title") or ""),
                    row_count=int(grid_properties.get("rowCount") or 0),
                    col_count=int(grid_properties.get("columnCount") or 0),
                )
            )
        return tabs

    def read_header_row(self, worksheet_title: str) -> list[str]:
        sheet_range = f"'{worksheet_title}'!1:1"
        payload = self._api_get(f"/values/{quote(sheet_range, safe='')}")
        values = payload.get("values", [[]])
        first_row = values[0] if values else []
        if not isinstance(first_row, list):
            return []
        values = first_row
        return [str(value).strip() for value in values if str(value).strip()]

    def worksheet_exists(self, worksheet_title: str) -> bool:
        titles = {tab.title for tab in self.list_worksheets()}
        return worksheet_title in titles

    def worksheet_preview(self, worksheet_title: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.read_records(worksheet_title)
        return rows[:limit]

    def read_records(self, worksheet_title: str) -> list[dict[str, Any]]:
        if not self.worksheet_exists(worksheet_title):
            raise GoogleSheetsConfigurationError(
                f"Worksheet '{worksheet_title}' is missing in Google Sheet '{self.settings.google_sheet_id}'."
            )
        sheet_range = f"'{worksheet_title}'!A:ZZ"
        payload = self._api_get(f"/values/{quote(sheet_range, safe='')}")
        rows = payload.get("values", [])
        if not isinstance(rows, list) or not rows:
            return []
        header_row = [str(value).strip() for value in rows[0] if str(value).strip()]
        if not header_row:
            return []
        records: list[dict[str, Any]] = []
        for raw_row in rows[1:]:
            if not isinstance(raw_row, list):
                continue
            padded_row = list(raw_row) + [""] * max(0, len(header_row) - len(raw_row))
            records.append({header_row[index]: str(padded_row[index]) if index < len(padded_row) else "" for index in range(len(header_row))})
        return records

    def _service_account_email(self, service_account_json: str | None) -> str | None:
        if not service_account_json:
            return None
        try:
            payload = self._load_service_account_info(service_account_json)
        except Exception:
            return None
        email = str(payload.get("client_email") or "").strip()
        return email or None

    def _load_service_account_info(self, service_account_json: str) -> dict[str, Any]:
        raw = service_account_json.strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = json.loads(self._normalize_service_account_json(raw))
        if not isinstance(payload, dict):
            raise GoogleSheetsConfigurationError("GOOGLE_SERVICE_ACCOUNT_JSON must decode to a JSON object.")
        return payload

    def _normalize_service_account_json(self, raw: str) -> str:
        private_key_pattern = re.compile(r'("private_key"\s*:\s*")(.+?)(")', re.DOTALL)

        def _escape_private_key(match: re.Match[str]) -> str:
            prefix, private_key, suffix = match.groups()
            normalized = private_key.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
            return f"{prefix}{normalized}{suffix}"

        return private_key_pattern.sub(_escape_private_key, raw, count=1)

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
