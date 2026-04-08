from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.discover_restricted_source_events import discover_cna, discover_mofa, discover_president
from tracker.models import Person, StatementParticipant, StatementSource
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
    queries: list[dict[str, Any]] | None = None
    ok: bool = True
    error: str | None = None


class PersonTaiwanEventMonitorService:
    def __init__(self, session: Session, timezone: str = "Asia/Taipei") -> None:
        self.session = session
        self.statements_service = StatementsService(session)
        self.timezone = timezone
        self._tz = ZoneInfo(timezone)
        self._http_headers = {"User-Agent": "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"}

    def default_config_for_person(self, person: Person, chinese_aliases: list[str] | None = None) -> dict[str, Any]:
        keywords = [person.full_name]
        if person.family_name and person.family_name not in keywords:
            keywords.append(person.family_name)
        for alias in list(chinese_aliases or []):
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

    def get_person_monitor_config(self, person: Person, chinese_aliases: list[str] | None = None) -> dict[str, Any]:
        payload = dict(person.raw_payload or {})
        config = payload.get(MONITOR_KEY)
        if not isinstance(config, dict):
            config = self.default_config_for_person(person, chinese_aliases=chinese_aliases)
        config.setdefault("enabled", False)
        config.setdefault("person_keywords", [person.full_name])
        config.setdefault("taiwan_keywords", list(DEFAULT_TAIWAN_KEYWORDS))
        config.setdefault("domains", list(DEFAULT_DOMAINS))
        config.setdefault("daily_time", DEFAULT_DAILY_TIME)
        config.setdefault("lookback_days", DEFAULT_LOOKBACK_DAYS)
        config.setdefault("include_global_news", True)
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
        payload = dict(person.raw_payload or {})
        config = self.get_person_monitor_config(person)
        config["enabled"] = bool(enabled)
        config["person_keywords"] = self._clean_keywords(person_keywords)
        config["taiwan_keywords"] = self._clean_keywords(taiwan_keywords) or list(DEFAULT_TAIWAN_KEYWORDS)
        config["domains"] = self._clean_domains(domains) or list(DEFAULT_DOMAINS)
        config["daily_time"] = self._normalize_daily_time(daily_time)
        config["lookback_days"] = self._normalize_lookback_days(lookback_days)
        payload[MONITOR_KEY] = config
        person.raw_payload = payload
        person.last_seen_at = datetime.utcnow()
        self.session.flush()
        return config

    def run_due_monitors(self) -> dict[str, Any]:
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
        return {"status": "success", "due_count": len(due_people), "results": results}

    def run_for_person(self, person_id: int, trigger: str = "manual") -> MonitorRunResult:
        person = self.session.get(Person, int(person_id))
        if not person:
            return MonitorRunResult(person_id=person_id, person_name="", ok=False, error="Person not found")

        config = self.get_person_monitor_config(person)
        person_keywords = self._clean_keywords(config.get("person_keywords") or [])
        taiwan_keywords = self._clean_keywords(config.get("taiwan_keywords") or [])
        domains = self._clean_domains(config.get("domains") or [])
        lookback_days = self._normalize_lookback_days(config.get("lookback_days"))
        include_global_news = bool(config.get("include_global_news", True))
        if not person_keywords:
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

        with httpx.Client(headers=self._http_headers, follow_redirects=True, timeout=25.0) as client:
            for domain in domains:
                query = self.build_query(person_keywords, taiwan_keywords, domain=domain)
                items = self._collect_domain_items(
                    client=client,
                    domain=domain,
                    query=query,
                    person_keywords=person_keywords,
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
                    matched_person = self._matched_keywords(text, person_keywords)
                    matched_taiwan = self._matched_keywords(text, taiwan_keywords)
                    if not matched_person or not matched_taiwan:
                        if bool(item.get("query_enforced_match")):
                            if not matched_person and person_keywords:
                                matched_person = [person_keywords[0]]
                            if not matched_taiwan and taiwan_keywords:
                                matched_taiwan = [taiwan_keywords[0]]
                        else:
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
                    matched_person = self._matched_keywords(text, person_keywords) or ([person_keywords[0]] if person_keywords else [])
                    matched_taiwan = self._matched_keywords(text, taiwan_keywords) or ([taiwan_keywords[0]] if taiwan_keywords else [])
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
            queries=query_logs,
            ok=True,
        )

    def _save_run_record(self, person: Person, run_record: dict[str, Any]) -> None:
        payload = dict(person.raw_payload or {})
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
        domain: str,
        query: str,
        person_keywords: list[str],
        lookback_days: int,
    ) -> list[dict[str, Any]]:
        normalized_domain = str(domain or "").strip().lower()
        if normalized_domain in {"cna.com.tw", "mofa.gov.tw", "president.gov.tw"}:
            return self._collect_direct_site_items(client, normalized_domain, person_keywords, lookback_days)
        rss_url = build_google_news_rss_url(query=query, hl="zh-TW", gl="TW", ceid="TW:zh-Hant")
        return self._collect_rss_items(client, rss_url=rss_url, domain=normalized_domain)

    def _collect_direct_site_items(
        self,
        client: httpx.Client,
        domain: str,
        person_keywords: list[str],
        lookback_days: int,
    ) -> list[dict[str, Any]]:
        end = datetime.utcnow().date() + timedelta(days=1)
        start = end - timedelta(days=max(1, int(lookback_days)))
        hits = []
        try:
            if domain == "cna.com.tw":
                try:
                    hits = discover_cna(
                        client,
                        client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        require_taiwan_keyword=False,
                        require_dated_url=False,
                    )
                except TypeError:
                    hits = discover_cna(
                        client,
                        client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        limit=120,
                    )
            elif domain == "mofa.gov.tw":
                try:
                    hits = discover_mofa(
                        client,
                        client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        max_pages=40,
                        require_taiwan_keyword=False,
                    )
                except TypeError:
                    hits = discover_mofa(
                        client,
                        client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        max_pages=40,
                    )
            elif domain == "president.gov.tw":
                try:
                    hits = discover_president(
                        client,
                        client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        max_pages=40,
                        require_taiwan_keyword=False,
                    )
                except TypeError:
                    hits = discover_president(
                        client,
                        client,
                        person_terms=person_keywords,
                        start=start,
                        end=end,
                        max_pages=40,
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
                    "query_enforced_match": False,
                }
            )
        return items

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
