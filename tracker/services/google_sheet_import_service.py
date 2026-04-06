from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import time
from typing import Any

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tracker.services.google_sheets_service import GoogleSheetsService
from tracker.services.legislation_service import LegislationService
from tracker.services.officials_service import OfficialsService
from tracker.services.statements_service import StatementsService


@dataclass
class GoogleSheetImportResult:
    people_found: int = 0
    people_created: int = 0
    people_updated: int = 0
    events_found: int = 0
    events_created: int = 0
    events_updated: int = 0
    legislation_found: int = 0
    legislation_created: int = 0
    legislation_updated: int = 0
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


class GoogleSheetImportService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.google_sheets = GoogleSheetsService()
        self.officials_service = OfficialsService(session)
        self.statements_service = StatementsService(session)
        self.legislation_service = LegislationService(session)
        self.sheet_person_id_map: dict[str, int] = {}

    def import_all(self) -> GoogleSheetImportResult:
        result = GoogleSheetImportResult()
        self._import_people(result)
        self._import_legislation(result)
        self._import_events(result)
        return result

    def _import_people(self, result: GoogleSheetImportResult) -> None:
        rows = self.google_sheets.read_records("People")
        result.people_found = len(rows)
        for row in rows:
            try:
                created = self._run_with_retry(lambda: self._import_person_row(row))
            except Exception as exc:
                self.session.rollback()
                result.errors.append(f"People/{row.get('full_name') or row.get('person_id')}: {exc}")
                continue
            self.session.commit()
            if created:
                result.people_created += 1
            else:
                result.people_updated += 1

    def _import_legislation(self, result: GoogleSheetImportResult) -> None:
        rows = self.google_sheets.read_records("Legislation")
        result.legislation_found = len(rows)
        for row in rows:
            try:
                created = self._run_with_retry(lambda: self._import_legislation_row(row))
            except Exception as exc:
                self.session.rollback()
                result.errors.append(f"Legislation/{row.get('bill_number') or row.get('legislation_id')}: {exc}")
                continue
            self.session.commit()
            if created:
                result.legislation_created += 1
            else:
                result.legislation_updated += 1

    def _import_events(self, result: GoogleSheetImportResult) -> None:
        rows = self.google_sheets.read_records("Events")
        result.events_found = len(rows)
        for row in rows:
            try:
                created = self._run_with_retry(lambda: self._import_event_row(row))
            except Exception as exc:
                self.session.rollback()
                result.errors.append(f"Events/{row.get('title') or row.get('event_id')}: {exc}")
                continue
            self.session.commit()
            if created:
                result.events_created += 1
            else:
                result.events_updated += 1

    def _run_with_retry(self, fn, retries: int = 3):  # type: ignore[no-untyped-def]
        delay_seconds = 0.5
        for attempt in range(retries):
            try:
                return fn()
            except OperationalError as exc:
                self.session.rollback()
                if "database is locked" not in str(exc).lower() or attempt == retries - 1:
                    raise
                time.sleep(delay_seconds)
                delay_seconds *= 2

    def _import_person_row(self, row: dict[str, Any]) -> bool:
        full_name = self._clean(row.get("full_name"))
        if not full_name:
            raise ValueError("Missing full_name")

        source_url = (
            self._clean(row.get("official_page"))
            or self._clean(row.get("wikipedia_page"))
            or self._sheet_url("people", row.get("person_id") or full_name)
        )
        source_type = "official" if self._clean(row.get("official_page")) else "google_sheet"

        social_profiles = {
            "x": self._split_pipe(row.get("x_accounts")),
            "facebook": self._split_pipe(row.get("facebook_accounts")),
            "instagram": self._split_pipe(row.get("instagram_accounts")),
        }
        social_profiles = {key: value for key, value in social_profiles.items() if value}

        raw_payload = {
            "seeded_from": "google_sheet_import",
            "sheet_person_id": self._clean(row.get("person_id")),
            "display_name_en": self._clean(row.get("display_name_en")),
            "display_name_zh": self._clean(row.get("display_name_zh")),
            "wikipedia_url": self._clean(row.get("wikipedia_page")),
            "committees": self._split_pipe(row.get("committees")),
            "notes": self._clean(row.get("notes")),
        }
        person, created = self.officials_service.upsert_person(
            {
                "full_name": full_name,
                "given_name": self._clean(row.get("given_name")) or None,
                "family_name": self._clean(row.get("family_name")) or None,
                "source_url": source_url,
                "source_type": source_type,
                "seed_source_type": "google_sheet",
                "profile_status": "background_enriched",
                "canonical_official_url": self._clean(row.get("official_page")) or None,
                "portrait_url": self._clean(row.get("portrait_url")) or None,
                "portrait_source_url": self._clean(row.get("official_page")) or self._clean(row.get("wikipedia_page")) or source_url,
                "portrait_source_type": "official" if self._clean(row.get("official_page")) else "google_sheet",
                "social_profiles": social_profiles or None,
                "parser_identity": "google_sheet_import_people",
                "verification_status": "seeded_official_link" if self._clean(row.get("official_page")) else "unverified",
                "raw_payload": raw_payload,
            }
        )
        if self._clean(row.get("display_name_zh")):
            self.officials_service.ensure_alias(
                person.id,
                self._clean(row.get("display_name_zh")),
                source_url,
                "google_sheet",
                alias_type="chinese_name",
            )

        background_data = {
            "date_of_birth": self._parse_date(row.get("date_of_birth")),
            "place_of_birth": self._clean(row.get("place_of_birth")) or None,
            "education": self._clean(row.get("education")) or None,
            "career_history": self._clean(row.get("past_experience")) or None,
            "full_name_display": self._clean(row.get("display_name_en")) or None,
        }
        self.officials_service.enrich_person_background(person, background_data)
        person.is_current = str(row.get("status") or "").strip().lower() != "former"

        office_title = self._clean(row.get("office_title"))
        level = self._clean(row.get("level")) or "federal"
        jurisdiction_name = self._clean(row.get("jurisdiction")) or "United States"
        if office_title:
            jurisdiction_type = "state" if level == "state" else "country"
            jurisdiction = self.officials_service.get_or_create_jurisdiction(
                jurisdiction_name,
                jurisdiction_type,
            )
            office = self.officials_service.get_or_create_office(
                office_name=office_title,
                level=level,
                branch=self._clean(row.get("branch")) or None,
                chamber=self._infer_chamber(office_title, row.get("branch")),
                jurisdiction_id=jurisdiction.id,
                source_url=source_url,
                source_type="google_sheet",
            )
            self.officials_service.upsert_appointment(
                person,
                office,
                jurisdiction.id,
                {
                    "role_title": office_title,
                    "district": self._clean(row.get("district")) or None,
                    "party": self._clean(row.get("party")) or None,
                    "status": "current" if person.is_current else "former",
                    "source_url": source_url,
                    "source_type": "google_sheet",
                    "parser_identity": "google_sheet_import_people",
                    "verification_status": "unverified",
                    "is_current": person.is_current,
                    "raw_payload": {
                        "department_name": self._clean(row.get("department")),
                        "subdepartment_name": self._clean(row.get("subdepartment")),
                        "unit_name": self._clean(row.get("unit")),
                        "committees": self._split_pipe(row.get("committees")),
                    },
                },
            )

        sheet_person_id = self._clean(row.get("person_id"))
        if sheet_person_id:
            self.sheet_person_id_map[sheet_person_id] = person.id
        return created

    def _import_legislation_row(self, row: dict[str, Any]) -> bool:
        title = self._clean(row.get("title"))
        if not title:
            raise ValueError("Missing title")

        official_page = self._clean(row.get("official_page"))
        sponsors = self._build_legislation_sponsors(row, official_page)
        topic_tags = self._split_pipe(row.get("topic_tags"))
        additional_topics = self._split_pipe(row.get("additional_topics"))
        raw_payload = {
            "seeded_from": "google_sheet_import",
            "sheet_legislation_id": self._clean(row.get("legislation_id")),
            "session_label": self._clean(row.get("session_label")),
            "session_year": self._clean(row.get("session_year")),
            "text_page_url": self._clean(row.get("official_text_page")),
            "latest_action_text": self._clean(row.get("latest_action")),
            "committee_assignments": self._split_pipe(row.get("committees")),
            "cosponsor_count": self._parse_int(row.get("cosponsor_count")),
            "topic_tags": topic_tags,
            "additional_topics": additional_topics,
            "notes": self._clean(row.get("notes")),
        }
        legislation, created = self.legislation_service.upsert_legislation(
            {
                "title": title,
                "bill_number": self._clean(row.get("bill_number")) or None,
                "bill_slug": self._build_legislation_slug(row),
                "legislation_type": self._infer_legislation_type(row.get("bill_number")),
                "level": self._normalize_scope(row.get("scope")),
                "jurisdiction_name": self._clean(row.get("jurisdiction")) or "United States",
                "chamber": self._clean(row.get("chamber")) or None,
                "summary": self._clean(row.get("summary")) or None,
                "status_text": self._clean(row.get("status")) or None,
                "introduced_date": self._parse_date(row.get("date")),
                "last_action_date": self._parse_date(row.get("date")),
                "source_url": official_page or self._sheet_url("legislation", row.get("legislation_id") or title),
                "source_type": "official" if official_page else "google_sheet",
                "parser_identity": "google_sheet_import_legislation",
                "relevance_score": 1.0,
                "is_taiwan_related": True,
                "raw_payload": raw_payload,
                "sources": [
                    {
                        "source_url": official_page or self._sheet_url("legislation", row.get("legislation_id") or title),
                        "source_type": "official" if official_page else "google_sheet",
                        "source_title": title,
                        "parser_identity": "google_sheet_import_legislation",
                        "raw_payload": {"worksheet": "Legislation"},
                    }
                ],
                "sponsors": sponsors,
            }
        )
        return created

    def _import_event_row(self, row: dict[str, Any]) -> bool:
        title = self._clean(row.get("title"))
        if not title:
            raise ValueError("Missing title")
        participant_ids = self._resolve_sheet_person_ids(row.get("participant_ids"))
        participant_ids.extend(self._resolve_person_names(self._split_pipe(row.get("participants_en"))))
        participant_ids = sorted(set(participant_ids))
        source_url, source_type = self._primary_event_source(row)
        event_date = self._parse_datetime(row.get("event_date"))
        statement, created = self.statements_service.ingest_statement(
            {
                "person_id": participant_ids[0] if participant_ids else None,
                "participant_ids": participant_ids,
                "title": title,
                "source_url": source_url,
                "source_type": source_type,
                "source_title": title,
                "statement_type": self._clean(row.get("event_type")) or "event",
                "date_published": event_date,
                "excerpt": self._clean(row.get("summary")) or None,
                "full_text": self._clean(row.get("summary")) or None,
                "raw_text": self._clean(row.get("summary")) or "",
                "is_primary_source": True,
                "parser_identity": "google_sheet_import_events",
                "raw_payload": {
                    "seeded_from": "google_sheet_import",
                    "sheet_event_id": self._clean(row.get("event_id")),
                    "taiwan_keywords": self._split_pipe(row.get("taiwan_keywords")),
                    "participants_en": self._split_pipe(row.get("participants_en")),
                    "participants_zh": self._split_pipe(row.get("participants_zh")),
                    "official_sources": self._split_pipe(row.get("official_sources")),
                    "media_sources": self._split_pipe(row.get("media_sources")),
                    "social_sources": self._split_pipe(row.get("social_sources")),
                    "cspan_sources": self._split_pipe(row.get("cspan_sources")),
                    "wikipedia_sources": self._split_pipe(row.get("wikipedia_sources")),
                    "review_status": self._clean(row.get("review_status")),
                    "notes": self._clean(row.get("notes")),
                },
            }
        )
        if self._clean(row.get("review_status")):
            statement.review_status = self._clean(row.get("review_status"))
        return created

    def _build_legislation_sponsors(self, row: dict[str, Any], default_source_url: str | None) -> list[dict[str, Any]]:
        sponsor_names_en = self._split_pipe(row.get("sponsors_en"))
        sponsor_names_zh = self._split_pipe(row.get("sponsors_zh"))
        sponsor_sheet_ids = self._split_pipe(row.get("sponsor_ids"))
        sponsors: list[dict[str, Any]] = []
        for index, full_name in enumerate(sponsor_names_en):
            if not full_name:
                continue
            sponsors.append(
                {
                    "full_name": full_name,
                    "chinese_name": sponsor_names_zh[index] if index < len(sponsor_names_zh) else None,
                    "sheet_person_id": sponsor_sheet_ids[index] if index < len(sponsor_sheet_ids) else None,
                    "role": "sponsor" if index == 0 else "cosponsor",
                    "role_title": "Legislator",
                    "source_url": default_source_url,
                    "source_type": "official" if default_source_url else "google_sheet",
                }
            )
        return sponsors

    def _resolve_sheet_person_ids(self, raw_ids: Any) -> list[int]:
        resolved: list[int] = []
        for item in self._split_pipe(raw_ids):
            mapped = self.sheet_person_id_map.get(item)
            if mapped:
                resolved.append(mapped)
        return resolved

    def _resolve_person_names(self, names: list[str]) -> list[int]:
        resolved: list[int] = []
        for full_name in names:
            person = self.officials_service.find_person(full_name)
            if person:
                resolved.append(person.id)
        return resolved

    def _primary_event_source(self, row: dict[str, Any]) -> tuple[str, str]:
        source_fields = [
            ("official_sources", "official"),
            ("media_sources", "media"),
            ("social_sources", "social"),
            ("cspan_sources", "cspan"),
            ("wikipedia_sources", "wikipedia"),
        ]
        for field_name, source_type in source_fields:
            values = self._split_pipe(row.get(field_name))
            if not values:
                continue
            first = values[0]
            if " | " in first:
                maybe_url = first.rsplit(" | ", 1)[-1].strip()
                if maybe_url.startswith("http://") or maybe_url.startswith("https://"):
                    return maybe_url, source_type
            if first.startswith("http://") or first.startswith("https://"):
                return first, source_type
        event_id = self._clean(row.get("event_id")) or self._clean(row.get("title")) or "event"
        return self._sheet_url("events", event_id), "google_sheet"

    def _build_legislation_slug(self, row: dict[str, Any]) -> str | None:
        bill_number = self._clean(row.get("bill_number"))
        session_year = self._clean(row.get("session_year"))
        scope = self._normalize_scope(row.get("scope"))
        if not bill_number:
            return None
        parts = [scope, self._clean(row.get("jurisdiction")) or "United States", session_year or "", bill_number]
        return "|".join(part.strip() for part in parts if part.strip()).lower()

    def _infer_legislation_type(self, bill_number: Any) -> str | None:
        text = self._clean(bill_number).lower()
        if not text:
            return None
        return text.split(".", 1)[0].replace(" ", "")

    def _normalize_scope(self, value: Any) -> str:
        text = self._clean(value).lower()
        if text in {"federal", "state", "local", "territory"}:
            return text
        return "federal"

    def _infer_chamber(self, office_title: str | None, branch: Any) -> str | None:
        title = self._clean(office_title).lower()
        branch_text = self._clean(branch).lower()
        if "senate" in title:
            return "senate"
        if "house" in title or "representative" in title or "assembly" in title:
            return "house"
        if branch_text == "executive":
            return None
        return None

    def _parse_date(self, value: Any) -> date | None:
        text = self._clean(value)
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            return None

    def _parse_datetime(self, value: Any) -> datetime | None:
        text = self._clean(value)
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def _parse_int(self, value: Any) -> int | None:
        text = self._clean(value)
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None

    def _split_pipe(self, value: Any) -> list[str]:
        text = self._clean(value)
        if not text:
            return []
        return [item.strip() for item in text.split("|") if item.strip()]

    def _clean(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _sheet_url(self, worksheet: str, identifier: Any) -> str:
        cleaned_id = self._clean(identifier).replace(" ", "_")
        return f"sheet://{worksheet}/{cleaned_id or 'row'}"
