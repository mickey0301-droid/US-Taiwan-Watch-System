from __future__ import annotations

from datetime import datetime
import re

from sqlalchemy import func, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tracker.models import Statement, StatementParticipant, StatementSource
from tracker.services.dedupe_service import DedupeService
from tracker.services.relevance_service import RelevanceService
from tracker.utils.source_types import source_priority_key
from tracker.utils.web import domain_from_url


class StatementsService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.dedupe_service = DedupeService()
        self.relevance_service = RelevanceService()

    @staticmethod
    def _is_taiwan_event(statement: Statement) -> bool:
        text = "\n".join(
            [
                statement.title or "",
                statement.excerpt or "",
                statement.full_text or "",
                statement.raw_text or "",
            ]
        )
        if RelevanceService.is_taiwan_time_only_reference(text):
            return False
        if (statement.relevance_score or 0) > 0:
            return True
        payload = statement.raw_payload or {}
        if isinstance(payload, dict):
            seeded_from = str(payload.get("seeded_from", ""))
            if seeded_from.startswith("manual_taiwan_") or seeded_from == "manual_url_ingest_v1":
                return True
        lowered = text.lower()
        return "taiwan" in lowered or "台灣" in text

    def list_recent_statements(self, limit: int = 20) -> list[Statement]:
        return self.session.execute(select(Statement).order_by(Statement.date_collected.desc()).limit(limit)).scalars().all()

    def list_review_queue(self, limit: int | None = 2000) -> list[Statement]:
        stmt = select(Statement).order_by(func.coalesce(Statement.date_published, Statement.date_collected).desc(), Statement.id.desc())
        if limit and limit > 0:
            stmt = stmt.limit(limit)
        return self.session.execute(stmt).scalars().all()

    def list_recent_media_reports(self, person_id: int, limit: int = 20) -> list[Statement]:
        stmt = (
            select(Statement)
            .join(StatementParticipant, StatementParticipant.statement_id == Statement.id)
            .join(StatementSource, StatementSource.statement_id == Statement.id)
            .where(
                StatementParticipant.person_id == person_id,
                StatementSource.source_type.in_(["media", "cspan"]),
            )
            .order_by(Statement.date_published.desc().nullslast(), Statement.date_collected.desc())
        )
        statements = self._dedupe_statements_by_id(self.session.execute(stmt).scalars().all())
        return [statement for statement in statements if self._is_taiwan_event(statement)][:limit]

    def list_recent_social_posts(self, person_id: int, limit: int = 20) -> list[Statement]:
        stmt = (
            select(Statement)
            .join(StatementParticipant, StatementParticipant.statement_id == Statement.id)
            .join(StatementSource, StatementSource.statement_id == Statement.id)
            .where(
                StatementParticipant.person_id == person_id,
                StatementSource.source_type == "social",
            )
            .order_by(Statement.date_published.desc().nullslast(), Statement.date_collected.desc())
        )
        statements = self._dedupe_statements_by_id(self.session.execute(stmt).scalars().all())
        return [statement for statement in statements if self._is_taiwan_event(statement)][:limit]

    def list_recent_official_statements(self, person_id: int, limit: int = 20) -> list[Statement]:
        stmt = (
            select(Statement)
            .join(StatementParticipant, StatementParticipant.statement_id == Statement.id)
            .join(StatementSource, StatementSource.statement_id == Statement.id)
            .where(
                StatementParticipant.person_id == person_id,
                StatementSource.source_type == "official",
            )
            .order_by(Statement.date_published.desc().nullslast(), Statement.date_collected.desc())
        )
        statements = self._dedupe_statements_by_id(self.session.execute(stmt).scalars().all())
        return [statement for statement in statements if self._is_taiwan_event(statement)][:limit]

    @staticmethod
    def _dedupe_statements_by_id(statements: list[Statement]) -> list[Statement]:
        output: list[Statement] = []
        seen_ids: set[int] = set()
        for statement in statements:
            if statement.id in seen_ids:
                continue
            seen_ids.add(statement.id)
            output.append(statement)
        return output

    def list_recent_taiwan_statements(self, person_id: int, limit: int = 3) -> list[Statement]:
        stmt = (
            select(Statement)
            .join(StatementParticipant, StatementParticipant.statement_id == Statement.id)
            .where(StatementParticipant.person_id == person_id)
            .order_by(Statement.date_published.desc().nullslast(), Statement.date_collected.desc())
        )
        statements = self.session.execute(stmt).scalars().all()
        return [statement for statement in statements if self._is_taiwan_event(statement)][:limit]

    def list_statement_years(self, person_id: int) -> list[int]:
        statements = (
            self.session.execute(
                select(Statement)
                .join(StatementParticipant, StatementParticipant.statement_id == Statement.id)
                .where(StatementParticipant.person_id == person_id)
            )
            .scalars()
            .all()
        )
        years = {
            (statement.date_published or statement.date_collected).year
            for statement in statements
            if (statement.date_published or statement.date_collected) and self._is_taiwan_event(statement)
        }
        return sorted(years, reverse=True)

    def list_statements_by_year(self, person_id: int, year: int) -> list[Statement]:
        statements = (
            self.session.execute(
                select(Statement)
                .join(StatementParticipant, StatementParticipant.statement_id == Statement.id)
                .where(StatementParticipant.person_id == person_id)
                .order_by(Statement.date_published.desc().nullslast(), Statement.date_collected.desc())
            )
            .scalars()
            .all()
        )
        return [
            statement
            for statement in statements
            if (statement.date_published or statement.date_collected)
            and (statement.date_published or statement.date_collected).year == year
            and self._is_taiwan_event(statement)
        ]

    def update_review_status(self, statement_id: int, review_status: str) -> None:
        statement = self.session.get(Statement, statement_id)
        if statement:
            statement.review_status = review_status
            statement.updated_at = datetime.utcnow()

    def get_source_count(self, statement_id: int) -> int:
        stmt = select(func.count()).select_from(StatementSource).where(StatementSource.statement_id == statement_id)
        return self.session.scalar(stmt) or 0

    def list_sources_for_statement(self, statement_id: int) -> list[StatementSource]:
        stmt = select(StatementSource).where(StatementSource.statement_id == statement_id)
        sources = self.session.execute(stmt).scalars().all()
        return sorted(
            sources,
            key=lambda source: (
                not bool(source.is_primary),
                source_priority_key(source.source_type, source.source_url),
                -(source.collected_at.timestamp() if source.collected_at else 0),
                source.id,
            ),
        )

    def list_participants_for_statement(self, statement_id: int) -> list[StatementParticipant]:
        stmt = select(StatementParticipant).where(StatementParticipant.statement_id == statement_id).order_by(StatementParticipant.id.asc())
        return self.session.execute(stmt).scalars().all()

    @staticmethod
    def _extract_date_from_url(url: str) -> datetime | None:
        """Fall back to extracting publication date from known URL patterns.

        Handles:
        - CNA:       /news/aopl/YYYYMMDDNNNN.aspx
        - President: /en/news/press-release/YYYY/MM/DD/...
        - MOFA:      /en/pC_103160YYYYMMDD...  (less reliable, skip)
        """
        text = str(url or "")
        # CNA: 8-digit date prefix in filename before optional serial digits
        m = re.search(r"/(\d{8})\d{0,6}\.aspx", text, re.I)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y%m%d")
            except ValueError:
                pass
        # President Office: /YYYY/MM/DD/ path segment
        m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", text)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
        return None

    def ingest_statement(self, payload: dict) -> tuple[Statement, bool]:
        title = payload.get("title") or payload["source_url"]
        raw_text = payload.get("raw_text") or payload.get("full_text") or payload.get("excerpt") or ""
        score, hits = self.relevance_service.score_text(f"{title}\n{raw_text}")
        canonical_event_key = self.dedupe_service.build_event_key(
            person_id=payload.get("person_id"),
            title=title,
            raw_text=raw_text,
            date_published=payload.get("date_published"),
            statement_type=payload.get("statement_type"),
        )
        existing = self.session.execute(select(Statement).where(Statement.canonical_event_key == canonical_event_key)).scalar_one_or_none()

        # If date_published not provided, try to extract from source URL.
        date_published = payload.get("date_published") or self._extract_date_from_url(payload.get("source_url", ""))

        if existing:
            existing.date_collected = datetime.utcnow()
            existing.relevance_score = max(existing.relevance_score or 0.0, score)
            existing.matched_keywords = self._merge_hits(existing.matched_keywords, hits)
            existing.raw_payload = self._merge_raw_payload(existing.raw_payload, payload.get("raw_payload"))
            if payload.get("full_text") and (not existing.full_text or len(payload["full_text"]) > len(existing.full_text)):
                existing.full_text = payload["full_text"]
            if payload.get("excerpt") and (not existing.excerpt or len(payload["excerpt"]) > len(existing.excerpt)):
                existing.excerpt = payload["excerpt"]
            # Backfill date_published if missing, using URL extraction as fallback.
            if date_published and not existing.date_published:
                existing.date_published = date_published
            self._promote_preferred_source(existing, payload)
            self._ensure_statement_source(existing.id, payload)
            self._ensure_statement_participants(existing.id, payload)
            return existing, False

        statement_kwargs = {
            "person_id": payload.get("person_id"),
            "tracker_id": payload.get("tracker_id"),
            "tracker_target_id": payload.get("tracker_target_id"),
            "title": title,
            "canonical_event_key": canonical_event_key,
            "date_published": date_published,
            "date_collected": datetime.utcnow(),
            "source_url": payload["source_url"],
            "source_domain": domain_from_url(payload["source_url"]),
            "source_type": payload["source_type"],
            "event_source_preference": payload["source_type"],
            "statement_type": payload.get("statement_type"),
            "excerpt": payload.get("excerpt"),
            "full_text": payload.get("full_text"),
            "raw_text": raw_text,
            "relevance_score": score,
            # Review queue is disabled: any discovered event is directly included.
            "review_status": "confirmed",
            "dedupe_hash": self.dedupe_service.build_statement_hash(title, payload["source_url"], raw_text),
            "is_primary_source": payload.get("is_primary_source", True),
            "matched_keywords": {"hits": hits},
            "raw_payload": payload.get("raw_payload"),
        }
        statement = Statement(**statement_kwargs)
        self.session.add(statement)
        try:
            self.session.flush()
        except IntegrityError as exc:
            if "statements_pkey" not in str(exc):
                raise
            self.session.rollback()
            self._repair_postgres_sequence("statements", "id")
            statement = Statement(**statement_kwargs)
            self.session.add(statement)
            self.session.flush()
        self._ensure_statement_source(statement.id, payload)
        self._ensure_statement_participants(statement.id, payload)
        return statement, True

    def _repair_postgres_sequence(self, table_name: str, id_column: str) -> None:
        bind = self.session.get_bind()
        if not bind or bind.dialect.name != "postgresql":
            return
        self.session.execute(
            sql_text(
                f"SELECT setval(pg_get_serial_sequence('{table_name}', '{id_column}'), "
                f"COALESCE((SELECT MAX({id_column}) FROM {table_name}), 0), true)"
            )
        )

    def _promote_preferred_source(self, statement: Statement, payload: dict) -> None:
        current_rank = source_priority_key(statement.event_source_preference or statement.source_type, statement.source_url)
        new_rank = source_priority_key(payload["source_type"], payload["source_url"])
        if new_rank <= current_rank:
            statement.source_url = payload["source_url"]
            statement.source_domain = domain_from_url(payload["source_url"])
            statement.source_type = payload["source_type"]
            statement.event_source_preference = payload["source_type"]
            statement.is_primary_source = payload.get("is_primary_source", statement.is_primary_source)
            if payload.get("title"):
                statement.title = payload["title"]

    def _merge_hits(self, current: dict | None, new_hits: list[str]) -> dict:
        merged = set((current or {}).get("hits", []))
        merged.update(new_hits)
        return {"hits": sorted(merged)}

    def _merge_raw_payload(self, current: dict | None, incoming: dict | None) -> dict | None:
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

    def _ensure_statement_source(self, statement_id: int, payload: dict) -> None:
        existing = self.session.execute(
            select(StatementSource).where(StatementSource.statement_id == statement_id, StatementSource.source_url == payload["source_url"])
        ).scalar_one_or_none()
        if existing:
            existing.collected_at = datetime.utcnow()
            return
        self.session.add(
            StatementSource(
                statement_id=statement_id,
                source_url=payload["source_url"],
                source_type=payload["source_type"],
                source_title=payload.get("source_title") or payload.get("title"),
                parser_identity=payload.get("parser_identity"),
                is_primary=payload.get("is_primary_source", True),
                raw_payload=payload.get("raw_payload"),
            )
        )
        try:
            self.session.flush()
        except IntegrityError as exc:
            if "statement_sources_pkey" not in str(exc):
                raise
            self.session.rollback()
            self._repair_postgres_sequence("statement_sources", "id")
            self.session.add(
                StatementSource(
                    statement_id=statement_id,
                    source_url=payload["source_url"],
                    source_type=payload["source_type"],
                    source_title=payload.get("source_title") or payload.get("title"),
                    parser_identity=payload.get("parser_identity"),
                    is_primary=payload.get("is_primary_source", True),
                    raw_payload=payload.get("raw_payload"),
                )
            )
            self.session.flush()

    def _ensure_statement_participants(self, statement_id: int, payload: dict) -> None:
        participant_ids = list(payload.get("participant_ids") or [])
        if payload.get("person_id") and payload["person_id"] not in participant_ids:
            participant_ids.append(payload["person_id"])
        for participant_id in participant_ids:
            existing = self.session.execute(
                select(StatementParticipant).where(
                    StatementParticipant.statement_id == statement_id,
                    StatementParticipant.person_id == participant_id,
                )
            ).scalar_one_or_none()
            if existing:
                continue
            self.session.add(
                StatementParticipant(
                    statement_id=statement_id,
                    person_id=participant_id,
                    source_url=payload.get("source_url"),
                    source_type=payload.get("source_type"),
                )
            )
            try:
                self.session.flush()
            except IntegrityError as exc:
                if "statement_participants_pkey" not in str(exc):
                    raise
                self.session.rollback()
                self._repair_postgres_sequence("statement_participants", "id")
                self.session.add(
                    StatementParticipant(
                        statement_id=statement_id,
                        person_id=participant_id,
                        source_url=payload.get("source_url"),
                        source_type=payload.get("source_type"),
                    )
                )
                self.session.flush()
