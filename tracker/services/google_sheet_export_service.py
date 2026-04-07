from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracker.db import session_scope
from tracker.models import (
    Appointment,
    Jurisdiction,
    Legislation,
    LegislationSource,
    LegislationSponsor,
    Office,
    Person,
    Statement,
    StatementParticipant,
    StatementSource,
)
from tracker.services.google_sheets_service import GoogleSheetsService


PEOPLE_HEADERS = [
    "person_id",
    "display_name_en",
    "display_name_zh",
    "full_name",
    "given_name",
    "family_name",
    "status",
    "level",
    "branch",
    "office_title",
    "department",
    "subdepartment",
    "unit",
    "jurisdiction",
    "party",
    "district",
    "committees",
    "official_page",
    "wikipedia_page",
    "portrait_url",
    "x_accounts",
    "facebook_accounts",
    "instagram_accounts",
    "date_of_birth",
    "place_of_birth",
    "education",
    "past_experience",
    "notes",
    "updated_at",
]

EVENTS_HEADERS = [
    "event_id",
    "event_date",
    "year",
    "month",
    "title",
    "summary",
    "event_type",
    "taiwan_keywords",
    "participants_en",
    "participants_zh",
    "participant_ids",
    "primary_source_type",
    "official_sources",
    "media_sources",
    "social_sources",
    "cspan_sources",
    "wikipedia_sources",
    "review_status",
    "source_count",
    "notes",
    "updated_at",
]

LEGISLATION_HEADERS = [
    "legislation_id",
    "scope",
    "session_label",
    "session_year",
    "jurisdiction",
    "bill_number",
    "title",
    "summary",
    "status",
    "chamber",
    "date",
    "sponsors_en",
    "sponsors_zh",
    "sponsor_ids",
    "official_page",
    "official_text_page",
    "latest_action",
    "committees",
    "cosponsor_count",
    "seed_source",
    "topic_tags",
    "additional_topics",
    "notes",
    "updated_at",
]


@dataclass
class PeopleExportResult:
    status: str
    people_exported: int
    worksheet: str


@dataclass
class EventsExportResult:
    status: str
    events_exported: int
    worksheet: str


@dataclass
class LegislationExportResult:
    status: str
    legislation_exported: int
    worksheet: str


