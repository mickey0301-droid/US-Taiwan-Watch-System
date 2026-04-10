from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import feedparser
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from scripts.discover_restricted_source_events import discover_cna, discover_mofa, discover_president
from tracker.models import Alias, Person, StatementParticipant, StatementSource, SyncRun
from tracker.services.relevance_service import RelevanceService
from tracker.services.statements_service import StatementsService
from tracker.utils.web import build_google_news_rss_url, domain_from_url, parse_datetime


DEFAULT_DOMAINS = ["cna.com.tw", "president.gov.tw", "mofa.gov.tw"]
DEFAULT_TAIWAN_KEYWORDS = ["台灣", "臺灣", "Taiwan"]
DEFAULT_DAILY_TIME = "09:00"
DEFAULT_LOOKBACK_DAYS = 30
MONITOR_KEY = "taiwan_event_monitor"
PARSER_IDENTITY = "person_taiwan_monitor_v1"


@dataclass
class MonitorRunResult:
    person_id: int
    person_name: str
    found: int = 0
    created: int = 0
    updated: int = 0
    skipped_existing: int = 0
    queries: list[dict[str, Any]] | None = None
    ok: bool = True
    error: str | None = None


class PersonTaiwanEventMonitorService:
    def __init__(self, session: Session, timezone: str = "Asia/Taipei") -> None:
        self.session = session
        self.statements_service = StatementsService(session)
        self.relevance_service = RelevanceService()
        self.timezone = timezone
        self._tz = ZoneInfo(timezone)
        self._http_headers = {"User-Agent": "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"}

    def default_config_for_person(self, person: Person, chinese_aliases: list[str] | None = None) -> dict[str, Any]:
        if self._is_trump_name(person.full_name):
            keywords = [person.full_name]
            if person.family_name and person.family_name not in keywords:
                keywords.append(person.family_name)
            for alias in list(chinese_aliases or []):
                if alias and alias not in keywords:
                    keywords.append(alias)
        else:
            keywords = self._filter_person_keywords(person, [person.full_name])
            # Also append Chinese aliases for all people, not just Trump.
            for alias in list(chinese_aliases or []):
                alias = str(alias or "").strip()
                if alias and alias not in keywords:
                    keywords.append(alias)
        return {
            "enabled": False,
            "person_keywords": keywords,
            "taiwan_keywords": list(DEFAULT_TAIWAN_KEYWORDS),
            "domains": list(DEFAULT_DOMAINS),
            "daily_time": DEFAULT_DAILY_TIME,
            "lookback_days": DEFAULT_LOOKBACK_DAYS,
            "last_run_at": None,
            "last_result": None,
            "runs": [],
        }

    @staticmethod
    def _person_payload(person: Person) -> dict[str, Any]:
        return dict(person.raw_payload) if isinstance(person.raw_payload, dict) else {}

    def get_person_monitor_config(self, person: Person, chinese_aliases: list[str] | None = None) -> dict[str, Any]:
        payload = self._person_payload(person)
        config = payload.get(MONITOR_KEY)
        if not isinstance(config, dict):
            config = self.default_config_for_person(person, chinese_aliases=chinese_aliases)
        config.setdefault("enabled", False)
        config.setdefault("person_keywords", [person.full_name])
        config.setdefault("taiwan_keywords", list(DEFAULT_TAIWAN_KEYWORDS))
        config.setdefault("domains", list(DEFAULT_DOMAINS))
        config.setdefault("daily_time", DEFAULT_DAILY_TIME)
        config.setdefault("lookback_days", DEFAULT_LOOKBACK_DAYS)
        config.setdefault("include_global_news", False)
        config.setdefault("runs", [])
        return config

    def save_person_monitor_config(
        self,
        person: Person,
        *,
        enabled: bool,
        person_keywords: list[str],
        taiwan_keywords: list[str],
        domains: list[str],
        daily_time: str,
        lookback_days: int | None = None,
    ) -> dict[str, Any]:
        payload = self._person_payload(person)
        config = self.get_person_monitor_config(person)
        config["enabled"] = bool(enabled)
        cleaned_person_keywords = self._clean_keywords(person_keywords)
        config["person_keywords"] = cleaned_person_keywords or [str(person.full_name or "").strip()]
        config["taiwan_keywords"] = self._clean_keywords(taiwan_keywords) or list(DEFAULT_TAIWAN_KEYWORDS)
        config["domains"] = self._clean_domains(domains) or list(DEFAULT_DOMAINS)
        config["daily_time"] = self._normalize_daily_time(daily_time)
        config["lookback_days"] = self._normalize_lookback_days(lookback_days)
        config["include_global_news"] = False
        payload[MONITOR_KEY] = config
        person.raw_payload = payload
        flag_modified(person, "raw_payload")
        person.last_seen_at = datetime.utcnow()
        self.session.flush()
        return config

    def run_due_monitors(self) -> dict[str, Any]:
        queue_result = self.run_queued_manual_runs(limit=2)
        now_utc = datetime.utcnow()
        now_local = datetime.now(self._tz)
        people = self.session.execute(select(Person).where(Person.is_current.is_(True))).scalars().all()
        due_people: list[Person] = []
        for person in people:
            config = self.get_person_monitor_config(person)
            if not bool(config.get("enabled")):
                continue
            if self._is_due_today(config, now_local, now_utc):
                due_people.append(person)

        results: list[dict[str, Any]] = []
        for person in due_people:
            result = self.run_for_person(person.id, trigger="scheduled")
            results.append(
                {
                    "person_id": result.person_id,
                    "person_name": result.person_name,
                    "found": result.found,
                    "created": result.created,
                    "updated": result.updated,
                    "ok": result.ok,
                    "error": result.error,
                }
            )
        return {
            "status": "success",
            "queued_processed": int(queue_result.get("processed", 0)),
            "due_count": len(due_people),
            "results": results,
            "queue_results": queue_result.get("results", []),
        }

    def enqueue_manual_run(self, person_id: int) -> SyncRun:
        person = self.session.get(Person, int(person_id))
        if not person:
            raise ValueError("Person not found")
        run = SyncRun(
            job_name=f"person_monitor_manual_{person.id}",
            job_type="person_monitor_manual",
            source_name=person.full_name,
            status="queued",
            records_found=0,
            records_created=0,
            records_updated=0,
            records_deactivated=0,
            meta={"person_id": person.id},
        )
        self.session.add(run)
        try:
            self.session.flush()
        except IntegrityError as exc:
            # PostgreSQL sequence drift can happen after bulk migration/import.
            # Repair sequence and retry once for sync_runs inserts.
            if "sync_runs_pkey" not in str(exc):
                raise
            self.session.rollback()
            self._repair_postgres_sequence("sync_runs", "id")
            run = SyncRun(
                job_name=f"person_monitor_manual_{person.id}",
                job_type="person_monitor_manual",
                source_name=person.full_name,
                status="queued",
                records_found=0,
                records_created=0,
                records_updated=0,
                records_deactivated=0,
                meta={"person_id": person.id},
            )
            self.session.add(run)
            self.session.flush()
        return run

    def get_latest_manual_run(self, person_id: int) -> SyncRun | None:
        candidates = (
            self.session.execute(
                select(SyncRun)
                .where(SyncRun.job_type == "person_monitor_manual")
                .order_by(SyncRun.created_at.desc())
                .limit(50)
            )
            .scalars()
            .all()
        )
        target = int(person_id)
        for run in candidates:
            if int((run.meta or {}).get("person_id") or 0) == target:
                return run
        return None

    def run_queued_manual_runs(self, limit: int = 2) -> dict[str, Any]:
        queued_runs = (
            self.session.execute(
                select(SyncRun)
                .where(
                    SyncRun.job_type == "person_monitor_manual",
                    SyncRun.status == "queued",
                )
                .order_by(SyncRun.created_at.asc())
                .limit(max(1, int(limit)))
            )
            .scalars()
            .all()
        )
        results: list[dict[str, Any]] = []
        processed = 0
        for run in queued_runs:
            processed += 1
            run.status = "running"
            run.started_at = datetime.utcnow()
            run.error_message = None
            self.session.flush()
            person_id = int((run.meta or {}).get("person_id") or 0)
            try:
                result = self.run_for_person(person_id, trigger="manual_queue")
                run.ended_at = datetime.utcnow()
                run.status = "success" if result.ok else "failed"
                run.records_found = int(result.found or 0)
                run.records_created = int(result.created or 0)
                run.records_updated = int(result.updated or 0)
                if not result.ok:
                    run.error_message = str(result.error or "manual queue run failed")
                run.meta = {**dict(run.meta or {}), "result": {"ok": result.ok, "found": result.found, "created": result.created, "updated": result.updated}}
                results.append(
                    {
                        "run_id": run.id,
                        "person_id": person_id,
                        "ok": bool(result.ok),
                        "found": int(result.found or 0),
                        "created": int(result.created or 0),
                        "updated": int(result.updated or 0),
                        "error": result.error,
                    }
                )
            except Exception as exc:
                run.ended_at = datetime.utcnow()
                run.status = "failed"
                run.error_message = f"{type(exc).__name__}: {exc}"
                results.append(
                    {
                        "run_id": run.id,
                        "person_id": person_id,
                        "ok": False,
                        "error": run.error_message,
                    }
                )
            self.session.flush()
        return {"processed": processed, "results": results}

    def run_for_person(self, person_id: int, trigger: str = "manual") -> MonitorRunResult:
        person = self.session.get(Person, int(person_id))
        if not person:
            return MonitorRunResult(person_id=person_id, person_name="", ok=False, error="Person not found")

        config = self.get_person_monitor_config(person)
        all_person_keywords = self._clean_keywords(config.get("person_keywords") or [])
        if not all_person_keywords:
            fallback_name = str(person.full_name or "").strip()
            if fallback_name:
                all_person_keywords = [fallback_name]
        # Augment with DB aliases (includes Chinese names) so monitors work
        # even when person_keywords in config only has English names.
        try:
            db_aliases = self.session.execute(
                select(Alias.alias).where(Alias.person_id == person.id)
            ).scalars().all()
            for _alias in db_aliases:
                _alias_str = str(_alias or "").strip()
                if _alias_str and _alias_str not in all_person_keywords:
                    all_person_keywords.append(_alias_str)
        except Exception:
            pass
        # Keep an English full-name subset for Google-style query quality,
        # but preserve full keyword set (including Chinese aliases) for
        # direct-site search and in-article matching.
        person_keywords = self._filter_person_keywords(person, list(all_person_keywords))
        if not person_keywords:
            person_keywords = list(all_person_keywords)
        taiwan_keywords = self._clean_keywords(config.get("taiwan_keywords") or [])
        domains = self._clean_domains(config.get("domains") or [])
        lookback_days = self._normalize_lookback_days(config.get("lookback_days"))
        include_global_news = bool(config.get("include_global_news", False))
        if not all_person_keywords:
            return MonitorRunResult(person_id=person.id, person_name=person.full_name, ok=False, error="Missing person keywords")
        if not taiwan_keywords:
            taiwan_keywords = list(DEFAULT_TAIWAN_KEYWORDS)
        if not domains:
            domains = list(DEFAULT_DOMAINS)

        started_at = datetime.utcnow()
        query_logs: list[dict[str, Any]] = []
        total_found = 0
        total_created = 0
        total_updated = 0
        total_skipped_existing = 0
        article_text_cache: dict[str, str] = {}

        with httpx.Client(headers=self._http_headers, follow_redirects=True, timeout=25.0) as client:
            with httpx.Client(headers=self._http_headers, follow_redirects=True, timeout=25.0, verify=False) as insecure_client:
                for domain in domains:
                    query = self.build_query(person_keywords, taiwan_keywords, domain=domain)
                    items = self._collect_domain_items(
                        client=client,
                        insecure_client=insecure_client,
                        domain=domain,
                        query=query,
                        person_keywords=person_keywords,
                        all_person_keywords=all_person_keywords,
                        lookback_days=lookback_days,
                    )
                    found = 0
                    created = 0
                    updated = 0
                    skipped_existing = 0
                    for item in items:
                        if not self._within_lookback(item.get("published_at"), lookback_days):
                            continue
                        text = self._merge_text(item.get("title"), item.get("summary"))
                        matched_person = self._matched_keywords(text, all_person_keywords)
                        matched_taiwan = self._matched_keywords(text, taiwan_keywords)
                        if not matched_person or not matched_taiwan:
                            if bool(item.get("query_enforced_match")):
                                # For source-query-matched items, keep Taiwan keyword
                                # strict in article text, while allowing person
                                # keyword fallback to the configured primary name.
                                if not matched_taiwan:
                                    source_url_for_check = str(item.get("url") or "").strip()
                                    if source_url_for_check:
                                        full_text = self._fetch_full_article_text(client, source_url_for_check, article_text_cache)
                                        if full_text:
                                            matched_taiwan = self._matched_keywords(
                                                self._merge_text(item.get("title"), full_text),
                                                taiwan_keywords,
                                            )
                                if not matched_taiwan:
                                    continue
                                if not matched_person and all_person_keywords:
                                    matched_person = [all_person_keywords[0]]
                            else:
                                continue
                        if self.relevance_service.is_taiwan_time_only_reference(text):
                            continue
                        found += 1

                        source_url = str(item.get("url") or "").strip()
                        if not source_url:
                            continue
                        if self._has_existing_person_source_url(person.id, source_url):
                            skipped_existing += 1
                            continue
                        source_domain = domain_from_url(source_url)
                        source_type = "official" if source_domain in {"president.gov.tw", "mofa.gov.tw"} else "media"

                        payload = {
                            "person_id": person.id,
                            "participant_ids": [person.id],
                            "title": str(item.get("title") or source_url),
                            "source_title": str(item.get("title") or source_url),
                            "date_published": item.get("published_at"),
                            "source_url": source_url,
                            "source_type": source_type,
                            "statement_type": "statement",
                            "excerpt": str(item.get("summary") or "")[:1000],
                            "full_text": str(item.get("summary") or "")[:5000],
                            "raw_text": str(item.get("summary") or ""),
                            "is_primary_source": source_type == "official",
                            "parser_identity": PARSER_IDENTITY,
                            "raw_payload": {
                                "seeded_from": PARSER_IDENTITY,
                                "monitor_trigger": trigger,
                                "monitor_domain": domain,
                                "monitor_query": query,
                                "monitor_lookback_days": lookback_days,
                                "matched_person_keywords": matched_person,
                                "matched_taiwan_keywords": matched_taiwan,
                            },
                        }
                        _, is_created = self.statements_service.ingest_statement(payload)
                        if is_created:
                            created += 1
                        else:
                            updated += 1

                    total_found += found
                    total_created += created
                    total_updated += updated
                    total_skipped_existing += skipped_existing
                    query_logs.append(
                        {
                            "domain": domain,
                            "query": query,
                            "lookback_days": lookback_days,
                            "items_found": found,
                            "items_added": created,
                            "items_updated": updated,
                            "items_skipped_existing": skipped_existing,
                        }
                    )

            if include_global_news:
                global_query = self.build_query(person_keywords, taiwan_keywords, domain=None)
                global_items = self._collect_rss_items(
                    client,
                    rss_url=build_google_news_rss_url(query=global_query, hl="zh-TW", gl="TW", ceid="TW:zh-Hant"),
                    domain="",
                )
                found = 0
                created = 0
                updated = 0
                skipped_existing = 0
                for item in global_items:
                    if not self._within_lookback(item.get("published_at"), lookback_days):
                        continue
                    source_url = str(item.get("url") or "").strip()
                    if not source_url:
                        continue
                    if self._has_existing_person_source_url(person.id, source_url):
                        skipped_existing += 1
                        continue
                    text = self._merge_text(item.get("title"), item.get("summary"))
                    matched_person = self._matched_keywords(text, all_person_keywords)
                    matched_taiwan = self._matched_keywords(text, taiwan_keywords)
                    if not matched_taiwan:
                        source_url_for_check = str(item.get("url") or "").strip()
                        if source_url_for_check:
                            full_text = self._fetch_full_article_text(client, source_url_for_check, article_text_cache)
                            if full_text:
                                matched_taiwan = self._matched_keywords(
                                    self._merge_text(item.get("title"), full_text),
                                    taiwan_keywords,
                                )
                        if not matched_taiwan:
                            continue
                    if not matched_person and all_person_keywords:
                        matched_person = [all_person_keywords[0]]
                    if self.relevance_service.is_taiwan_time_only_reference(text):
                        continue
                    found += 1
                    source_domain = domain_from_url(source_url)
                    source_type = "official" if source_domain in {"president.gov.tw", "mofa.gov.tw"} else "media"
                    payload = {
                        "person_id": person.id,
                        "participant_ids": [person.id],
                        "title": str(item.get("title") or source_url),
                        "source_title": str(item.get("title") or source_url),
                        "date_published": item.get("published_at"),
                        "source_url": source_url,
                        "source_type": source_type,
                        "statement_type": "statement",
                        "excerpt": str(item.get("summary") or "")[:1000],
                        "full_text": str(item.get("summary") or "")[:5000],
                        "raw_text": str(item.get("summary") or ""),
                        "is_primary_source": source_type == "official",
                        "parser_identity": PARSER_IDENTITY,
                        "raw_payload": {
                            "seeded_from": PARSER_IDENTITY,
                            "monitor_trigger": trigger,
                            "monitor_domain": "__all__",
                            "monitor_query": global_query,
                            "monitor_lookback_days": lookback_days,
                            "matched_person_keywords": matched_person,
                            "matched_taiwan_keywords": matched_taiwan,
                        },
                    }
                    _, is_created = self.statements_service.ingest_statement(payload)
                    if is_created:
                        created += 1
                    else:
                        updated += 1
                total_found += found
                total_created += created
                total_updated += updated
                total_skipped_existing += skipped_existing
                query_logs.append(
                    {
                        "domain": "__all__",
                        "query": global_query,
                        "lookback_days": lookback_days,
                        "items_found": found,
                        "items_added": created,
                        "items_updated": updated,
                        "items_skipped_existing": skipped_existing,
                    }
                )

        finished_at = datetime.utcnow()
        run_record = {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "trigger": trigger,
            "ok": True,
            "found": total_found,
            "created": total_created,
            "updated": total_updated,
            "queries": query_logs,
            "message": "completed",
        }
        self._save_run_record(person, run_record)
        self.session.flush()

        return MonitorRunResult(
            person_id=person.id,
            person_name=person.full_name,
            found=total_found,
            created=total_created,
            updated=total_updated,
            skipped_existing=total_skipped_existing,
            queries=query_logs,
            ok=True,
        )

    def _save_run_record(self, person: Person, run_record: dict[str, Any]) -> None:
        payload = self._person_payload(person)
        config = self.get_person_monitor_config(person)
        runs = list(config.get("runs") or [])
        runs.insert(0, run_record)
        config["runs"] = runs[:30]
        config["last_run_at"] = run_record.get("finished_at")
        config["last_result"] = {
            "found": run_record.get("found", 0),
            "created": run_record.get("created", 0),
            "updated": run_record.get("updated", 0),
            "ok": run_record.get("ok", False),
        }
        payload[MONITOR_KEY] = config
        person.raw_payload = payload
        flag_modified(person, "raw_payload")
        person.last_seen_at = datetime.utcnow()

    def _collect_rss_items(self, client: httpx.Client, rss_url: str, domain: str) -> list[dict[str, Any]]:
        try:
            response = client.get(rss_url, follow_redirects=True, timeout=25.0)
            response.raise_for_status()
            parsed = feedparser.parse(response.text)
        except Exception:
            # Fallback to feedparser URL fetch if direct fetch fails.
            parsed = feedparser.parse(rss_url)
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for entry in parsed.entries[:50]:
            raw_link = str(getattr(entry, "link", "") or "").strip()
            url = self._resolve_entry_url(client, raw_link)
            url_key = self._url_key(url) or self._url_key(raw_link)
            if url_key and url_key in seen:
                continue
            if url_key:
                seen.add(url_key)
            if domain and domain not in domain_from_url(url):
                continue
            title = str(getattr(entry, "title", "") or "").strip()
            summary = str(getattr(entry, "summary", "") or getattr(entry, "description", "") or "").strip()
            published_at = parse_datetime(getattr(entry, "published", None)) or parse_datetime(getattr(entry, "updated", None))
            items.append(
                {
                    "url": url or raw_link,
                    "title": title,
                    "summary": self._strip_html(summary),
                    "published_at": published_at,
                    "query_enforced_match": True,
                }
            )
        return items

    def _collect_domain_items(
        self,
        client: httpx.Client,
        insecure_client: httpx.Client,
        domain: str,
        query: str,
        person_keywords: list[str],
        all_person_keywords: list[str],
        lookback_days: int,
    ) -> list[dict[str, Any]]:
        normalized_domain = str(domain or "").strip().lower()
        if normalized_domain in {"cna.com.tw", "mofa.gov.tw", "president.gov.tw"}:
            return self._collect_direct_site_items(
                client,
                insecure_client,
                normalized_domain,
                all_person_keywords or person_keywords,
                lookback_days,
                query=query,
            )
        rss_url = build_google_news_rss_url(query=query, hl="zh-TW", gl="TW", ceid="TW:zh-Hant")
        return self._collect_rss_items(client, rss_url=rss_url, domain=normalized_domain)

    def _collect_direct_site_items(
        self,
        client: httpx.Client,
        insecure_client: httpx.Client,
        domain: str,
        person_keywords: list[str],
        lookback_days: int,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        end = datetime.utcnow().date() + timedelta(days=1)
        start = end - timedelta(days=max(1, int(lookback_days)))
        cna_limit = self._cna_limit_for_lookback(lookback_days)
        max_pages = self._max_pages_for_lookback(lookback_days)
        hits = []
        try:
            if domain == "cna.com.tw":
                try:
                    hits = discover_cna(
                        client,
                        insecure_client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        limit=cna_limit,
                        require_taiwan_keyword=True,
                        require_dated_url=False,
                    )
                except TypeError:
                    hits = discover_cna(
                        client,
                        insecure_client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        limit=cna_limit,
                    )
            elif domain == "mofa.gov.tw":
                try:
                    hits = discover_mofa(
                        client,
                        insecure_client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        max_pages=max_pages,
                        require_taiwan_keyword=True,
                    )
                except TypeError:
                    hits = discover_mofa(
                        client,
                        insecure_client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        max_pages=max_pages,
                    )
            elif domain == "president.gov.tw":
                try:
                    hits = discover_president(
                        client,
                        insecure_client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        max_pages=max_pages,
                        require_taiwan_keyword=True,
                    )
                except TypeError:
                    hits = discover_president(
                        client,
                        insecure_client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        max_pages=max_pages,
                    )
        except Exception:
            hits = []

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in hits:
            url = str(getattr(hit, "url", "") or "").strip()
            if not url:
                continue
            key = self._url_key(url)
            if key in seen:
                continue
            seen.add(key)
            published_at = None
            published_date = str(getattr(hit, "published_date", "") or "").strip()
            if published_date:
                try:
                    day = datetime.strptime(published_date, "%Y-%m-%d").date()
                    published_at = datetime(day.year, day.month, day.day)
                except Exception:
                    published_at = None
            items.append(
                {
                    "url": url,
                    "title": str(getattr(hit, "title", "") or url),
                    "summary": str(getattr(hit, "excerpt", "") or ""),
                    "published_at": published_at,
                    "query_enforced_match": True,
                }
            )
        if domain == "cna.com.tw":
            # CNA site search can intermittently under-return (or rate-limit).
            # Add Google News CNA feed fallback and merge by URL.
            fallback_items = self._collect_cna_google_fallback_items(
                client,
                person_keywords=person_keywords,
                lookback_days=lookback_days,
                base_query=query or "",
            )
            for item in fallback_items:
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                key = self._url_key(url)
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
        return items

    def _collect_cna_google_fallback_items(
        self,
        client: httpx.Client,
        *,
        person_keywords: list[str],
        lookback_days: int,
        base_query: str,
    ) -> list[dict[str, Any]]:
        query_candidates: list[str] = []
        if base_query:
            query_candidates.append(base_query)
        for term in ["台灣", "臺灣", "Taiwan"]:
            query_candidates.append(self.build_query(person_keywords, [term], domain="cna.com.tw"))
        query_candidates = [q for q in query_candidates if q]

        seen_keys: set[str] = set()
        items: list[dict[str, Any]] = []
        for query in query_candidates:
            for hl, gl, ceid in [("zh-TW", "TW", "TW:zh-Hant"), ("en-US", "US", "US:en")]:
                rss_url = build_google_news_rss_url(query=query, hl=hl, gl=gl, ceid=ceid)
                for item in self._collect_rss_items(client, rss_url=rss_url, domain="cna.com.tw"):
                    url = str(item.get("url") or "").strip()
                    key = self._url_key(url)
                    if not key or key in seen_keys:
                        continue
                    if not self._within_lookback(item.get("published_at"), lookback_days):
                        continue
                    seen_keys.add(key)
                    item["query_enforced_match"] = True
                    items.append(item)
        return items

    def _cna_limit_for_lookback(self, lookback_days: int) -> int:
        days = max(1, int(lookback_days or 1))
        # Scale candidate depth with lookback window so long windows don't
        # silently cap at a small fixed number.
        if days >= 1800:
            return 1200
        if days >= 1000:
            return 900
        if days >= 365:
            return 600
        if days >= 180:
            return 400
        return 300

    def _max_pages_for_lookback(self, lookback_days: int) -> int:
        days = max(1, int(lookback_days or 1))
        if days >= 1800:
            return 220
        if days >= 1000:
            return 180
        if days >= 365:
            return 120
        if days >= 180:
            return 90
        return 40

    def _has_existing_person_source_url(self, person_id: int, source_url: str) -> bool:
        url = str(source_url or "").strip()
        if not url:
            return False
        existing = self.session.execute(
            select(StatementSource.id)
            .join(StatementParticipant, StatementParticipant.statement_id == StatementSource.statement_id)
            .where(
                StatementParticipant.person_id == int(person_id),
                StatementSource.source_url == url,
            )
            .limit(1)
        ).first()
        return existing is not None

    def _resolve_entry_url(self, client: httpx.Client, url: str) -> str:
        link = str(url or "").strip()
        if not link:
            return ""
        if "news.google.com" not in link:
            return link
        try:
            response = client.get(link, follow_redirects=True, timeout=20.0)
            final_url = str(response.url)
            if final_url and "news.google.com" not in final_url:
                return final_url
        except Exception:
            return link
        return link

    def _url_key(self, url: str) -> str:
        value = str(url or "").strip()
        if not value:
            return ""
        parsed = urlparse(value)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    def _fetch_full_article_text(self, client: httpx.Client, url: str, cache: dict[str, str]) -> str:
        key = self._url_key(url) or str(url or "").strip()
        if not key:
            return ""
        if key in cache:
            return cache[key]
        try:
            response = client.get(url, follow_redirects=True, timeout=20.0)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            text = " ".join(
                node.get_text(" ", strip=True)
                for node in soup.select("article p, .paragraph p, .paragraph, .cp p, .page-content p, .article p, .con p")
            )
            if not text:
                text = soup.get_text(" ", strip=True)
            text = self._strip_html(text)
        except Exception:
            text = ""
        cache[key] = text
        return text

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

    def _is_due_today(self, config: dict[str, Any], now_local: datetime, now_utc: datetime) -> bool:
        daily_time = self._normalize_daily_time(str(config.get("daily_time") or DEFAULT_DAILY_TIME))
        try:
            hour, minute = [int(item) for item in daily_time.split(":", 1)]
        except Exception:
            hour, minute = 9, 0
        if (now_local.hour, now_local.minute) < (hour, minute):
            return False

        last_run_at_text = str(config.get("last_run_at") or "").strip()
        if not last_run_at_text:
            return True
        last_run = parse_datetime(last_run_at_text)
        if not last_run:
            return True
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=ZoneInfo("UTC"))
        last_run_local = last_run.astimezone(self._tz)
        return last_run_local.date() < now_local.date()

    def _normalize_daily_time(self, value: str) -> str:
        text = str(value or "").strip()
        match = re.match(r"^(\d{1,2}):(\d{1,2})$", text)
        if not match:
            return DEFAULT_DAILY_TIME
        hour = max(0, min(23, int(match.group(1))))
        minute = max(0, min(59, int(match.group(2))))
        return f"{hour:02d}:{minute:02d}"

    def _normalize_lookback_days(self, value: Any) -> int:
        try:
            days = int(value)
        except Exception:
            days = DEFAULT_LOOKBACK_DAYS
        return max(1, min(3650, days))

    def _within_lookback(self, published_at: datetime | None, lookback_days: int) -> bool:
        if not published_at:
            return True
        now_utc = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
        published = published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=ZoneInfo("UTC"))
        cutoff = now_utc.timestamp() - float(lookback_days) * 86400.0
        return published.timestamp() >= cutoff

    def _clean_keywords(self, values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in list(values or []):
            text = str(value or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            output.append(text)
        return output

    def _filter_person_keywords(self, person: Person, values: list[str]) -> list[str]:
        if self._is_trump_name(person.full_name):
            return self._clean_keywords(values)
        cleaned = self._clean_keywords(values)
        filtered: list[str] = []
        seen: set[str] = set()
        for text in cleaned:
            if not self._is_english_full_name(text):
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            filtered.append(text)
        if filtered:
            return filtered
        fallback = str(person.full_name or "").strip()
        if self._is_english_full_name(fallback):
            return [fallback]
        return [fallback] if fallback else []

    def _is_trump_name(self, full_name: str | None) -> bool:
        return "trump" in str(full_name or "").casefold()

    def _is_english_full_name(self, text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        if re.search(r"[\u4e00-\u9fff]", value):
            return False
        parts = [part for part in re.split(r"\s+", value) if part]
        if len(parts) < 2:
            return False
        return bool(re.search(r"[A-Za-z]", value))

    def _clean_domains(self, values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in list(values or []):
            text = str(value or "").strip().lower()
            if not text:
                continue
            text = text.replace("https://", "").replace("http://", "").strip("/")
            if not text or "." not in text:
                continue
            if text in seen:
                continue
            seen.add(text)
            output.append(text)
        return output

    def _matched_keywords(self, text: str, keywords: list[str]) -> list[str]:
        haystack = str(text or "")
        lowered = haystack.casefold()
        matched: list[str] = []
        for keyword in keywords:
            term = str(keyword or "").strip()
            if not term:
                continue
            if re.search(r"[A-Za-z]", term):
                pattern = rf"(?<![A-Za-z]){re.escape(term.casefold())}(?![A-Za-z])"
                if re.search(pattern, lowered):
                    matched.append(term)
            elif term in haystack:
                matched.append(term)
        return matched

    def _merge_text(self, title: str | None, summary: str | None) -> str:
        return f"{str(title or '').strip()} {str(summary or '').strip()}".strip()

    def _strip_html(self, text: str) -> str:
        value = re.sub(r"<[^>]+>", " ", str(text or ""))
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def build_query(self, person_keywords: list[str], taiwan_keywords: list[str], domain: str | None = None) -> str:
        person_expr = " OR ".join(self._quote_if_needed(item) for item in person_keywords if item)
        taiwan_expr = " OR ".join(self._quote_if_needed(item) for item in taiwan_keywords if item)
        base = f"({person_expr}) ({taiwan_expr})".strip()
        if domain:
            return f"{base} site:{domain}"
        return base

    def _quote_if_needed(self, text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        if " " in value:
            return f"\"{value}\""
        return value
