from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from tracker.models import (
    Alias,
    Appointment,
    Legislation,
    LegislationSource,
    LegislationSponsor,
    Person,
    RosterMembership,
    Statement,
    StatementMention,
    StatementParticipant,
    StatementSource,
    Tracker,
)


class DedupeCleanupService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def cleanup_all(self) -> dict[str, Any]:
        people = self.cleanup_people_by_url()
        statements = self.cleanup_statements_by_url()
        legislation = self.cleanup_legislation_by_url()
        return {
            "status": "success",
            "people": people,
            "statements": statements,
            "legislation": legislation,
        }

    def cleanup_people_by_url(self) -> dict[str, int]:
        by_url: dict[str, list[Person]] = defaultdict(list)
        rows = self.session.execute(select(Person).order_by(Person.id.asc())).scalars().all()
        for person in rows:
            normalized = self._normalize_url(person.source_url or person.canonical_official_url)
            if normalized:
                by_url[normalized].append(person)

        merged_groups = 0
        merged_people = 0
        for _url, items in by_url.items():
            if len(items) <= 1:
                continue
            keeper = items[0]
            duplicates = items[1:]
            merged_groups += 1
            for duplicate in duplicates:
                self._merge_person(keeper=keeper, duplicate=duplicate)
                self.session.flush()
                merged_people += 1
        return {"merged_groups": merged_groups, "merged_people": merged_people}

    def cleanup_statements_by_url(self) -> dict[str, int]:
        by_url: dict[str, list[Statement]] = defaultdict(list)
        rows = self.session.execute(select(Statement).order_by(Statement.id.asc())).scalars().all()
        for statement in rows:
            normalized = self._normalize_url(statement.source_url)
            if normalized:
                by_url[normalized].append(statement)

        merged_groups = 0
        merged_statements = 0
        for _url, items in by_url.items():
            if len(items) <= 1:
                continue
            keeper = self._pick_statement_keeper(items)
            duplicates = [item for item in items if item.id != keeper.id]
            merged_groups += 1
            for duplicate in duplicates:
                self._merge_statement(keeper=keeper, duplicate=duplicate)
                self.session.flush()
                merged_statements += 1
        return {"merged_groups": merged_groups, "merged_statements": merged_statements}

    def cleanup_legislation_by_url(self) -> dict[str, int]:
        by_url: dict[str, list[Legislation]] = defaultdict(list)
        rows = self.session.execute(select(Legislation).order_by(Legislation.id.asc())).scalars().all()
        for bill in rows:
            normalized = self._normalize_url(bill.source_url)
            if normalized:
                by_url[normalized].append(bill)

        merged_groups = 0
        merged_legislation = 0
        for _url, items in by_url.items():
            if len(items) <= 1:
                continue
            keeper = self._pick_legislation_keeper(items)
            duplicates = [item for item in items if item.id != keeper.id]
            merged_groups += 1
            for duplicate in duplicates:
                self._merge_legislation(keeper=keeper, duplicate=duplicate)
                self.session.flush()
                merged_legislation += 1
        return {"merged_groups": merged_groups, "merged_legislation": merged_legislation}

    def _merge_person(self, keeper: Person, duplicate: Person) -> None:
        if duplicate.id == keeper.id:
            return
        now = datetime.utcnow()
        if not keeper.canonical_official_url and duplicate.canonical_official_url:
            keeper.canonical_official_url = duplicate.canonical_official_url
        if not keeper.source_url and duplicate.source_url:
            keeper.source_url = duplicate.source_url
        if not keeper.portrait_url and duplicate.portrait_url:
            keeper.portrait_url = duplicate.portrait_url
            keeper.portrait_source_url = duplicate.portrait_source_url
            keeper.portrait_source_type = duplicate.portrait_source_type
        if not keeper.social_profiles and duplicate.social_profiles:
            keeper.social_profiles = duplicate.social_profiles
        keeper.last_seen_at = now

        params = {"dup_id": duplicate.id, "keep_id": keeper.id}
        self.session.execute(
            text(
                """
                DELETE FROM aliases
                WHERE person_id = :dup_id
                  AND EXISTS (
                    SELECT 1 FROM aliases a2
                    WHERE a2.person_id = :keep_id AND a2.alias = aliases.alias
                  )
                """
            ),
            params,
        )
        self.session.execute(
            text(
                """
                DELETE FROM appointments
                WHERE person_id = :dup_id
                  AND EXISTS (
                    SELECT 1 FROM appointments a2
                    WHERE a2.person_id = :keep_id
                      AND a2.office_id = appointments.office_id
                      AND IFNULL(a2.jurisdiction_id, -1) = IFNULL(appointments.jurisdiction_id, -1)
                      AND a2.role_title = appointments.role_title
                      AND IFNULL(a2.start_date, '') = IFNULL(appointments.start_date, '')
                  )
                """
            ),
            params,
        )
        self.session.execute(
            text(
                """
                DELETE FROM statement_participants
                WHERE person_id = :dup_id
                  AND EXISTS (
                    SELECT 1 FROM statement_participants sp2
                    WHERE sp2.statement_id = statement_participants.statement_id
                      AND sp2.person_id = :keep_id
                  )
                """
            ),
            params,
        )
        self.session.execute(
            text(
                """
                DELETE FROM legislation_sponsors
                WHERE person_id = :dup_id
                  AND EXISTS (
                    SELECT 1 FROM legislation_sponsors ls2
                    WHERE ls2.legislation_id = legislation_sponsors.legislation_id
                      AND ls2.person_id = :keep_id
                      AND ls2.role = legislation_sponsors.role
                  )
                """
            ),
            params,
        )
        self.session.execute(
            text(
                """
                DELETE FROM roster_memberships
                WHERE person_id = :dup_id
                  AND EXISTS (
                    SELECT 1 FROM roster_memberships rm2
                    WHERE rm2.roster_id = roster_memberships.roster_id
                      AND rm2.person_id = :keep_id
                      AND IFNULL(rm2.office_id, -1) = IFNULL(roster_memberships.office_id, -1)
                      AND rm2.role_title = roster_memberships.role_title
                  )
                """
            ),
            params,
        )
        self.session.execute(text("UPDATE aliases SET person_id = :keep_id WHERE person_id = :dup_id"), params)
        self.session.execute(text("UPDATE appointments SET person_id = :keep_id WHERE person_id = :dup_id"), params)
        self.session.execute(text("UPDATE trackers SET person_id = :keep_id WHERE person_id = :dup_id"), params)
        self.session.execute(text("UPDATE statements SET person_id = :keep_id WHERE person_id = :dup_id"), params)
        self.session.execute(text("UPDATE statement_participants SET person_id = :keep_id WHERE person_id = :dup_id"), params)
        self.session.execute(text("UPDATE legislation_sponsors SET person_id = :keep_id WHERE person_id = :dup_id"), params)
        self.session.execute(text("UPDATE roster_memberships SET person_id = :keep_id WHERE person_id = :dup_id"), params)
        self.session.execute(delete(Person).where(Person.id == duplicate.id))

    def _pick_statement_keeper(self, items: list[Statement]) -> Statement:
        def score(item: Statement) -> tuple[int, int, int, int]:
            text_len = len((item.full_text or "") + (item.excerpt or ""))
            has_date = 1 if item.date_published else 0
            has_person = 1 if item.person_id else 0
            return (text_len, has_date, has_person, -item.id)

        return sorted(items, key=score, reverse=True)[0]

    def _merge_statement(self, keeper: Statement, duplicate: Statement) -> None:
        if duplicate.id == keeper.id:
            return
        if (duplicate.relevance_score or 0) > (keeper.relevance_score or 0):
            keeper.relevance_score = duplicate.relevance_score
        if duplicate.date_published and not keeper.date_published:
            keeper.date_published = duplicate.date_published
        if len(duplicate.full_text or "") > len(keeper.full_text or ""):
            keeper.full_text = duplicate.full_text
        if len(duplicate.excerpt or "") > len(keeper.excerpt or ""):
            keeper.excerpt = duplicate.excerpt
        if not keeper.person_id and duplicate.person_id:
            keeper.person_id = duplicate.person_id
        keeper.title = keeper.title or duplicate.title

        params = {"dup_id": duplicate.id, "keep_id": keeper.id}
        self.session.execute(
            text(
                """
                DELETE FROM statement_sources
                WHERE statement_id = :dup_id
                  AND EXISTS (
                    SELECT 1 FROM statement_sources ss2
                    WHERE ss2.statement_id = :keep_id
                      AND ss2.source_url = statement_sources.source_url
                  )
                """
            ),
            params,
        )
        self.session.execute(
            text(
                """
                DELETE FROM statement_participants
                WHERE statement_id = :dup_id
                  AND EXISTS (
                    SELECT 1 FROM statement_participants sp2
                    WHERE sp2.statement_id = :keep_id
                      AND sp2.person_id = statement_participants.person_id
                  )
                """
            ),
            params,
        )
        self.session.execute(text("UPDATE statement_sources SET statement_id = :keep_id WHERE statement_id = :dup_id"), params)
        self.session.execute(text("UPDATE statement_participants SET statement_id = :keep_id WHERE statement_id = :dup_id"), params)
        self.session.execute(text("UPDATE statement_mentions SET statement_id = :keep_id WHERE statement_id = :dup_id"), params)
        self.session.execute(delete(Statement).where(Statement.id == duplicate.id))

    def _pick_legislation_keeper(self, items: list[Legislation]) -> Legislation:
        def score(item: Legislation) -> tuple[int, int, int]:
            summary_len = len(item.summary or "")
            has_bill_number = 1 if item.bill_number else 0
            has_chamber = 1 if item.chamber else 0
            return (summary_len, has_bill_number, has_chamber)

        return sorted(items, key=score, reverse=True)[0]

    def _merge_legislation(self, keeper: Legislation, duplicate: Legislation) -> None:
        if duplicate.id == keeper.id:
            return
        keeper.title = keeper.title or duplicate.title
        keeper.bill_number = keeper.bill_number or duplicate.bill_number
        keeper.legislation_type = keeper.legislation_type or duplicate.legislation_type
        keeper.level = keeper.level or duplicate.level
        keeper.jurisdiction_name = keeper.jurisdiction_name or duplicate.jurisdiction_name
        keeper.jurisdiction_id = keeper.jurisdiction_id or duplicate.jurisdiction_id
        keeper.chamber = keeper.chamber or duplicate.chamber
        if len(duplicate.summary or "") > len(keeper.summary or ""):
            keeper.summary = duplicate.summary
        keeper.status_text = keeper.status_text or duplicate.status_text
        keeper.introduced_date = keeper.introduced_date or duplicate.introduced_date
        keeper.last_action_date = keeper.last_action_date or duplicate.last_action_date
        keeper.source_url = keeper.source_url or duplicate.source_url
        keeper.source_type = keeper.source_type or duplicate.source_type

        params = {"dup_id": duplicate.id, "keep_id": keeper.id}
        self.session.execute(
            text(
                """
                DELETE FROM legislation_sources
                WHERE legislation_id = :dup_id
                  AND EXISTS (
                    SELECT 1 FROM legislation_sources ls2
                    WHERE ls2.legislation_id = :keep_id
                      AND ls2.source_url = legislation_sources.source_url
                  )
                """
            ),
            params,
        )
        self.session.execute(
            text(
                """
                DELETE FROM legislation_sponsors
                WHERE legislation_id = :dup_id
                  AND EXISTS (
                    SELECT 1 FROM legislation_sponsors lsp2
                    WHERE lsp2.legislation_id = :keep_id
                      AND lsp2.person_id = legislation_sponsors.person_id
                      AND lsp2.role = legislation_sponsors.role
                  )
                """
            ),
            params,
        )
        self.session.execute(text("UPDATE legislation_sources SET legislation_id = :keep_id WHERE legislation_id = :dup_id"), params)
        self.session.execute(text("UPDATE legislation_sponsors SET legislation_id = :keep_id WHERE legislation_id = :dup_id"), params)
        self.session.execute(delete(Legislation).where(Legislation.id == duplicate.id))

    def _normalize_url(self, value: str | None) -> str:
        if not value:
            return ""
        parsed = urlparse(str(value).strip())
        if not parsed.scheme or not parsed.netloc:
            return ""
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"
