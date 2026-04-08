from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from scripts.discover_restricted_source_events import (
    _month_bounds,
    dedupe_hits,
    discover_cna,
    discover_mofa,
    discover_president,
)
from tracker.models import Alias, Appointment, CollectionSchedule, Office, Person
from tracker.services.statements_service import StatementsService


USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"


class ScheduledCollectionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_schedules(self) -> list[CollectionSchedule]:
        return self.session.execute(select(CollectionSchedule).order_by(CollectionSchedule.updated_at.desc())).scalars().all()

    def create_schedule(
        self,
        name: str,
        entity_scope: str,
        person_scope: str,
        year: int | None,
        months: list[int],
        interval_minutes: int,
        max_people: int | None = None,
    ) -> CollectionSchedule:
        now = datetime.utcnow()
        task = CollectionSchedule(
            name=name.strip() or "Scheduled collection",
            enabled=True,
            entity_scope=entity_scope,
            person_scope=person_scope,
            year=year,
            months_csv=",".join(str(item) for item in sorted(set(months))) if months else None,
            interval_minutes=max(5, int(interval_minutes)),
            max_people=max_people if max_people and max_people > 0 else None,
            next_run_at=now,
            raw_payload={"created_from": "jobs_page_schedule_form_v1"},
        )
        self.session.add(task)
        self.session.flush()
        return task

    def set_enabled(self, schedule_id: int, enabled: bool) -> CollectionSchedule | None:
        task = self.session.get(CollectionSchedule, schedule_id)
        if not task:
            return None
        task.enabled = bool(enabled)
        if task.enabled and not task.next_run_at:
            task.next_run_at = datetime.utcnow()
        return task

    def run_due_schedules(self) -> list[dict[str, Any]]:
        now = datetime.utcnow()
        due = self.session.execute(
            select(CollectionSchedule).where(
                CollectionSchedule.enabled.is_(True),
                CollectionSchedule.next_run_at.is_not(None),
                CollectionSchedule.next_run_at <= now,
            )
        ).scalars().all()
        results: list[dict[str, Any]] = []
        for task in due:
            results.append(self.run_schedule(task.id))
        return results

    def run_schedule(self, schedule_id: int) -> dict[str, Any]:
        task = self.session.get(CollectionSchedule, schedule_id)
        if not task:
            return {"status": "failed", "error": "Schedule not found."}
        now = datetime.utcnow()
        try:
            result = self._execute_task(task)
            task.last_status = "success"
            task.last_message = str(result)
            task.last_run_at = now
            task.next_run_at = now + timedelta(minutes=max(5, int(task.interval_minutes or 60)))
            return {"status": "success", "schedule_id": task.id, "result": result}
        except Exception as exc:
            task.last_status = "failed"
            task.last_message = f"{type(exc).__name__}: {exc}"
            task.last_run_at = now
            task.next_run_at = now + timedelta(minutes=max(5, int(task.interval_minutes or 60)))
            return {"status": "failed", "schedule_id": task.id, "error": f"{type(exc).__name__}: {exc}"}

    def _execute_task(self, task: CollectionSchedule) -> dict[str, Any]:
        entity_scope = str(task.entity_scope or "all")
        result: dict[str, Any] = {}
        if entity_scope in {"all", "people"}:
            from tracker.jobs.sync_officials_wikipedia_only import run_sync_officials_wikipedia_only

            result["people"] = run_sync_officials_wikipedia_only()
        if entity_scope in {"all", "legislation"}:
            from tracker.jobs.sync_congress_taiwan import run_sync_congress_taiwan

            result["legislation"] = run_sync_congress_taiwan()
        if entity_scope in {"all", "events"}:
            result["events"] = self._run_restricted_event_scan(task)
        return result

    def _run_restricted_event_scan(self, task: CollectionSchedule) -> dict[str, Any]:
        months = self._months_from_task(task)
        year = int(task.year or datetime.utcnow().year)
        start, end = _month_bounds(year, months)
        people = self._list_people_by_scope(task.person_scope, max_people=task.max_people)
        if not people:
            return {"status": "success", "people_scanned": 0, "created": 0, "updated": 0, "discovered": 0}

        aliases_map: dict[int, list[str]] = {}
        for person_id, full_name in people:
            aliases = self.session.execute(select(Alias.alias).where(Alias.person_id == person_id)).scalars().all()
            terms = [full_name] + [alias.strip() for alias in aliases if (alias or "").strip()]
            aliases_map[person_id] = list(dict.fromkeys(terms))

        created = 0
        updated = 0
        discovered_count = 0

        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(headers=headers, follow_redirects=True) as client:
            with httpx.Client(headers=headers, follow_redirects=True, verify=False) as insecure_client:
                for person_id, full_name in people:
                    person_terms = aliases_map.get(person_id, [full_name])
                    hits = dedupe_hits(
                        discover_cna(client, insecure_client, person_terms=person_terms, start=start, end=end)
                        + discover_mofa(client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=30)
                        + discover_president(client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=30)
                    )
                    discovered_count += len(hits)
                    for hit in hits:
                        payload = {
                            "person_id": person_id,
                            "participant_ids": [person_id],
                            "title": (hit.title or hit.url).strip(),
                            "source_title": (hit.title or hit.url).strip(),
                            "date_published": datetime.fromisoformat(hit.published_date) if hit.published_date else None,
                            "source_url": hit.url,
                            "source_type": "official" if hit.source in {"mofa.gov.tw", "president.gov.tw"} else "media",
                            "statement_type": "statement",
                            "excerpt": hit.excerpt,
                            "full_text": hit.excerpt,
                            "raw_text": hit.excerpt,
                            "is_primary_source": hit.source in {"mofa.gov.tw", "president.gov.tw"},
                            "parser_identity": "scheduled_restricted_event_scan_v1",
                            "raw_payload": {
                                "seeded_from": "scheduled_restricted_event_scan_v1",
                                "schedule_id": task.id,
                                "person_terms": person_terms,
                                "search_year": year,
                                "search_months": months,
                            },
                        }
                        is_created = self._ingest_statement_with_retry(payload)
                        if is_created:
                            created += 1
                        else:
                            updated += 1
        return {
            "status": "success",
            "people_scanned": len(people),
            "discovered": discovered_count,
            "created": created,
            "updated": updated,
            "year": year,
            "months": months,
            "person_scope": task.person_scope,
        }

    def _ingest_statement_with_retry(self, payload: dict[str, Any], retries: int = 5) -> bool:
        for attempt in range(retries):
            try:
                service = StatementsService(self.session)
                _, is_created = service.ingest_statement(payload)
                self.session.flush()
                return bool(is_created)
            except OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempt == retries - 1:
                    raise
        return False

    def _months_from_task(self, task: CollectionSchedule) -> list[int]:
        csv = str(task.months_csv or "").strip()
        months: list[int] = []
        if csv:
            for item in csv.split(","):
                try:
                    month = int(item.strip())
                except ValueError:
                    continue
                if 1 <= month <= 12:
                    months.append(month)
        if not months:
            months = [datetime.utcnow().month]
        return sorted(set(months))

    def _list_people_by_scope(self, person_scope: str, max_people: int | None = None) -> list[tuple[int, str]]:
        stmt = (
            select(Person.id, Person.full_name)
            .join(Appointment, Appointment.person_id == Person.id)
            .join(Office, Office.id == Appointment.office_id)
            .where(Appointment.status == "current")
        )
        normalized_scope = str(person_scope or "all_federal")
        if normalized_scope == "federal_officials":
            stmt = stmt.where(Office.level == "federal", Office.branch == "executive")
        elif normalized_scope == "federal_senators":
            stmt = stmt.where(Office.level == "federal", Office.branch == "legislative", Office.chamber == "senate")
        elif normalized_scope == "federal_house":
            stmt = stmt.where(Office.level == "federal", Office.branch == "legislative", Office.chamber == "house")
        elif normalized_scope == "all_federal":
            stmt = stmt.where(Office.level == "federal")
        elif normalized_scope == "all_current":
            pass
        else:
            stmt = stmt.where(Office.level == "federal")

        rows = self.session.execute(stmt.order_by(Person.full_name.asc())).all()
        dedup: dict[int, tuple[int, str]] = {}
        for person_id, full_name in rows:
            if person_id not in dedup:
                dedup[person_id] = (person_id, full_name)
        people = list(dedup.values())
        if max_people and max_people > 0:
            people = people[:max_people]
        return people
