from __future__ import annotations

from datetime import date, datetime
import logging
import re
from typing import Any

from tracker.services.google_sheets_service import GoogleSheetsConfigurationError, GoogleSheetsService


_URL_PATTERN = re.compile(r"https?://[^\s|]+")
logger = logging.getLogger(__name__)


class GoogleSheetReadService:
    def __init__(self) -> None:
        self.google_sheets = GoogleSheetsService()
        self.last_error: str | None = None

    def has_any_data(self) -> bool:
        return bool(self.list_people() or self.list_events() or self.list_legislation())

    def get_last_error(self) -> str | None:
        return self.last_error

    def list_people(self) -> list[dict[str, Any]]:
        rows = self._read_records("People")
        normalized: list[dict[str, Any]] = []
        for row in rows:
            normalized.append(
                {
                    **row,
                    "person_id": self._parse_int(row.get("person_id")),
                    "status": str(row.get("status") or "").strip().lower() or "unknown",
                    "level": str(row.get("level") or "").strip().lower(),
                    "branch": str(row.get("branch") or "").strip().lower(),
                    "display_name_en": str(row.get("display_name_en") or row.get("full_name") or "").strip(),
                    "display_name_zh": str(row.get("display_name_zh") or "").strip(),
                    "office_title": str(row.get("office_title") or "").strip(),
                    "department": str(row.get("department") or "").strip(),
                    "subdepartment": str(row.get("subdepartment") or "").strip(),
                    "unit": str(row.get("unit") or "").strip(),
                    "jurisdiction": str(row.get("jurisdiction") or "").strip(),
                    "x_accounts_list": self._split_values(row.get("x_accounts")),
                    "facebook_accounts_list": self._split_values(row.get("facebook_accounts")),
                    "instagram_accounts_list": self._split_values(row.get("instagram_accounts")),
                    "committees_list": self._split_values(row.get("committees")),
                    "updated_at_date": self._parse_date(row.get("updated_at")),
                }
            )
        return sorted(normalized, key=lambda item: (str(item.get("full_name") or "").lower(), item.get("person_id") or 0))

    def get_person(self, person_id: int) -> dict[str, Any] | None:
        for person in self.list_people():
            if person.get("person_id") == int(person_id):
                return person
        return None

    def list_events(self) -> list[dict[str, Any]]:
        rows = self._read_records("Events")
        normalized: list[dict[str, Any]] = []
        for row in rows:
            normalized.append(
                {
                    **row,
                    "event_id": self._parse_int(row.get("event_id")),
                    "event_date_date": self._parse_date(row.get("event_date")),
                    "year_int": self._parse_int(row.get("year")),
                    "month_int": self._parse_int(row.get("month")),
                    "participant_ids_list": self._parse_int_list(row.get("participant_ids")),
                    "participants_en_list": self._split_values(row.get("participants_en")),
                    "participants_zh_list": self._split_values(row.get("participants_zh")),
                    "source_urls": self._collect_source_urls(row),
                    "source_count_int": self._parse_int(row.get("source_count")) or 0,
                }
            )
        return sorted(
            normalized,
            key=lambda item: (
                item.get("event_date_date") or date.min,
                item.get("event_id") or 0,
            ),
            reverse=True,
        )

    def list_events_for_person(self, person_id: int) -> list[dict[str, Any]]:
        target_person_id = int(person_id)
        person = self.get_person(target_person_id)
        name_keys = self._person_name_keys(person) if person else set()

        matched: list[dict[str, Any]] = []
        for item in self.list_events():
            participant_ids = item.get("participant_ids_list", [])
            if target_person_id in participant_ids:
                matched.append(item)
                continue
            if not name_keys:
                continue
            participant_names = set(self._event_participant_name_keys(item))
            if name_keys & participant_names:
                matched.append(item)
        return matched

    def list_legislation(self) -> list[dict[str, Any]]:
        rows = self._read_records("Legislation")
        normalized: list[dict[str, Any]] = []
        for row in rows:
            normalized.append(
                {
                    **row,
                    "legislation_id": self._parse_int(row.get("legislation_id")),
                    "date_date": self._parse_date(row.get("date")),
                    "session_year_int": self._parse_int(row.get("session_year")),
                    "sponsor_ids_list": self._parse_int_list(row.get("sponsor_ids")),
                    "sponsors_en_list": self._split_values(row.get("sponsors_en")),
                    "sponsors_zh_list": self._split_values(row.get("sponsors_zh")),
                    "committees_list": self._split_values(row.get("committees")),
                    "topic_tags_list": self._split_values(row.get("topic_tags")),
                    "additional_topics_list": self._split_values(row.get("additional_topics")),
                    "cosponsor_count_int": self._parse_int(row.get("cosponsor_count")),
                }
            )
        return sorted(
            normalized,
            key=lambda item: (
                item.get("date_date") or date.min,
                item.get("legislation_id") or 0,
            ),
            reverse=True,
        )

    def list_legislation_for_person(self, person_id: int) -> list[dict[str, Any]]:
        return [item for item in self.list_legislation() if int(person_id) in item.get("sponsor_ids_list", [])]

    def _read_records(self, worksheet_title: str) -> list[dict[str, Any]]:
        try:
            self.last_error = None
            return self.google_sheets.read_records(worksheet_title)
        except GoogleSheetsConfigurationError as exc:
            self.last_error = str(exc)
            logger.warning("Google Sheet fallback configuration error for worksheet %s: %s", worksheet_title, exc)
            return []
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Google Sheet fallback read failed for worksheet %s", worksheet_title)
            return []

    def _collect_source_urls(self, row: dict[str, Any]) -> list[str]:
        urls: list[str] = []
        for key in ("official_sources", "media_sources", "social_sources", "cspan_sources", "wikipedia_sources"):
            for match in _URL_PATTERN.findall(str(row.get(key) or "")):
                if match not in urls:
                    urls.append(match)
        return urls

    def _split_values(self, value: Any) -> list[str]:
        parts = [str(item).strip() for item in str(value or "").split("|")]
        return [item for item in parts if item]

    def _person_name_keys(self, person: dict[str, Any]) -> set[str]:
        keys: set[str] = set()

        def _add(value: Any) -> None:
            normalized = self._normalize_name_key(value)
            if normalized:
                keys.add(normalized)

        _add(person.get("full_name"))
        _add(person.get("display_name_en"))
        _add(person.get("display_name_zh"))
        given = str(person.get("given_name") or "").strip()
        family = str(person.get("family_name") or "").strip()
        if given and family:
            _add(f"{given} {family}")
            _add(f"{family} {given}")
        return keys

    def _event_participant_name_keys(self, event: dict[str, Any]) -> list[str]:
        keys: list[str] = []
        for value in list(event.get("participants_en_list") or []) + list(event.get("participants_zh_list") or []):
            normalized = self._normalize_name_key(value)
            if normalized:
                keys.append(normalized)
        return keys

    def _normalize_name_key(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", text)

    def _parse_int(self, value: Any) -> int | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None

    def _parse_int_list(self, value: Any) -> list[int]:
        values: list[int] = []
        for item in self._split_values(value):
            parsed = self._parse_int(item)
            if parsed is not None:
                values.append(parsed)
        return values

    def _parse_date(self, value: Any) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        for parser in (date.fromisoformat, lambda raw: datetime.fromisoformat(raw).date()):
            try:
                return parser(text)
            except ValueError:
                continue
        return None
