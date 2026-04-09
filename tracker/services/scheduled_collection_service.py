from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from datetime import date as date_type
import re
from typing import Any

import feedparser
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
from tracker.config import get_settings
from tracker.models import Alias, Appointment, CollectionSchedule, Office, Person, StatementParticipant, StatementSource
from tracker.utils.web import build_google_news_rss_url
from tracker.services.statements_service import StatementsService


USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"
DEFAULT_EVENT_DOMAINS = ["cna.com.tw", "president.gov.tw", "mofa.gov.tw"]
DEFAULT_TAIWAN_KEYWORDS = ["台灣", "臺灣", "Taiwan", "taiwan", "Taipei", "台海"]


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

    def create_event_keyword_schedule(
        self,
        *,
        name: str,
        person_scope: str,
        start_at: datetime,
        end_at: datetime,
        taiwan_keywords: list[str],
        domains: list[str],
        run_at: datetime,
        max_people: int | None = None,
        one_shot: bool = True,
        interval_minutes: int | None = None,
    ) -> CollectionSchedule:
        normalized_keywords = self._normalize_keywords(taiwan_keywords)
        normalized_domains = self._normalize_domains(domains)
        task = CollectionSchedule(
            name=name.strip() or "事件排程搜尋",
            enabled=True,
            entity_scope="events",
            person_scope=person_scope or "all_current",
            year=None,
            months_csv=None,
            interval_minutes=max(5, int(interval_minutes or 1440)),
            max_people=max_people if max_people and max_people > 0 else None,
            next_run_at=run_at,
            raw_payload={
                "created_from": "schedule_page_event_form_v1",
                "schedule_kind": "event_keyword_search_v1",
                "person_scope": person_scope or "all_current",
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "taiwan_keywords": normalized_keywords,
                "domains": normalized_domains,
                "one_shot": bool(one_shot),
                "interval_minutes": max(5, int(interval_minutes or 1440)),
            },
        )
        self.session.add(task)
        self.session.flush()
        return task

    def create_congress_legislation_schedule(
        self,
        *,
        name: str,
        start_at: datetime,
        end_at: datetime,
        taiwan_keywords: list[str],
        run_at: datetime,
        one_shot: bool = True,
        interval_minutes: int | None = None,
    ) -> CollectionSchedule:
        normalized_keywords = self._normalize_keywords(taiwan_keywords)
        task = CollectionSchedule(
            name=name.strip() or "Congress 涉台法案排程",
            enabled=True,
            entity_scope="legislation",
            person_scope="all_current",
            year=None,
            months_csv=None,
            interval_minutes=max(5, int(interval_minutes or 1440)),
            max_people=None,
            next_run_at=run_at,
            raw_payload={
                "created_from": "schedule_page_congress_form_v1",
                "schedule_kind": "congress_taiwan_legislation_search_v1",
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "taiwan_keywords": normalized_keywords,
                "one_shot": bool(one_shot),
                "interval_minutes": max(5, int(interval_minutes or 1440)),
            },
        )
        self.session.add(task)
        self.session.flush()
        return task

    def run_event_keyword_search_now(
        self,
        *,
        person_scope: str,
        start_at: datetime,
        end_at: datetime,
        taiwan_keywords: list[str],
        domains: list[str],
        max_people: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "person_scope": person_scope or "all_current",
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "taiwan_keywords": self._normalize_keywords(taiwan_keywords),
            "domains": self._normalize_domains(domains),
            "max_people": max_people if max_people and max_people > 0 else None,
        }
        return self._run_event_keyword_search(payload, schedule_id=None)

    def run_congress_legislation_search_now(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        taiwan_keywords: list[str],
    ) -> dict[str, Any]:
        payload = {
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "taiwan_keywords": self._normalize_keywords(taiwan_keywords),
        }
        return self._run_congress_taiwan_legislation_search(payload)

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
        raw_payload = dict(task.raw_payload or {})
        one_shot = bool(raw_payload.get("one_shot"))
        try:
            result = self._execute_task(task)
            task.last_status = "success"
            task.last_message = str(result)
            task.last_run_at = now
            if one_shot:
                task.enabled = False
                task.next_run_at = None
            else:
                task.next_run_at = now + timedelta(minutes=max(5, int(task.interval_minutes or 60)))
            return {"status": "success", "schedule_id": task.id, "result": result}
        except Exception as exc:
            task.last_status = "failed"
            task.last_message = f"{type(exc).__name__}: {exc}"
            task.last_run_at = now
            if one_shot:
                task.enabled = False
                task.next_run_at = None
            else:
                task.next_run_at = now + timedelta(minutes=max(5, int(task.interval_minutes or 60)))
            return {"status": "failed", "schedule_id": task.id, "error": f"{type(exc).__name__}: {exc}"}

    def _execute_task(self, task: CollectionSchedule) -> dict[str, Any]:
        raw_payload = dict(task.raw_payload or {})
        schedule_kind = str(raw_payload.get("schedule_kind") or "").strip()
        if schedule_kind == "event_keyword_search_v1":
            return {"events": self._run_event_keyword_search(raw_payload, schedule_id=task.id)}
        if schedule_kind == "congress_taiwan_legislation_search_v1":
            return {"legislation": self._run_congress_taiwan_legislation_search(raw_payload)}

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

    def _run_event_keyword_search(self, payload: dict[str, Any], schedule_id: int | None) -> dict[str, Any]:
        start_at = self._parse_datetime(payload.get("start_at")) or (datetime.utcnow() - timedelta(days=30))
        end_at = self._parse_datetime(payload.get("end_at")) or datetime.utcnow()
        if end_at <= start_at:
            end_at = start_at + timedelta(days=1)

        person_scope = str(payload.get("person_scope") or "all_current")
        max_people = payload.get("max_people")
        max_people_int = int(max_people) if isinstance(max_people, int) and max_people > 0 else None
        taiwan_keywords = self._normalize_keywords(payload.get("taiwan_keywords"))
        domains = self._normalize_domains(payload.get("domains"))
        people = self._list_people_by_scope(person_scope, max_people=max_people_int)
        if not people:
            return {
                "status": "success",
                "person_scope": person_scope,
                "people_scanned": 0,
                "found": 0,
                "created": 0,
                "updated": 0,
                "skipped_existing": 0,
                "queries": [],
            }

        aliases_map: dict[int, list[str]] = {}
        for person_id, full_name in people:
            aliases = self.session.execute(select(Alias.alias).where(Alias.person_id == person_id)).scalars().all()
            terms = [full_name] + [alias.strip() for alias in aliases if (alias or "").strip()]
            aliases_map[person_id] = list(dict.fromkeys(terms))

        created = 0
        updated = 0
        skipped_existing = 0
        found = 0
        result_items: list[dict[str, Any]] = []
        query_counters = {domain: {"domain": domain, "found": 0, "created": 0, "updated": 0, "skipped_existing": 0} for domain in domains}

        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(headers=headers, follow_redirects=True) as client:
            with httpx.Client(headers=headers, follow_redirects=True, verify=False) as insecure_client:
                for person_id, full_name in people:
                    person_terms = aliases_map.get(person_id, [full_name])
                    collected_hits = []
                    for domain in domains:
                        hits = self._discover_hits_for_domain(
                            domain=domain,
                            client=client,
                            insecure_client=insecure_client,
                            person_terms=person_terms,
                            taiwan_keywords=taiwan_keywords,
                            start_at=start_at,
                            end_at=end_at,
                        )
                        query_counters[domain]["found"] += len(hits)
                        collected_hits.extend(hits)
                    hits = dedupe_hits(collected_hits)
                    found += len(hits)
                    for hit in hits:
                        if self._statement_source_exists(person_id, hit.url):
                            skipped_existing += 1
                            counter = query_counters.get(hit.source)
                            if counter:
                                counter["skipped_existing"] += 1
                            if len(result_items) < 300:
                                result_items.append(
                                    {
                                        "person_id": person_id,
                                        "person_name": full_name,
                                        "source": hit.source,
                                        "title": (hit.title or hit.url).strip(),
                                        "url": hit.url,
                                        "published_date": hit.published_date,
                                        "status": "skipped_existing",
                                    }
                                )
                            continue
                        statement_payload = {
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
                            "parser_identity": "scheduled_event_keyword_search_v1",
                            "raw_payload": {
                                "seeded_from": "scheduled_event_keyword_search_v1",
                                "schedule_id": schedule_id,
                                "person_terms": person_terms,
                                "taiwan_keywords": taiwan_keywords,
                                "domains": domains,
                                "range_start_at": start_at.isoformat(),
                                "range_end_at": end_at.isoformat(),
                            },
                        }
                        is_created = self._ingest_statement_with_retry(statement_payload)
                        counter = query_counters.get(hit.source)
                        if is_created:
                            created += 1
                            if counter:
                                counter["created"] += 1
                            status_text = "created"
                        else:
                            updated += 1
                            if counter:
                                counter["updated"] += 1
                            status_text = "updated"
                        if len(result_items) < 300:
                            result_items.append(
                                {
                                    "person_id": person_id,
                                    "person_name": full_name,
                                    "source": hit.source,
                                    "title": (hit.title or hit.url).strip(),
                                    "url": hit.url,
                                    "published_date": hit.published_date,
                                    "status": status_text,
                                }
                            )

        return {
            "status": "success",
            "person_scope": person_scope,
            "people_scanned": len(people),
            "found": found,
            "created": created,
            "updated": updated,
            "skipped_existing": skipped_existing,
            "range_start_at": start_at.isoformat(),
            "range_end_at": end_at.isoformat(),
            "taiwan_keywords": taiwan_keywords,
            "domains": domains,
            "queries": list(query_counters.values()),
            "items": result_items,
        }

    def _run_congress_taiwan_legislation_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        if not settings.congress_api_key:
            return {
                "status": "skipped",
                "records_found": 0,
                "records_created": 0,
                "records_updated": 0,
                "errors": ["CONGRESS_API_KEY not configured"],
            }

        from tracker.jobs.sync_congress_taiwan import CONGRESSES, _fetch_congress_bills, _upsert_bills

        start_at = self._parse_datetime(payload.get("start_at")) or (datetime.utcnow() - timedelta(days=30))
        end_at = self._parse_datetime(payload.get("end_at")) or datetime.utcnow()
        if end_at <= start_at:
            end_at = start_at + timedelta(days=1)
        taiwan_keywords = self._normalize_legislation_keywords(self._normalize_keywords(payload.get("taiwan_keywords")))

        all_bills: list[dict[str, Any]] = []
        result_items: list[dict[str, Any]] = []
        scan_meta: list[dict[str, Any]] = []
        summary_cache: dict[str, str] = {}
        for congress in CONGRESSES:
            bills = asyncio.run(_fetch_congress_bills(congress, settings.congress_api_key))
            filtered: list[dict[str, Any]] = []
            for item in bills:
                title = str(item.get("title") or "")
                latest_action = item.get("latestAction") if isinstance(item.get("latestAction"), dict) else {}
                policy_area = item.get("policyArea") if isinstance(item.get("policyArea"), dict) else {}
                text = " ".join(
                    [
                        title,
                        str(latest_action.get("text") or ""),
                        str(policy_area.get("name") or ""),
                    ]
                )
                introduced_at = self._parse_iso_datetime_to_date(item.get("introducedDate"))
                updated_at = self._parse_iso_datetime_to_date(item.get("updateDate"))
                action_at = self._parse_iso_datetime_to_date(latest_action.get("actionDate"))
                in_range = any(
                    value and (start_at.date() <= value <= end_at.date())
                    for value in (introduced_at, action_at, updated_at)
                )
                if not in_range:
                    continue

                has_keyword = self._contains_any_keyword(text, taiwan_keywords)
                if not has_keyword:
                    bill_type = str(item.get("type") or "").strip().lower()
                    bill_number = str(item.get("number") or "").strip()
                    cache_key = f"{int(item.get('congress') or congress)}-{bill_type}-{bill_number}"
                    summary_text = summary_cache.get(cache_key)
                    if summary_text is None:
                        summary_text = self._fetch_congress_bill_summaries_text(
                            congress=int(item.get("congress") or congress),
                            bill_type=bill_type,
                            bill_number=bill_number,
                            api_key=settings.congress_api_key,
                        )
                        summary_cache[cache_key] = summary_text
                    has_keyword = self._contains_any_keyword(summary_text, taiwan_keywords)
                if not has_keyword:
                    continue

                updated_at = self._parse_iso_datetime_to_date(item.get("updateDate"))
                filtered.append(item)
                if len(result_items) < 300:
                    result_items.append(
                        {
                            "congress": int(item.get("congress") or congress),
                            "bill_type": str(item.get("type") or ""),
                            "bill_number": str(item.get("number") or ""),
                            "title": str(item.get("title") or ""),
                            "introduced_date": str(item.get("introducedDate") or ""),
                            "latest_action_date": str(latest_action.get("actionDate") or ""),
                            "update_date": str(item.get("updateDate") or ""),
                            "url": f"https://www.congress.gov/bill/{int(item.get('congress') or congress)}th-congress/{str(item.get('type') or '').lower()}-bill/{str(item.get('number') or '')}",
                        }
                    )
            all_bills.extend(filtered)
            scan_meta.append({"congress": congress, "fetched_total": len(bills), "matched": len(filtered)})

        db_result = _upsert_bills(all_bills)
        return {
            "status": "success" if not db_result.get("errors") else "partial_success",
            "records_found": len(all_bills),
            "records_created": int(db_result.get("created", 0)),
            "records_updated": int(db_result.get("updated", 0)),
            "range_start_at": start_at.isoformat(),
            "range_end_at": end_at.isoformat(),
            "taiwan_keywords": taiwan_keywords,
            "metadata": {
                "scans": scan_meta,
                "detail_enriched": int(db_result.get("detail_ok", 0)),
            },
            "items": result_items,
            "errors": list(db_result.get("errors") or [])[:20],
        }

    def _normalize_legislation_keywords(self, keywords: list[str]) -> list[str]:
        baseline = ["Taiwan", "taiwan", "Republic of China", "Taipei", "台灣", "臺灣"]
        merged = [str(item or "").strip() for item in list(keywords or []) + baseline]
        return [item for item in dict.fromkeys(merged) if item]

    def _fetch_congress_bill_summaries_text(
        self,
        *,
        congress: int,
        bill_type: str,
        bill_number: str,
        api_key: str,
    ) -> str:
        if not (congress and bill_type and bill_number and api_key):
            return ""
        url = f"https://api.congress.gov/v3/bill/{int(congress)}/{bill_type}/{bill_number}/summaries"
        params = {"api_key": api_key, "format": "json", "limit": 250}
        try:
            response = httpx.get(url, params=params, timeout=25.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            data = response.json() if response.content else {}
        except Exception:
            return ""
        summaries = data.get("summaries") if isinstance(data, dict) else None
        if not isinstance(summaries, list):
            return ""
        chunks: list[str] = []
        for item in summaries:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("summaryText") or "").strip()
            if text:
                chunks.append(re.sub(r"<[^>]+>", " ", text))
        return "\n".join(chunks)

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
        elif normalized_scope == "federal_legislators":
            stmt = stmt.where(Office.level == "federal", Office.branch == "legislative")
        elif normalized_scope == "federal_senators":
            stmt = stmt.where(Office.level == "federal", Office.branch == "legislative", Office.chamber == "senate")
        elif normalized_scope == "federal_house":
            stmt = stmt.where(Office.level == "federal", Office.branch == "legislative", Office.chamber == "house")
        elif normalized_scope == "state_officials":
            stmt = stmt.where(Office.level == "state", Office.branch == "executive")
        elif normalized_scope == "state_legislators":
            stmt = stmt.where(Office.level == "state", Office.branch == "legislative")
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

    def _normalize_keywords(self, keywords: list[str] | Any) -> list[str]:
        if not keywords:
            return list(DEFAULT_TAIWAN_KEYWORDS)
        if isinstance(keywords, str):
            items = [item.strip() for item in keywords.split(",")]
        else:
            items = [str(item).strip() for item in keywords]
        normalized = [item for item in items if item]
        if not normalized:
            return list(DEFAULT_TAIWAN_KEYWORDS)
        return list(dict.fromkeys(normalized))

    def _normalize_domains(self, domains: list[str] | Any) -> list[str]:
        if not domains:
            return list(DEFAULT_EVENT_DOMAINS)
        if isinstance(domains, str):
            items = [item.strip().lower() for item in domains.split(",")]
        else:
            items = [str(item).strip().lower() for item in domains]
        normalized = []
        for item in items:
            if not item:
                continue
            item = item.removeprefix("https://").removeprefix("http://").strip("/")
            if item:
                normalized.append(item)
        if not normalized:
            return list(DEFAULT_EVENT_DOMAINS)
        return list(dict.fromkeys(normalized))

    def _contains_any_keyword(self, text: str, keywords: list[str]) -> bool:
        normalized = (text or "").casefold()
        return any((kw or "").casefold() in normalized for kw in keywords if kw)

    def _parse_datetime(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def _parse_iso_datetime_to_date(self, value: Any) -> date_type | None:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            pass
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    def _statement_source_exists(self, person_id: int, source_url: str) -> bool:
        existing = self.session.execute(
            select(StatementSource.id)
            .join(StatementParticipant, StatementParticipant.statement_id == StatementSource.statement_id)
            .where(
                StatementParticipant.person_id == person_id,
                StatementSource.source_url == source_url,
            )
            .limit(1)
        ).scalar_one_or_none()
        return existing is not None

    def _discover_hits_for_domain(
        self,
        *,
        domain: str,
        client: httpx.Client,
        insecure_client: httpx.Client,
        person_terms: list[str],
        taiwan_keywords: list[str],
        start_at: datetime,
        end_at: datetime,
    ):
        start = start_at.date()
        end = end_at.date()
        if domain == "cna.com.tw":
            # CNA search endpoint only uses the first term as query seed.
            # Run multiple seeds (full name / aliases) then merge to avoid missing
            # Chinese-name-only coverage.
            aggregate = []
            ordered_terms = [term.strip() for term in person_terms if term and term.strip()]
            for idx, seed in enumerate(ordered_terms[:4]):
                reordered = [seed] + [term for term in ordered_terms if term != seed]
                try:
                    aggregate.extend(discover_cna(client, insecure_client, person_terms=reordered, start=start, end=end))
                except TypeError:
                    aggregate.extend(discover_cna(client, insecure_client, reordered, start, end))
                # Keep runtime bounded for large scopes.
                if idx >= 2 and len(aggregate) >= 20:
                    break
            return dedupe_hits(aggregate)
        if domain == "mofa.gov.tw":
            try:
                return discover_mofa(client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=30)
            except TypeError:
                return discover_mofa(client, insecure_client, person_terms, start, end, 30)
        if domain == "president.gov.tw":
            try:
                return discover_president(client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=30)
            except TypeError:
                return discover_president(client, insecure_client, person_terms, start, end, 30)
        return self._discover_generic_domain_hits(
            domain=domain,
            client=client,
            person_terms=person_terms,
            taiwan_keywords=taiwan_keywords,
            start_at=start_at,
            end_at=end_at,
        )

    def _discover_generic_domain_hits(
        self,
        *,
        domain: str,
        client: httpx.Client,
        person_terms: list[str],
        taiwan_keywords: list[str],
        start_at: datetime,
        end_at: datetime,
    ):
        query = f"({ ' OR '.join(person_terms[:6]) }) ({ ' OR '.join(taiwan_keywords[:6]) }) site:{domain} after:{start_at.date().isoformat()} before:{end_at.date().isoformat()}"
        feed = feedparser.parse(build_google_news_rss_url(query, hl="zh-TW", gl="TW", ceid="TW:zh-Hant"))
        hits = []
        for entry in getattr(feed, "entries", [])[:50]:
            title = str(getattr(entry, "title", "") or "").strip()
            summary = str(getattr(entry, "summary", "") or "").strip()
            merged_text = f"{title} {summary}"
            if not (self._contains_any_keyword(merged_text, person_terms) and self._contains_any_keyword(merged_text, taiwan_keywords)):
                continue
            published_struct = getattr(entry, "published_parsed", None)
            published_date = None
            if published_struct is not None:
                try:
                    published_date = datetime(*published_struct[:6]).date()
                except Exception:
                    published_date = None
            if published_date and not (start_at.date() <= published_date <= end_at.date()):
                continue
            link = str(getattr(entry, "link", "") or "").strip()
            resolved_link = self._resolve_possible_google_news_link(client, link)
            hits.append(
                type(
                    "DynamicEventHit",
                    (),
                    {
                        "source": domain,
                        "url": resolved_link or link,
                        "title": title or (resolved_link or link),
                        "published_date": published_date.isoformat() if published_date else None,
                        "excerpt": summary[:220],
                    },
                )()
            )
        return dedupe_hits(hits)

    def _resolve_possible_google_news_link(self, client: httpx.Client, link: str) -> str:
        if not link:
            return link
        if "news.google.com" not in link:
            return link
        try:
            response = client.get(link, timeout=20.0, follow_redirects=True)
            final_url = str(response.url)
            if final_url:
                return final_url
        except Exception:
            return link
        return link
