from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from tracker.collectors.media.cspan_search import CSpanSearchCollector
from tracker.logging_utils import get_logger
from tracker.models import Appointment, Office, Person, SyncRun, Tracker, TrackerTarget
from tracker.services.notification_service import NotificationService
from tracker.services.officials_service import OfficialsService
from tracker.services.statements_service import StatementsService
from tracker.utils.social import detect_social_platform, discover_social_profiles
from tracker.utils.text import compact_whitespace
from tracker.utils.web import absolute_url, parse_datetime


logger = get_logger(__name__)


@dataclass
class TrackerSyncResult:
    tracker_id: int
    tracker_name: str
    records_found: int = 0
    records_created: int = 0
    records_updated: int = 0
    errors: list[str] = field(default_factory=list)


class TrackerSyncService:
    http_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def __init__(self, session: Session) -> None:
        self.session = session
        self.statements_service = StatementsService(session)
        self.notification_service = NotificationService(session)
        self.officials_service = OfficialsService(session)
        self.cspan_collector = CSpanSearchCollector()
        self._person_reference_cache: list[dict[str, Any]] | None = None

    def sync_all_active_trackers(self) -> dict[str, Any]:
        trackers = self.session.execute(select(Tracker).where(Tracker.status == "active").order_by(Tracker.updated_at.desc())).scalars().all()
        results = [self.sync_tracker(tracker) for tracker in trackers]
        failed = [item for item in results if item.errors]
        return {
            "status": "partial_failure" if failed else "success",
            "results": [item.__dict__ for item in results],
        }

    def sync_tracker(self, tracker: Tracker) -> TrackerSyncResult:
        result = TrackerSyncResult(tracker_id=tracker.id, tracker_name=tracker.name)
        sync_run = SyncRun(job_name=f"tracker_sync_{tracker.id}", job_type="tracker_sync", source_name=tracker.name)
        self.session.add(sync_run)
        self.session.flush()
        try:
            active_targets = [item for item in tracker.targets if item.is_active]
            person = self.session.get(Person, tracker.person_id)
            for target in active_targets:
                candidates = self._fetch_target_candidates(tracker, target)
                result.records_found += len(candidates)
                if person and target.target_type in {"official_website", "press_release_page", "hearing_page", "activity_page", "social_page"}:
                    self._enrich_person_from_target(person, target)
                for candidate in candidates:
                    self._attach_matching_participants(candidate)
                    statement, created = self.statements_service.ingest_statement(candidate)
                    if person:
                        self._extract_chinese_alias_from_candidate(person, candidate)
                    if created:
                        result.records_created += 1
                        if statement.relevance_score and statement.relevance_score > 0:
                            event_type = "new_media_report" if not statement.is_primary_source else "new_taiwan_statement"
                            self.notification_service.notify(
                                event_type=event_type,
                                title=statement.title,
                                body=statement.excerpt or statement.source_url,
                                target_identifier=str(tracker.person_id),
                                payload={
                                    "tracker_id": tracker.id,
                                    "statement_id": statement.id,
                                    "source_url": statement.source_url,
                                    "source_type": statement.source_type,
                                },
                            )
                    else:
                        result.records_updated += 1
                target.last_checked_at = datetime.utcnow()
                target.last_status = "success"
                target.last_error_message = None
            tracker.last_run_at = datetime.utcnow()
            tracker.last_run_status = "success"
            tracker.last_error_message = None
            sync_run.status = "success"
        except Exception as exc:
            logger.exception("Tracker sync failed for tracker_id=%s", tracker.id)
            result.errors.append(str(exc))
            tracker.last_run_at = datetime.utcnow()
            tracker.last_run_status = "failed"
            tracker.last_error_message = str(exc)
            sync_run.status = "failed"
            sync_run.error_message = str(exc)
        finally:
            sync_run.ended_at = datetime.utcnow()
            sync_run.records_found = result.records_found
            sync_run.records_created = result.records_created
            sync_run.records_updated = result.records_updated
            sync_run.meta = {"errors": result.errors, "tracker_id": tracker.id}
        return result

    def _fetch_target_candidates(self, tracker: Tracker, target: TrackerTarget) -> list[dict[str, Any]]:
        if target.target_type == "cspan_search_target":
            return self.cspan_collector.search(target.target_url, tracker.person_id, tracker.id, target.id)
        if target.target_type == "social_page" and self._extract_x_handle(target.target_url):
            return self._fetch_x_site_candidates(tracker, target)
        if target.target_type == "rss_feed" or target.target_url.endswith(".xml") or "rss" in target.target_url.lower():
            return self._fetch_rss_candidates(tracker, target)
        return self._fetch_html_candidates(tracker, target)

    def _resolve_source_type(self, target_type: str) -> str:
        if target_type == "cspan_search_target":
            return "cspan"
        if target_type in {"media_search_target", "activity_media_target"}:
            return "media"
        if target_type == "social_page":
            return "social"
        return "official"

    def _is_primary_source(self, target_type: str) -> bool:
        return target_type not in {"media_search_target", "activity_media_target", "cspan_search_target"}

    def _fetch_rss_candidates(self, tracker: Tracker, target: TrackerTarget) -> list[dict[str, Any]]:
        parsed = feedparser.parse(target.target_url)
        candidates: list[dict[str, Any]] = []
        target_year = self._target_year(target)
        for entry in parsed.entries[:25]:
            link = getattr(entry, "link", None)
            if not link:
                continue
            summary = compact_whitespace(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
            title = compact_whitespace(getattr(entry, "title", "") or link)
            published_at = parse_datetime(getattr(entry, "published", None)) or parse_datetime(getattr(entry, "updated", None))
            if target_year and published_at and published_at.year != target_year:
                continue
            candidates.append(
                {
                    "person_id": tracker.person_id,
                    "tracker_id": tracker.id,
                    "tracker_target_id": target.id,
                    "title": title,
                    "date_published": published_at,
                    "source_url": link,
                    "source_type": self._rss_source_type(target),
                    "statement_type": target.target_type,
                    "excerpt": summary[:1000],
                    "full_text": summary[:5000],
                    "raw_text": summary,
                    "source_title": title,
                    "parser_identity": target.parser_identity or "rss_target_v1",
                    "is_primary_source": self._is_primary_source(target.target_type),
                    "raw_payload": {"target_name": target.target_name, "target_url": target.target_url},
                }
            )
        return candidates

    def _rss_source_type(self, target: TrackerTarget) -> str:
        parser_identity = (target.parser_identity or "").lower()
        target_name = (target.target_name or "").lower()
        if "taiwan_president" in parser_identity or "president.gov.tw" in target_name:
            return "official"
        if "taiwan_mofa" in parser_identity or "mofa.gov.tw" in target_name:
            return "official"
        if "taiwan_cna" in parser_identity or "cna.com.tw" in target_name:
            return "media"
        return self._resolve_source_type(target.target_type)

    def _attach_matching_participants(self, candidate: dict[str, Any]) -> None:
        text = compact_whitespace(
            " ".join(
                [
                    str(candidate.get("title") or ""),
                    str(candidate.get("excerpt") or ""),
                    str(candidate.get("full_text") or ""),
                    str(candidate.get("raw_text") or ""),
                ]
            )
        )
        if not text:
            return

        participant_ids = list(candidate.get("participant_ids") or [])
        if candidate.get("person_id") and candidate["person_id"] not in participant_ids:
            participant_ids.append(candidate["person_id"])

        matched_people: list[dict[str, Any]] = []
        for seeded in self._seed_missing_people_from_text(text, candidate):
            if seeded["person_id"] not in participant_ids:
                participant_ids.append(seeded["person_id"])
            matched_people.append(seeded)

        for item in self._person_references():
            if self._text_mentions_person(text, item["tokens"]):
                if item["person_id"] not in participant_ids:
                    participant_ids.append(item["person_id"])
                if not any(existing["person_id"] == item["person_id"] for existing in matched_people):
                    matched_people.append({"person_id": item["person_id"], "name": item["full_name"]})

        if participant_ids:
            candidate["participant_ids"] = participant_ids
        if matched_people:
            raw_payload = dict(candidate.get("raw_payload") or {})
            raw_payload["matched_people"] = matched_people
            candidate["raw_payload"] = raw_payload

    def _person_references(self) -> list[dict[str, Any]]:
        if self._person_reference_cache is not None:
            return self._person_reference_cache

        people = (
            self.session.execute(
                select(Person).options(
                    joinedload(Person.aliases),
                    joinedload(Person.appointments).joinedload(Appointment.office),
                    joinedload(Person.appointments).joinedload(Appointment.jurisdiction),
                )
            )
            .scalars()
            .unique()
            .all()
        )
        references: list[dict[str, Any]] = []
        for person in people:
            if not self._is_us_public_official(person):
                continue
            tokens: list[str] = []
            seen: set[str] = set()
            for token in [person.full_name, *(alias.alias for alias in person.aliases)]:
                normalized = compact_whitespace(token or "")
                if not normalized:
                    continue
                lowered = normalized.casefold()
                if lowered in seen:
                    continue
                seen.add(lowered)
                tokens.append(normalized)
            references.append({"person_id": person.id, "full_name": person.full_name, "tokens": tokens})
        self._person_reference_cache = references
        return references

    def _is_us_public_official(self, person: Person) -> bool:
        for appointment in person.appointments:
            office = appointment.office
            jurisdiction = appointment.jurisdiction
            if not office:
                continue
            if office.level not in {"federal", "state", "local"}:
                continue
            country = (jurisdiction.country if jurisdiction else None) or ""
            if not country or country.lower() in {"usa", "us", "united states", "united states of america"}:
                return True
        return False

    def _text_mentions_person(self, text: str, tokens: list[str]) -> bool:
        lowered_text = text.casefold()
        for token in tokens:
            if self._token_mentioned(lowered_text, token):
                return True
        return False

    def _token_mentioned(self, lowered_text: str, token: str) -> bool:
        lowered_token = token.casefold()
        if not lowered_token:
            return False
        if re.search(r"[a-z]", lowered_token):
            pattern = rf"(?<![a-z]){re.escape(lowered_token)}(?![a-z])"
            return re.search(pattern, lowered_text) is not None
        if len(lowered_token) < 2:
            return False
        return lowered_token in lowered_text

    def _seed_missing_people_from_text(self, text: str, candidate: dict[str, Any]) -> list[dict[str, Any]]:
        discovered = self._extract_name_pairs_from_text(text)
        if not discovered:
            return []

        created: list[dict[str, Any]] = []
        for english_name, chinese_alias in discovered:
            if self.officials_service.find_person(english_name):
                continue
            if not self._looks_like_us_public_official_context(text, english_name, chinese_alias):
                continue
            try:
                person, _ = self.officials_service.upsert_person(
                    {
                        "full_name": english_name,
                        "source_url": candidate.get("source_url"),
                        "source_type": candidate.get("source_type") or "media",
                        "seed_source_type": candidate.get("source_type") or "media",
                        "profile_status": "seeded",
                        "verification_status": "unverified",
                        "parser_identity": "mentioned_person_seed_v1",
                        "raw_payload": {
                            "mention_seed": True,
                            "mention_seed_source_url": candidate.get("source_url"),
                            "mention_seed_title": candidate.get("title"),
                        },
                    }
                )
            except Exception:
                continue
            if chinese_alias:
                self.officials_service.ensure_alias(
                    person.id,
                    chinese_alias,
                    candidate.get("source_url"),
                    candidate.get("source_type") or "media",
                    alias_type="chinese_name",
                )
            created.append({"person_id": person.id, "name": person.full_name})

        if created:
            self._person_reference_cache = None
        return created

    def _looks_like_us_public_official_context(self, text: str, english_name: str, chinese_alias: str | None) -> bool:
        keywords = [
            "u.s.", "us ", "american", "federal", "senator", "representative", "congressman", "congresswoman",
            "secretary", "under secretary", "assistant secretary", "governor", "mayor", "ait",
            "state department", "white house", "president trump", "vice president vance",
            "ç¾Žåœ‹", "ç¾Žæ–¹", "è¯é‚¦", "åƒè­°å“¡", "çœ¾è­°å“¡", "è­°å“¡", "å®˜å“¡", "åœ‹å‹™å¿", "éƒ¨é•·", "æ¬¡å¿", "åŠ©å¿",
            "ç™½å®®", "ç¾Žåœ‹åœ¨å°å”æœƒ", "è™•é•·", "ç¸½çµ±å·æ™®", "å‰¯ç¸½çµ±èŒƒæ–¯",
        ]
        haystack = text.casefold()
        names = [english_name]
        if chinese_alias:
            names.append(chinese_alias)
        for name in names:
            idx = haystack.find(name.casefold())
            if idx == -1:
                continue
            start = max(0, idx - 160)
            end = min(len(text), idx + len(name) + 160)
            window = text[start:end]
            lowered_window = window.casefold()
            if any(keyword in lowered_window for keyword in keywords if re.search(r"[a-z]", keyword)):
                return True
            if any(keyword in window for keyword in keywords if not re.search(r"[a-z]", keyword)):
                return True
        return False

    def _extract_name_pairs_from_text(self, text: str) -> list[tuple[str, str | None]]:
        pairs: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        patterns = [
            re.compile(r"([\u4e00-\u9fff\u00b7]{2,12})\s*[\uFF08(]\s*([A-Z][A-Za-z'`.-]+(?:\s+[A-Z][A-Za-z'`.-]+){1,4})\s*[\uFF09)]"),
            re.compile(r"([A-Z][A-Za-z'`.-]+(?:\s+[A-Z][A-Za-z'`.-]+){1,4})\s*[\uFF08(]\s*([\u4e00-\u9fff\u00b7]{2,12})\s*[\uFF09)]"),
        ]
        for pattern in patterns:
            for match in pattern.finditer(text):
                if re.search(r"[\u4e00-\u9fff]", match.group(1)):
                    chinese_alias = compact_whitespace(match.group(1))
                    english_name = compact_whitespace(match.group(2))
                else:
                    english_name = compact_whitespace(match.group(1))
                    chinese_alias = compact_whitespace(match.group(2))
                if not self._looks_like_person_name(english_name):
                    continue
                key = english_name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                pairs.append((english_name, chinese_alias))
        return pairs

    def _looks_like_person_name(self, name: str) -> bool:
        lowered = name.casefold()
        if any(token in lowered for token in ["department", "committee", "council", "government", "administration", "office"]):
            return False
        words = name.split()
        if len(words) < 2 or len(words) > 5:
            return False
        return all(re.match(r"^[A-Z][A-Za-z'`.-]+$", word) for word in words)

    def _extract_chinese_alias_from_candidate(self, person: Person, candidate: dict[str, Any]) -> None:
        text = compact_whitespace(
            " ".join(
                [
                    str(candidate.get("title") or ""),
                    str(candidate.get("excerpt") or ""),
                    str(candidate.get("full_text") or ""),
                ]
            )
        )
        if not text:
            return
        for alias in self._find_chinese_aliases(text, person.full_name):
            self.officials_service.ensure_alias(
                person.id,
                alias,
                candidate.get("source_url"),
                candidate.get("source_type"),
                alias_type="chinese_name",
            )

    def _find_chinese_aliases(self, text: str, full_name: str) -> list[str]:
        aliases: list[str] = []
        escaped_name = re.escape(full_name)
        patterns = [
            rf"([\u4e00-\u9fff\u00b7]{{2,12}})\s*[\uFF08(]\s*{escaped_name}\s*[\uFF09)]",
            rf"{escaped_name}\s*[\uFF08(]\s*([\u4e00-\u9fff\u00b7]{{2,12}})\s*[\uFF09)]",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                candidate = compact_whitespace(match.group(1))
                if candidate and candidate not in aliases:
                    aliases.append(candidate)
        return aliases

    def _enrich_person_from_target(self, person: Person, target: TrackerTarget) -> None:
        try:
            response = httpx.get(
                target.target_url,
                timeout=20.0,
                follow_redirects=True,
                trust_env=False,
                headers=self.http_headers,
            )
            response.raise_for_status()
        except Exception:
            return
        soup = BeautifulSoup(response.text, "html.parser")
        portrait_url = self._discover_portrait_url(target.target_url, soup)
        bio = self._discover_short_bio(soup)
        social_profiles = discover_social_profiles(target.target_url, soup)
        self.officials_service.enrich_person_profile(
            person=person,
            official_url=target.target_url if target.target_type != "social_page" else None,
            portrait_url=portrait_url,
            portrait_source_url=target.target_url,
            portrait_source_type="social" if target.target_type == "social_page" else "official",
            bio=bio,
            social_profiles=social_profiles,
        )
        if social_profiles:
            self._ensure_social_targets(person.id, target.tracker_id, social_profiles)

    def _discover_portrait_url(self, base_url: str, soup: BeautifulSoup) -> str | None:
        meta = soup.find("meta", attrs={"property": "og:image"}) or soup.find("meta", attrs={"name": "og:image"})
        if meta and meta.get("content"):
            return absolute_url(base_url, meta["content"].strip())

        selectors = [
            "img[alt*='official' i]",
            "img[alt*='portrait' i]",
            "img[alt*='headshot' i]",
            "img[src*='headshot']",
            "img[src*='portrait']",
            "main img",
            "article img",
        ]
        for selector in selectors:
            image = soup.select_one(selector)
            if image and image.get("src"):
                return absolute_url(base_url, image["src"].strip())
        return None

    def _discover_short_bio(self, soup: BeautifulSoup) -> str | None:
        paragraphs = soup.select("main p, article p, .entry-content p")
        for paragraph in paragraphs[:5]:
            text = compact_whitespace(paragraph.get_text(" ", strip=True))
            if len(text) >= 120:
                return text[:800]
        return None

    def _ensure_social_targets(self, person_id: int, tracker_id: int, social_profiles: dict[str, str]) -> None:
        existing_targets = self.session.execute(select(TrackerTarget).where(TrackerTarget.tracker_id == tracker_id)).scalars().all()
        existing_urls = {target.target_url for target in existing_targets}
        for platform, url in social_profiles.items():
            if url in existing_urls:
                continue
            self.session.add(
                TrackerTarget(
                    tracker_id=tracker_id,
                    target_name=f"{platform} profile",
                    target_type="social_page",
                    target_url=url,
                    parser_identity="official_social_discovery_v1",
                    is_active=True,
                )
            )

    def _fetch_html_candidates(self, tracker: Tracker, target: TrackerTarget) -> list[dict[str, Any]]:
        response = httpx.get(
            target.target_url,
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
            headers=self.http_headers,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title = compact_whitespace(soup.title.get_text(" ", strip=True) if soup.title else target.target_name or target.target_url)
        page_text = compact_whitespace(soup.get_text(" ", strip=True))
        target_year = self._target_year(target)
        page_date = self._extract_page_date(soup)
        candidates: list[dict[str, Any]] = [
            {
                "person_id": tracker.person_id,
                "tracker_id": tracker.id,
                "tracker_target_id": target.id,
                "title": title,
                "date_published": page_date,
                "source_url": target.target_url,
                "source_type": self._resolve_source_type(target.target_type),
                "statement_type": target.target_type,
                "excerpt": page_text[:1000],
                "full_text": page_text[:5000],
                "raw_text": page_text,
                "source_title": title,
                "parser_identity": target.parser_identity or "generic_html_target_v1",
                "is_primary_source": self._is_primary_source(target.target_type),
                "raw_payload": {"target_name": target.target_name, "target_url": target.target_url, "fetched_from": "page"},
            }
        ]
        if target_year and page_date and page_date.year != target_year:
            candidates = []

        followed = 0
        for anchor in soup.find_all("a", href=True):
            if followed >= 20:
                break
            href = anchor["href"].strip()
            if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
                continue
            absolute = absolute_url(target.target_url, href)
            anchor_text = compact_whitespace(anchor.get_text(" ", strip=True))
            link_text = f"{anchor_text} {absolute}".lower()
            if target.target_type in {"media_search_target", "activity_media_target"}:
                allowed = True
            elif target.target_type == "social_page":
                allowed = any(
                    term in link_text
                    for term in [
                        "taiwan",
                        "status",
                        "post",
                        "posts",
                        "thread",
                        "reel",
                        "video",
                        "watch",
                        "x.com",
                        "twitter.com",
                        "facebook.com",
                        "instagram.com",
                    ]
                )
            else:
                allowed = any(term in link_text for term in ["press", "release", "news", "statement", "speech", "hearing", "remarks", "taiwan", "post", "thread", "event", "visit"])
            if not allowed:
                continue
            try:
                child = httpx.get(
                    absolute,
                    timeout=20.0,
                    follow_redirects=True,
                    trust_env=False,
                    headers=self.http_headers,
                )
                child.raise_for_status()
            except Exception:
                continue
            child_soup = BeautifulSoup(child.text, "html.parser")
            child_title = compact_whitespace(child_soup.title.get_text(" ", strip=True) if child_soup.title else anchor_text or absolute)
            child_text = compact_whitespace(child_soup.get_text(" ", strip=True))
            child_date = self._extract_page_date(child_soup)
            if target_year and child_date and child_date.year != target_year:
                continue
            candidates.append(
                {
                    "person_id": tracker.person_id,
                    "tracker_id": tracker.id,
                    "tracker_target_id": target.id,
                    "title": child_title,
                    "date_published": child_date,
                    "source_url": absolute,
                    "source_type": self._resolve_source_type(target.target_type),
                    "statement_type": target.target_type,
                    "excerpt": child_text[:1000],
                    "full_text": child_text[:5000],
                    "raw_text": child_text,
                    "source_title": child_title,
                    "parser_identity": target.parser_identity or "generic_html_target_v1",
                    "is_primary_source": self._is_primary_source(target.target_type),
                    "raw_payload": {"target_name": target.target_name, "target_url": target.target_url, "fetched_from": target.target_url},
                }
            )
            followed += 1
        return candidates

    def _fetch_x_site_candidates(self, tracker: Tracker, target: TrackerTarget) -> list[dict[str, Any]]:
        handle = self._extract_x_handle(target.target_url)
        if not handle:
            return self._fetch_html_candidates(tracker, target)

        query = f'site:x.com/{handle} Taiwan OR site:twitter.com/{handle} Taiwan'
        search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            response = httpx.get(
                search_url,
                timeout=20.0,
                follow_redirects=True,
                trust_env=False,
                headers=self.http_headers,
            )
            response.raise_for_status()
        except Exception:
            return self._fetch_html_candidates(tracker, target)

        if response.status_code == 202:
            return self._fetch_html_candidates(tracker, target)

        soup = BeautifulSoup(response.text, "html.parser")
        candidates: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for result in soup.select(".result")[:8]:
            link = result.select_one("a.result__a")
            if not link:
                continue
            candidate_url = self._unwrap_duckduckgo_url(link.get("href", ""))
            normalized = self._normalize_x_post_url(candidate_url, handle)
            if not normalized or normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            snippet_node = result.select_one(".result__snippet")
            snippet = compact_whitespace(snippet_node.get_text(" ", strip=True) if snippet_node else "")
            title = compact_whitespace(link.get_text(" ", strip=True) or normalized)
            candidates.append(
                {
                    "person_id": tracker.person_id,
                    "tracker_id": tracker.id,
                    "tracker_target_id": target.id,
                    "title": title,
                    "date_published": None,
                    "source_url": normalized,
                    "source_type": "social",
                    "statement_type": "social_post",
                    "excerpt": snippet[:1000],
                    "full_text": snippet[:5000],
                    "raw_text": snippet,
                    "source_title": title,
                    "parser_identity": "x_site_search_v1",
                    "is_primary_source": True,
                    "raw_payload": {
                        "target_name": target.target_name,
                        "target_url": target.target_url,
                        "search_query": query,
                        "search_backend": "duckduckgo_html",
                    },
                }
            )
            if len(candidates) >= 5:
                break

        if candidates:
            return candidates
        return self._fetch_html_candidates(tracker, target)

    def _extract_x_handle(self, url: str) -> str | None:
        platform = detect_social_platform(url)
        if platform != "x":
            return None
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if not path_parts:
            return None
        first = path_parts[0].lower()
        if first in {"home", "search", "explore", "hashtag", "i", "settings", "intent"}:
            return None
        return path_parts[0]

    def _unwrap_duckduckgo_url(self, url: str) -> str | None:
        if not url:
            return None
        if url.startswith("//duckduckgo.com/l/?"):
            parsed = urlparse(f"https:{url}")
            uddg = parse_qs(parsed.query).get("uddg", [])
            if uddg:
                return unquote(uddg[0])
        return url

    def _normalize_x_post_url(self, url: str | None, handle: str) -> str | None:
        if not url:
            return None
        parsed = urlparse(url)
        hostname = (parsed.netloc or "").lower()
        if hostname not in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 3:
            return None
        if parts[0].lower() != handle.lower():
            return None
        if parts[1].lower() != "status":
            return None
        return f"{parsed.scheme or 'https'}://{parsed.netloc}/{parts[0]}/{parts[1]}/{parts[2]}"

    def _target_year(self, target: TrackerTarget) -> int | None:
        parser_identity = target.parser_identity or ""
        match = re.search(r"_y(20\d{2})_", parser_identity)
        if match:
            return int(match.group(1))
        return None

    def _extract_page_date(self, soup: BeautifulSoup) -> datetime | None:
        selectors = [
            ("meta", {"property": "article:published_time"}, "content"),
            ("meta", {"name": "article:published_time"}, "content"),
            ("meta", {"property": "og:updated_time"}, "content"),
            ("meta", {"name": "pubdate"}, "content"),
            ("meta", {"name": "date"}, "content"),
            ("time", {}, "datetime"),
        ]
        for tag_name, attrs, value_key in selectors:
            tag = soup.find(tag_name, attrs=attrs) if attrs else soup.find(tag_name)
            if tag and tag.get(value_key):
                parsed = parse_datetime(tag.get(value_key))
                if parsed:
                    return parsed
        time_tag = soup.find("time")
        if time_tag:
            parsed = parse_datetime(time_tag.get_text(" ", strip=True))
            if parsed:
                return parsed
        return None

