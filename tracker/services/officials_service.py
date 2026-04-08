from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tracker.models import Alias, Appointment, Jurisdiction, Office, Person
from tracker.utils.names import normalize_person_name, slugify_name, split_person_name


@dataclass
class UpsertResult:
    created: int = 0
    updated: int = 0
    deactivated: int = 0


class InvalidPersonNameError(ValueError):
    def __init__(self, full_name: str, reason: str, category: str = "invalid_person_name") -> None:
        self.full_name = full_name
        self.reason = reason
        self.category = category
        super().__init__(f"{reason}: {full_name}")

    def to_dict(self) -> dict[str, str]:
        return {
            "rejected_name": self.full_name,
            "reason": self.reason,
            "category": self.category,
        }


class OfficialsService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.validation_log: list[dict[str, str]] = []

    def get_or_create_jurisdiction(self, name: str, jurisdiction_type: str, code: str | None = None, parent_id: int | None = None) -> Jurisdiction:
        stmt = select(Jurisdiction).where(Jurisdiction.name == name, Jurisdiction.type == jurisdiction_type)
        jurisdiction = self.session.execute(stmt).scalar_one_or_none()
        if jurisdiction:
            jurisdiction.last_seen_at = datetime.utcnow()
            jurisdiction.code = code or jurisdiction.code
            jurisdiction.parent_id = parent_id or jurisdiction.parent_id
            return jurisdiction
        jurisdiction = Jurisdiction(name=name, type=jurisdiction_type, code=code, parent_id=parent_id, country="United States")
        self.session.add(jurisdiction)
        self.session.flush()
        return jurisdiction

    def get_or_create_office(
        self,
        office_name: str,
        level: str,
        branch: str | None,
        chamber: str | None,
        jurisdiction_id: int | None,
        source_url: str,
        source_type: str,
    ) -> Office:
        stmt = select(Office).where(
            Office.office_name == office_name,
            Office.level == level,
            Office.chamber == chamber,
            Office.jurisdiction_id == jurisdiction_id,
        )
        office = self.session.execute(stmt).scalar_one_or_none()
        if office:
            office.last_seen_at = datetime.utcnow()
            office.branch = branch or office.branch
            office.source_url = source_url
            office.source_type = source_type
            return office
        office = Office(
            office_name=office_name,
            level=level,
            branch=branch,
            chamber=chamber,
            jurisdiction_id=jurisdiction_id,
            source_url=source_url,
            source_type=source_type,
        )
        self.session.add(office)
        self.session.flush()
        return office

    def find_person(self, full_name: str) -> Person | None:
        candidates = self.session.execute(select(Person)).scalars().all()
        normalized_name = slugify_name(full_name)
        for person in candidates:
            if person.official_slug == normalized_name:
                return person
        alias_person_id = self.session.execute(
            select(Alias.person_id).where(
                func.lower(Alias.alias) == full_name.lower(),
                Alias.is_current.is_(True),
            )
        ).scalars().first()
        if alias_person_id:
            alias_person = self.session.get(Person, int(alias_person_id))
            if alias_person:
                return alias_person
        for person in candidates:
            if fuzz.token_sort_ratio(person.full_name.lower(), full_name.lower()) >= 97:
                return person
        return None

    def validate_person_name(self, full_name: str) -> str:
        normalized = normalize_person_name(full_name)
        if not normalized:
            raise InvalidPersonNameError(normalized or full_name or "(empty)", "Empty person name")

        if re.match(r"^(district|state)\s+\d+(st|nd|rd|th)$", normalized, re.IGNORECASE):
            raise InvalidPersonNameError(normalized, "Rejected non-person label", "district_label")

        if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+\d+(st|nd|rd|th)$", normalized):
            raise InvalidPersonNameError(normalized, "Rejected geographic/district label", "geographic_label")

        lower_name = normalized.lower()
        if any(token in lower_name for token in ["district", "at large"]) and re.search(r"\d+(st|nd|rd|th)\b", lower_name):
            raise InvalidPersonNameError(normalized, "Rejected district-like name", "district_like_label")

        word_count = len(normalized.replace(",", " ").split())
        if word_count < 2:
            raise InvalidPersonNameError(normalized, "Rejected short non-person name", "too_short")

        return normalized

    def upsert_person(self, payload: dict[str, Any]) -> tuple[Person, bool]:
        try:
            validated_name = self.validate_person_name(payload["full_name"])
        except InvalidPersonNameError as exc:
            self.validation_log.append(exc.to_dict())
            raise
        payload["full_name"] = validated_name
        inferred_given_name, inferred_family_name = split_person_name(validated_name)
        payload["given_name"] = payload.get("given_name") or inferred_given_name
        payload["family_name"] = payload.get("family_name") or inferred_family_name
        person = self.find_person(validated_name)
        created = False
        if not person:
            person = Person(
                full_name=validated_name,
                given_name=payload.get("given_name"),
                family_name=payload.get("family_name"),
                honorific=payload.get("honorific"),
                official_slug=slugify_name(validated_name),
                source_url=payload.get("source_url"),
                source_type=payload.get("source_type"),
                seed_source_type=payload.get("seed_source_type") or payload.get("source_type"),
                profile_status=payload.get("profile_status", "seeded"),
                canonical_official_url=payload.get("canonical_official_url"),
                portrait_url=payload.get("portrait_url"),
                portrait_source_url=payload.get("portrait_source_url"),
                portrait_source_type=payload.get("portrait_source_type"),
                social_profiles=payload.get("social_profiles"),
                parser_identity=payload.get("parser_identity"),
                verification_status=payload.get("verification_status", "unverified"),
                raw_payload=payload.get("raw_payload"),
            )
            self.session.add(person)
            self.session.flush()
            created = True
            self._append_person_source_link(person, payload)
        else:
            person.last_seen_at = datetime.utcnow()
            incoming_rank = self.source_sort_key(payload.get("source_type"), payload.get("source_url"))
            current_rank = self.source_sort_key(person.source_type, person.source_url)
            if payload.get("source_url") and (not person.source_url or incoming_rank <= current_rank):
                person.source_url = payload.get("source_url") or person.source_url
            if payload.get("source_type") and (not person.source_type or incoming_rank <= current_rank):
                person.source_type = payload.get("source_type") or person.source_type
            person.seed_source_type = person.seed_source_type or payload.get("seed_source_type") or payload.get("source_type")
            person.profile_status = payload.get("profile_status") or person.profile_status
            if payload.get("canonical_official_url") and (not person.canonical_official_url or incoming_rank <= current_rank):
                person.canonical_official_url = payload.get("canonical_official_url") or person.canonical_official_url
            if payload.get("portrait_url"):
                self.set_best_portrait(
                    person,
                    portrait_url=payload.get("portrait_url"),
                    portrait_source_url=payload.get("portrait_source_url"),
                    portrait_source_type=payload.get("portrait_source_type"),
                )
            if payload.get("social_profiles"):
                merged_profiles = dict(person.social_profiles or {})
                merged_profiles.update(payload["social_profiles"])
                person.social_profiles = merged_profiles
            person.parser_identity = payload.get("parser_identity") or person.parser_identity
            person.raw_payload = self._merge_raw_payload(person.raw_payload, payload.get("raw_payload"))
            self._append_person_source_link(person, payload)
            if validated_name != person.full_name:
                self.ensure_alias(
                    person_id=person.id,
                    alias_text=validated_name,
                    source_url=payload.get("source_url"),
                    source_type=payload.get("source_type"),
                    alias_type="alternate_name",
                )
        return person, created

    def _append_person_source_link(self, person: Person, payload: dict[str, Any]) -> None:
        raw_payload = dict(person.raw_payload or {})
        links = list(raw_payload.get("source_links") or [])
        seen_urls = {
            str(item.get("url") or "").strip()
            for item in links
            if isinstance(item, dict)
        }
        parser_identity = str(payload.get("parser_identity") or "").strip() or None
        source_type = str(payload.get("source_type") or "").strip() or None
        for url in [payload.get("source_url"), payload.get("canonical_official_url")]:
            url_text = str(url or "").strip()
            if not url_text or url_text in seen_urls:
                continue
            links.append(
                {
                    "url": url_text,
                    "source_type": source_type,
                    "parser_identity": parser_identity,
                }
            )
            seen_urls.add(url_text)
        if links:
            raw_payload["source_links"] = links[-80:]
            person.raw_payload = raw_payload

    def enrich_person_profile(
        self,
        person: Person,
        official_url: str | None = None,
        portrait_url: str | None = None,
        portrait_source_url: str | None = None,
        portrait_source_type: str | None = None,
        bio: str | None = None,
        social_profiles: dict[str, str] | None = None,
    ) -> None:
        person.last_seen_at = datetime.utcnow()
        if official_url:
            person.canonical_official_url = official_url
            person.source_url = official_url
            person.source_type = "official"
            person.profile_status = "officially_enriched"
            if person.verification_status == "unverified":
                person.verification_status = "seeded_official_link"
        if portrait_url:
            self.set_best_portrait(
                person,
                portrait_url=portrait_url,
                portrait_source_url=portrait_source_url or official_url,
                portrait_source_type=portrait_source_type or "official",
            )
        if bio and not person.bio:
            person.bio = bio
        if social_profiles:
            merged = dict(person.social_profiles or {})
            merged.update({key: value for key, value in social_profiles.items() if value})
            person.social_profiles = merged

    def ensure_wikipedia_portrait(self, person: Person, portrait_url: str | None, wikipedia_url: str | None) -> None:
        if not portrait_url:
            return
        self.set_best_portrait(
            person,
            portrait_url=portrait_url,
            portrait_source_url=wikipedia_url,
            portrait_source_type="wikipedia",
        )

    def set_best_portrait(
        self,
        person: Person,
        portrait_url: str | None,
        portrait_source_url: str | None,
        portrait_source_type: str | None,
    ) -> bool:
        if not portrait_url:
            return False
        incoming_type = portrait_source_type or "unknown"
        current_rank = self._portrait_priority(person.portrait_source_type)
        incoming_rank = self._portrait_priority(incoming_type)
        if person.portrait_url and incoming_rank > current_rank:
            return False
        person.portrait_url = portrait_url
        person.portrait_source_url = portrait_source_url or person.portrait_source_url
        person.portrait_source_type = incoming_type
        return True

    def _portrait_priority(self, source_type: str | None) -> int:
        ranking = {
            "official": 0,
            "official_api": 0,
            "social": 1,
            "wikipedia": 2,
        }
        return ranking.get((source_type or "").lower(), 99)

    def enrich_person_background(
        self,
        person: Person,
        profile_data: dict[str, Any],
        field_sources: dict[str, dict[str, str]] | None = None,
    ) -> list[str]:
        updated_fields: list[str] = []
        field_sources = field_sources or {}

        simple_fields = ["date_of_birth", "place_of_birth", "education", "career_history"]
        for field_name in simple_fields:
            incoming = profile_data.get(field_name)
            current = getattr(person, field_name)
            if incoming in (None, "", []):
                continue
            if current not in (None, "", []):
                continue
            setattr(person, field_name, incoming)
            updated_fields.append(field_name)

        if updated_fields:
            person.last_seen_at = datetime.utcnow()
            if person.profile_status in {None, "seeded", "officially_enriched"}:
                person.profile_status = "background_enriched"
            raw_payload = dict(person.raw_payload or {})
            background_sources = dict(raw_payload.get("background_sources") or {})
            for field_name in updated_fields:
                if field_name in field_sources:
                    background_sources[field_name] = field_sources[field_name]
            full_name_display = profile_data.get("full_name_display")
            if full_name_display and not raw_payload.get("full_name_display"):
                raw_payload["full_name_display"] = full_name_display
                if "full_name_display" in field_sources:
                    background_sources["full_name_display"] = field_sources["full_name_display"]
            raw_payload["background_sources"] = background_sources
            person.raw_payload = raw_payload

        return updated_fields

    def ensure_alias(
        self,
        person_id: int,
        alias_text: str,
        source_url: str | None,
        source_type: str | None,
        alias_type: str = "alternate_name",
    ) -> None:
        stmt = select(Alias).where(Alias.person_id == person_id, Alias.alias == alias_text)
        alias = self.session.execute(stmt).scalars().first()
        if alias:
            alias.last_seen_at = datetime.utcnow()
            alias.alias_type = alias.alias_type or alias_type
            alias.source_url = source_url or alias.source_url
            alias.source_type = source_type or alias.source_type
            return
        self.session.add(
            Alias(
                person_id=person_id,
                alias=alias_text,
                alias_type=alias_type,
                source_url=source_url,
                source_type=source_type,
            )
        )

    def chinese_alias_sort_key(self, source_type: str | None, source_url: str | None = None) -> tuple[int, str]:
        return self.source_sort_key(source_type, source_url)

    def source_sort_key(self, source_type: str | None, source_url: str | None = None) -> tuple[int, str]:
        normalized = str(source_type or "").lower()
        ranking = {
            "official": 0,
            "official_api": 0,
            "legislature_directory": 0,
            "state_portal": 0,
            "media": 1,
            "social": 2,
            "google_sheet": 3,
            "wikipedia": 4,
            "seed": 5,
            "ai": 9,
            "ai_generated": 9,
        }
        return (ranking.get(normalized, 5), str(source_url or ""))

    def _merge_raw_payload(self, current: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any] | None:
        if not incoming:
            return current
        if not current:
            return incoming
        merged = dict(current)
        for key, value in incoming.items():
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value
            elif isinstance(merged[key], dict) and isinstance(value, dict):
                nested = dict(merged[key])
                nested.update({nested_key: nested_value for nested_key, nested_value in value.items() if nested_key not in nested or nested[nested_key] in (None, "", [], {})})
                merged[key] = nested
        return merged

    def list_chinese_aliases(self, person_id: int) -> list[str]:
        aliases = self.session.execute(
            select(Alias).where(
                Alias.person_id == person_id,
                Alias.alias_type == "chinese_name",
                Alias.is_current.is_(True),
            )
        ).scalars().all()
        aliases = sorted(
            aliases,
            key=lambda alias: (
                self.chinese_alias_sort_key(alias.source_type, alias.source_url),
                alias.id,
            ),
        )
        ordered: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            alias_text = (alias.alias or "").strip()
            if not alias_text or alias_text in seen:
                continue
            seen.add(alias_text)
            ordered.append(alias_text)
        return ordered

    def upsert_appointment(self, person: Person, office: Office, jurisdiction_id: int | None, payload: dict[str, Any]) -> bool:
        stmt = select(Appointment).where(
            Appointment.person_id == person.id,
            Appointment.office_id == office.id,
            Appointment.jurisdiction_id == jurisdiction_id,
            Appointment.role_title == payload["role_title"],
            Appointment.start_date == payload.get("start_date"),
        )
        appointment = self.session.execute(stmt).scalars().first()
        if not appointment and office.level == "state" and (office.branch or "").lower() == "legislative":
            # Guard against duplicate active records across differently named state-legislative offices.
            district_text = str(payload.get("district") or "").strip()
            fallback_stmt = (
                select(Appointment)
                .join(Office, Office.id == Appointment.office_id)
                .where(
                    Appointment.person_id == person.id,
                    Appointment.jurisdiction_id == jurisdiction_id,
                    Appointment.is_current.is_(True),
                    Office.level == "state",
                    Office.branch == "legislative",
                    Office.chamber == office.chamber,
                    func.coalesce(Appointment.district, "") == district_text,
                )
                .order_by(Appointment.id.desc())
            )
            appointment = self.session.execute(fallback_stmt).scalars().first()
        if appointment:
            appointment.last_seen_at = datetime.utcnow()
            appointment.status = payload.get("status", appointment.status)
            appointment.party = payload.get("party") or appointment.party
            appointment.district = payload.get("district") or appointment.district
            appointment.end_date = payload.get("end_date") or appointment.end_date
            appointment.is_current = payload.get("is_current", True)
            appointment.raw_payload = payload.get("raw_payload") or appointment.raw_payload
            return False
        self.session.add(
            Appointment(
                person_id=person.id,
                office_id=office.id,
                jurisdiction_id=jurisdiction_id,
                role_title=payload["role_title"],
                district=payload.get("district"),
                party=payload.get("party"),
                status=payload.get("status", "current"),
                start_date=payload.get("start_date"),
                end_date=payload.get("end_date"),
                source_url=payload.get("source_url"),
                source_type=payload.get("source_type"),
                parser_identity=payload.get("parser_identity"),
                verification_status=payload.get("verification_status", "unverified"),
                is_current=payload.get("is_current", True),
                raw_payload=payload.get("raw_payload"),
            )
        )
        return True

    def reconcile_current_appointments(
        self,
        parser_identity: str,
        seen_keys: set[tuple[int, int, int | None, str]],
        jurisdiction_ids: set[int] | None = None,
    ) -> int:
        stmt = select(Appointment).where(
            Appointment.parser_identity == parser_identity,
            Appointment.is_current.is_(True),
        )
        if jurisdiction_ids:
            stmt = stmt.where(Appointment.jurisdiction_id.in_(jurisdiction_ids))
        appointments = self.session.execute(stmt).scalars().all()
        deactivated = 0
        touched_person_ids: set[int] = set()
        for appointment in appointments:
            key = (appointment.person_id, appointment.office_id, appointment.jurisdiction_id, appointment.role_title)
            if key in seen_keys:
                continue
            appointment.is_current = False
            appointment.status = "former"
            appointment.end_date = appointment.end_date or datetime.utcnow().date()
            appointment.last_seen_at = datetime.utcnow()
            deactivated += 1
            touched_person_ids.add(appointment.person_id)

        if touched_person_ids:
            for person_id in touched_person_ids:
                person = self.session.get(Person, person_id)
                if not person:
                    continue
                remaining_current = self.session.execute(
                    select(Appointment).where(
                        Appointment.person_id == person_id,
                        Appointment.is_current.is_(True),
                    )
                ).scalars().first()
                if remaining_current is None:
                    person.is_current = False
                    person.last_seen_at = datetime.utcnow()
        return deactivated