class GoogleSheetExportService:
    def __init__(self) -> None:
        self.google_sheets = GoogleSheetsService()

    def export_people(self) -> PeopleExportResult:
        rows = self._build_people_rows()
        self.google_sheets.replace_rows("People", PEOPLE_HEADERS, rows)
        return PeopleExportResult(status="success", people_exported=len(rows), worksheet="People")

    def export_events(self) -> EventsExportResult:
        rows = self._build_event_rows()
        self.google_sheets.replace_rows("Events", EVENTS_HEADERS, rows)
        return EventsExportResult(status="success", events_exported=len(rows), worksheet="Events")

    def export_legislation(self) -> LegislationExportResult:
        rows = self._build_legislation_rows()
        self.google_sheets.replace_rows("Legislation", LEGISLATION_HEADERS, rows)
        return LegislationExportResult(status="success", legislation_exported=len(rows), worksheet="Legislation")

    def _build_people_rows(self) -> list[list[Any]]:
        with session_scope() as session:
            people = (
                session.execute(
                    select(Person)
                    .options(
                        selectinload(Person.aliases),
                        selectinload(Person.appointments).selectinload(Appointment.office),
                        selectinload(Person.appointments).selectinload(Appointment.jurisdiction),
                    )
                    .order_by(Person.full_name.asc())
                )
                .scalars()
                .all()
            )
            return [self._person_to_row(person) for person in people]

    def _build_event_rows(self) -> list[list[Any]]:
        with session_scope() as session:
            statements = (
                session.execute(
                    select(Statement)
                    .options(
                        selectinload(Statement.participants).selectinload(StatementParticipant.person),
                    )
                    .order_by(Statement.date_published.desc().nullslast(), Statement.date_collected.desc(), Statement.id.desc())
                )
                .scalars()
                .all()
            )
            statement_ids = [statement.id for statement in statements]
            all_sources = (
                session.execute(
                    select(StatementSource).where(StatementSource.statement_id.in_(statement_ids))
                )
                .scalars()
                .all()
                if statement_ids
                else []
            )
            sources_by_statement: dict[int, list[StatementSource]] = {}
            for source in all_sources:
                sources_by_statement.setdefault(source.statement_id, []).append(source)
            return [self._statement_to_row(statement, sources_by_statement.get(statement.id, [])) for statement in statements]

    def _build_legislation_rows(self) -> list[list[Any]]:
        with session_scope() as session:
            legislation_items = (
                session.execute(
                    select(Legislation)
                    .options(
                        selectinload(Legislation.sponsors).selectinload(LegislationSponsor.person),
                        selectinload(Legislation.sources),
                    )
                    .order_by(Legislation.introduced_date.desc().nullslast(), Legislation.last_action_date.desc().nullslast(), Legislation.id.desc())
                )
                .scalars()
                .all()
            )
            return [self._legislation_to_row(item) for item in legislation_items]

    def _person_to_row(self, person: Person) -> list[Any]:
        current_appointments = [appt for appt in person.appointments if appt.is_current]
        appointment = self._pick_primary_appointment(current_appointments or person.appointments)
        raw_payload = person.raw_payload or {}
        committees = raw_payload.get("committees") or raw_payload.get("committee_assignments") or []
        if isinstance(committees, list):
            committees_value = " | ".join(str(item).strip() for item in committees if str(item).strip())
        else:
            committees_value = str(committees or "").strip()

        chinese_names = [
            alias.alias.strip()
            for alias in person.aliases
            if alias.is_current and alias.alias_type == "chinese_name" and alias.alias
        ]
        display_name_zh = chinese_names[0] if chinese_names else ""

        wikipedia_page = ""
        if str(person.source_type or "").lower() == "wikipedia" and person.source_url:
            wikipedia_page = person.source_url
        elif raw_payload.get("wikipedia_url"):
            wikipedia_page = str(raw_payload.get("wikipedia_url"))

        social_profiles = person.social_profiles or {}
        x_accounts = self._flatten_social_values(social_profiles.get("x"))
        facebook_accounts = self._flatten_social_values(social_profiles.get("facebook"))
        instagram_accounts = self._flatten_social_values(social_profiles.get("instagram"))

        notes = raw_payload.get("full_name_display") or ""

        office: Office | None = appointment.office if appointment else None
        jurisdiction: Jurisdiction | None = appointment.jurisdiction if appointment else None
        appt_raw = appointment.raw_payload or {} if appointment else {}

        department = appt_raw.get("top_department_name") or appt_raw.get("department_name") or (
            jurisdiction.name if jurisdiction else ""
        )
        subdepartment = appt_raw.get("subdepartment_name") or ""
        unit = appt_raw.get("unit_name") or appointment.district if appointment else ""
        display_name_en = person.full_name
        if display_name_zh:
            display_name_en = f"{display_name_zh} {person.full_name}"

        row = [
            person.id,
            display_name_en,
            display_name_zh,
            person.full_name,
            person.given_name or "",
            person.family_name or "",
            "current" if person.is_current else "former",
            office.level if office else "",
            office.branch if office else "",
            appointment.role_title if appointment else "",
            department or "",
            subdepartment,
            unit or "",
            jurisdiction.name if jurisdiction else "",
            appointment.party if appointment and appointment.party else "",
            appointment.district if appointment and appointment.district else "",
            committees_value,
            person.canonical_official_url or "",
            wikipedia_page,
            person.portrait_url or "",
            x_accounts,
            facebook_accounts,
            instagram_accounts,
            person.date_of_birth.isoformat() if person.date_of_birth else "",
            person.place_of_birth or "",
            (person.education or "").strip(),
            (person.career_history or "").strip(),
            notes,
            person.updated_at.isoformat() if person.updated_at else "",
        ]
        return row

    def _pick_primary_appointment(self, appointments: list[Appointment]) -> Appointment | None:
        if not appointments:
            return None
        return sorted(
            appointments,
            key=lambda item: (
                0 if item.status == "current" else 1,
                0 if (item.office and item.office.level == "federal") else 1,
                0 if (item.office and item.office.branch == "executive") else 1,
                item.updated_at.isoformat() if item.updated_at else "",
            ),
        )[0]

    def _statement_to_row(self, statement: Statement, statement_sources: list[StatementSource]) -> list[Any]:
        participants = sorted(
            [participant for participant in statement.participants if participant.person],
            key=lambda participant: participant.person.full_name.lower(),
        )
        participant_names_en = [participant.person.full_name for participant in participants if participant.person]
        participant_names_zh = [self._person_primary_chinese_name(participant.person) for participant in participants if participant.person]
        participant_names_zh = [name for name in participant_names_zh if name]
        participant_ids = [str(participant.person_id) for participant in participants]

        sources_by_type: dict[str, list[str]] = {
            "official": [],
            "media": [],
            "social": [],
            "cspan": [],
            "wikipedia": [],
        }
        for source in statement_sources:
            label = source.source_title or source.source_url
            source_text = f"{label} | {source.source_url}" if source.source_url else label
            bucket = self._source_bucket(source.source_type)
            sources_by_type.setdefault(bucket, []).append(source_text)

        event_dt = statement.date_published or statement.date_collected
        event_type = statement.statement_type or statement.source_type
        matched_keywords = statement.matched_keywords or {}
        hits = matched_keywords.get("hits") if isinstance(matched_keywords, dict) else []
        if isinstance(hits, list):
            keywords_value = " | ".join(str(item).strip() for item in hits if str(item).strip())
        else:
            keywords_value = ""
        notes = ""
        raw_payload = statement.raw_payload or {}
        if isinstance(raw_payload, dict):
            seeded_from = raw_payload.get("seeded_from")
            if seeded_from:
                notes = f"seeded_from={seeded_from}"

        return [
            statement.id,
            event_dt.date().isoformat() if isinstance(event_dt, datetime) else "",
            event_dt.year if isinstance(event_dt, datetime) else "",
            event_dt.month if isinstance(event_dt, datetime) else "",
            statement.title,
            (statement.excerpt or statement.full_text or statement.raw_text or "").strip(),
            event_type,
            keywords_value,
            " | ".join(participant_names_en),
            " | ".join(participant_names_zh),
            " | ".join(participant_ids),
            statement.event_source_preference or statement.source_type,
            " | ".join(sources_by_type.get("official", [])),
            " | ".join(sources_by_type.get("media", [])),
            " | ".join(sources_by_type.get("social", [])),
            " | ".join(sources_by_type.get("cspan", [])),
            " | ".join(sources_by_type.get("wikipedia", [])),
            statement.review_status,
            len(statement_sources),
            notes,
            statement.updated_at.isoformat() if statement.updated_at else "",
        ]

    def _source_bucket(self, source_type: str | None) -> str:
        normalized = str(source_type or "").lower()
        if normalized in {"official", "official_api", "state_portal", "legislature_directory"}:
            return "official"
        if normalized in {"media"}:
            return "media"
        if normalized in {"social"}:
            return "social"
        if normalized in {"cspan", "secondary_video"}:
            return "cspan"
        if normalized in {"wikipedia", "seed"}:
            return "wikipedia"
        return normalized or "media"

    def _person_primary_chinese_name(self, person: Person) -> str:
        chinese_aliases = [
            alias.alias.strip()
            for alias in person.aliases
            if alias.is_current and alias.alias_type == "chinese_name" and alias.alias
        ]
        return chinese_aliases[0] if chinese_aliases else ""

    def _legislation_to_row(self, item: Legislation) -> list[Any]:
        sponsors = [sponsor for sponsor in item.sponsors if sponsor.person]
        sponsors_en = [sponsor.person.full_name for sponsor in sponsors if sponsor.person]
        sponsors_zh = [self._person_primary_chinese_name(sponsor.person) for sponsor in sponsors if sponsor.person]
        sponsors_zh = [name for name in sponsors_zh if name]
        sponsor_ids = [str(sponsor.person_id) for sponsor in sponsors]

        official_page = ""
        source_type = str(item.source_type or "").lower()
        if source_type in {"official", "official_api"}:
            official_page = item.source_url
        else:
            for source in item.sources:
                if str(source.source_type or "").lower() in {"official", "official_api"}:
                    official_page = source.source_url
                    break

        raw_payload = item.raw_payload or {}
        official_page = official_page or str(raw_payload.get("congress_gov_url") or "")
        official_text_page = str(raw_payload.get("text_page_url") or "")
        latest_action = str(raw_payload.get("latest_action_text") or "")
        committees = raw_payload.get("committee_assignments") or []
        if isinstance(committees, list):
            committees_value = " | ".join(str(item).strip() for item in committees if str(item).strip())
        else:
            committees_value = str(committees or "").strip()
        cosponsor_count = raw_payload.get("cosponsor_count") or ""
        topic_tags = []
        if isinstance(raw_payload, dict):
            if raw_payload.get("topic_tags"):
                topic_tags = raw_payload.get("topic_tags") or []
            elif raw_payload.get("topic_tag"):
                topic_tags = [raw_payload.get("topic_tag")]
        topic_tags_value = " | ".join(str(tag).strip() for tag in topic_tags if str(tag).strip())

        additional_topics = []
        if isinstance(raw_payload, dict):
            additional_topics = raw_payload.get("additional_topics") or []
        additional_topics_value = " | ".join(str(tag).strip() for tag in additional_topics if str(tag).strip())

        seed_source = item.parser_identity or source_type
        date_value = self._effective_legislation_date(item, raw_payload)
        session_label = ""
        session_year = ""
        if isinstance(raw_payload, dict):
            session_label = str(raw_payload.get("session_label") or raw_payload.get("congress_label") or raw_payload.get("session") or "")
            session_year = str(raw_payload.get("session_year") or raw_payload.get("congress") or "")

        notes = ""
        if isinstance(raw_payload, dict) and raw_payload.get("seeded_from"):
            notes = f"seeded_from={raw_payload['seeded_from']}"

        return [
            item.id,
            item.level,
            session_label,
            session_year,
            item.jurisdiction_name or "",
            item.bill_number or "",
            item.title,
            (item.summary or "").strip(),
            item.status_text or "",
            item.chamber or "",
            date_value.isoformat() if date_value else "",
            " | ".join(sponsors_en),
            " | ".join(sponsors_zh),
            " | ".join(sponsor_ids),
            official_page,
            official_text_page,
            latest_action,
            committees_value,
            cosponsor_count,
            seed_source,
            topic_tags_value,
            additional_topics_value,
            notes,
            item.updated_at.isoformat() if item.updated_at else "",
        ]

    def _effective_legislation_date(self, item: Legislation, raw_payload: dict[str, Any]) -> date | None:
        if item.introduced_date:
            return item.introduced_date
        introduced_from_payload = self._parse_date(raw_payload.get("introduced_on_congress"))
        if introduced_from_payload:
            return introduced_from_payload
        if item.last_action_date:
            return item.last_action_date
        for key in ("introduced_date", "latest_action_date", "update_date", "update_date_including_text"):
            parsed = self._parse_date(raw_payload.get(key))
            if parsed:
                return parsed
        return None

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

    def _flatten_social_values(self, value: Any) -> str:
        if value in (None, ""):
            return ""
        if isinstance(value, list):
            return " | ".join(str(item).strip() for item in value if str(item).strip())
        if isinstance(value, dict):
            return " | ".join(str(item).strip() for item in value.values() if str(item).strip())
        return str(value).strip()
