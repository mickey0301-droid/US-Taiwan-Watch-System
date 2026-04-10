from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import (
    Jurisdiction,
    Legislation,
    LegislationSource,
    LegislationSponsor,
    Person,
)
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService
from tracker.utils.hashing import sha256_text


class LegislationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.officials_service = OfficialsService(session)

    def upsert_legislation(self, payload: dict[str, Any]) -> tuple[Legislation, bool]:
        bill_slug = payload.get("bill_slug") or sha256_text(
            "|".join(
                [
                    payload.get("bill_number") or "",
                    payload.get("title") or "",
                    payload.get("level") or "",
                    payload.get("jurisdiction_name") or "",
                ]
            )
        )
        legislation = self.session.execute(select(Legislation).where(Legislation.bill_slug == bill_slug)).scalar_one_or_none()
        created = False
        jurisdiction_id = self._resolve_jurisdiction_id(payload.get("jurisdiction_name"), payload.get("level"))
        if not legislation:
            legislation = Legislation(
                title=payload["title"],
                bill_number=payload.get("bill_number"),
                bill_slug=bill_slug,
                legislation_type=payload.get("legislation_type"),
                level=payload["level"],
                jurisdiction_name=payload.get("jurisdiction_name"),
                jurisdiction_id=jurisdiction_id,
                chamber=payload.get("chamber"),
                summary=payload.get("summary"),
                status_text=payload.get("status_text"),
                introduced_date=payload.get("introduced_date"),
                last_action_date=payload.get("last_action_date"),
                source_url=payload["source_url"],
                source_type=payload["source_type"],
                parser_identity=payload.get("parser_identity"),
                relevance_score=payload.get("relevance_score", 1.0),
                is_taiwan_related=payload.get("is_taiwan_related", True),
                raw_payload=payload.get("raw_payload"),
            )
            self.session.add(legislation)
            self.session.flush()
            created = True
        else:
            incoming_rank = self.officials_service.source_sort_key(payload.get("source_type"), payload.get("source_url"))
            current_rank = self.officials_service.source_sort_key(legislation.source_type, legislation.source_url)
            if payload.get("title") and (not legislation.title or incoming_rank <= current_rank):
                legislation.title = payload.get("title") or legislation.title
            legislation.bill_number = payload.get("bill_number") or legislation.bill_number
            legislation.legislation_type = payload.get("legislation_type") or legislation.legislation_type
            legislation.jurisdiction_name = payload.get("jurisdiction_name") or legislation.jurisdiction_name
            legislation.jurisdiction_id = jurisdiction_id or legislation.jurisdiction_id
            legislation.chamber = payload.get("chamber") or legislation.chamber
            if payload.get("summary") and (not legislation.summary or incoming_rank <= current_rank):
                legislation.summary = payload.get("summary") or legislation.summary
            if payload.get("status_text") and (not legislation.status_text or incoming_rank <= current_rank):
                legislation.status_text = payload.get("status_text") or legislation.status_text
            legislation.introduced_date = payload.get("introduced_date") or legislation.introduced_date
            legislation.last_action_date = payload.get("last_action_date") or legislation.last_action_date
            if payload.get("source_url") and (not legislation.source_url or incoming_rank <= current_rank):
                legislation.source_url = payload.get("source_url") or legislation.source_url
            if payload.get("source_type") and (not legislation.source_type or incoming_rank <= current_rank):
                legislation.source_type = payload.get("source_type") or legislation.source_type
            legislation.parser_identity = payload.get("parser_identity") or legislation.parser_identity
            legislation.relevance_score = max(legislation.relevance_score or 0, payload.get("relevance_score", 0))
            legislation.raw_payload = self._merge_raw_payload(legislation.raw_payload, payload.get("raw_payload"))

        for source in payload.get("sources", []):
            self.ensure_legislation_source(legislation.id, source)
        for sponsor in payload.get("sponsors", []):
            self.ensure_legislation_sponsor(legislation.id, sponsor, payload)
        if payload.get("skipped_sponsors"):
            legislation.raw_payload = self._merge_raw_payload(
                legislation.raw_payload,
                {"skipped_sponsors": payload.get("skipped_sponsors")},
            )
        return legislation, created

    def ensure_legislation_source(self, legislation_id: int, payload: dict[str, Any]) -> None:
        existing = self.session.execute(
            select(LegislationSource).where(
                LegislationSource.legislation_id == legislation_id,
                LegislationSource.source_url == payload["source_url"],
            )
        ).scalar_one_or_none()
        if existing:
            return
        self.session.add(
            LegislationSource(
                legislation_id=legislation_id,
                source_url=payload["source_url"],
                source_type=payload["source_type"],
                source_title=payload.get("source_title"),
                parser_identity=payload.get("parser_identity"),
                raw_payload=payload.get("raw_payload"),
            )
        )

    def ensure_legislation_sponsor(self, legislation_id: int, sponsor_payload: dict[str, Any], legislation_payload: dict[str, Any]) -> bool:
        try:
            person = self._find_or_seed_person(sponsor_payload, legislation_payload)
        except InvalidPersonNameError as exc:
            skipped = list(legislation_payload.setdefault("skipped_sponsors", []))
            skipped.append(
                {
                    "full_name": sponsor_payload.get("full_name"),
                    "role": sponsor_payload.get("role", "sponsor"),
                    "reason": exc.category or "invalid_person_name",
                    "detail": exc.reason,
                }
            )
            legislation_payload["skipped_sponsors"] = skipped
            return False
        existing = self.session.execute(
            select(LegislationSponsor).where(
                LegislationSponsor.legislation_id == legislation_id,
                LegislationSponsor.person_id == person.id,
                LegislationSponsor.role == sponsor_payload.get("role", "sponsor"),
            )
        ).scalar_one_or_none()
        if existing:
            return False
        self.session.add(
            LegislationSponsor(
                legislation_id=legislation_id,
                person_id=person.id,
                role=sponsor_payload.get("role", "sponsor"),
                source_url=sponsor_payload.get("source_url"),
                source_type=sponsor_payload.get("source_type"),
            )
        )
        return True

    def list_years(self) -> list[int]:
        rows = self.session.execute(select(Legislation)).scalars().all()
        years = {
            (row.introduced_date or row.last_action_date).year
            for row in rows
            if (row.introduced_date or row.last_action_date)
        }
        return sorted(years, reverse=True)

    def list_months(self, year: int) -> list[int]:
        rows = self.session.execute(select(Legislation)).scalars().all()
        months = {
            (row.introduced_date or row.last_action_date).month
            for row in rows
            if (row.introduced_date or row.last_action_date) and (row.introduced_date or row.last_action_date).year == year
        }
        return sorted(months, reverse=True)

    def list_by_year_month(self, year: int, month: int) -> list[Legislation]:
        rows = self.session.execute(
            select(Legislation).order_by(Legislation.introduced_date.desc().nullslast(), Legislation.last_action_date.desc().nullslast(), Legislation.id.desc())
        ).scalars().all()
        return [
            row
            for row in rows
            if (row.introduced_date or row.last_action_date)
            and (row.introduced_date or row.last_action_date).year == year
            and (row.introduced_date or row.last_action_date).month == month
        ]

    def list_sources(self, legislation_id: int) -> list[LegislationSource]:
        return self.session.execute(
            select(LegislationSource).where(LegislationSource.legislation_id == legislation_id).order_by(LegislationSource.collected_at.asc())
        ).scalars().all()

    def list_sponsors(self, legislation_id: int) -> list[LegislationSponsor]:
        return self.session.execute(
            select(LegislationSponsor).where(LegislationSponsor.legislation_id == legislation_id).order_by(LegislationSponsor.id.asc())
        ).scalars().all()

    def _resolve_jurisdiction_id(self, name: str | None, level: str | None) -> int | None:
        if not name:
            return None
        jurisdiction_type = "state" if level == "state" else "country"
        return self.session.execute(
            select(Jurisdiction.id).where(Jurisdiction.name == name, Jurisdiction.type == jurisdiction_type)
        ).scalar_one_or_none()

    def _find_or_seed_person(self, sponsor_payload: dict[str, Any], legislation_payload: dict[str, Any]) -> Person:
        full_name = sponsor_payload["full_name"]
        person = self.officials_service.find_person(full_name)
        if person:
            if sponsor_payload.get("chinese_name"):
                self.officials_service.ensure_alias(
                    person.id,
                    sponsor_payload["chinese_name"],
                    sponsor_payload.get("source_url"),
                    sponsor_payload.get("source_type"),
                    alias_type="chinese_name",
                )
            return person

        if sponsor_payload.get("allow_seed_person") is False:
            raise InvalidPersonNameError(
                full_name,
                "Person not found; AI-extracted manual bill sponsors are not auto-created",
                "person_not_found",
            )

        role_title = sponsor_payload.get("role_title") or ("State Legislator" if legislation_payload.get("level") == "state" else "Legislator")
        raw_payload = {
            "seeded_from": "legislation_seed_v1",
            "office_title": role_title,
            "jurisdiction_name": legislation_payload.get("jurisdiction_name"),
        }
        person, _ = self.officials_service.upsert_person(
            {
                "full_name": full_name,
                "source_url": sponsor_payload.get("source_url") or legislation_payload["source_url"],
                "source_type": sponsor_payload.get("source_type") or legislation_payload["source_type"],
                "seed_source_type": "legislation",
                "profile_status": "seeded",
                "parser_identity": legislation_payload.get("parser_identity"),
                "verification_status": "unverified",
                "raw_payload": raw_payload,
            }
        )
        if sponsor_payload.get("chinese_name"):
            self.officials_service.ensure_alias(
                person.id,
                sponsor_payload["chinese_name"],
                sponsor_payload.get("source_url"),
                sponsor_payload.get("source_type"),
                alias_type="chinese_name",
            )
        return person

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
