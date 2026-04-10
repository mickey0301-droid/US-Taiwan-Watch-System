from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import Alias, Appointment, Office, Person
from tracker.models import Legislation, LegislationSource, Statement, StatementParticipant, StatementSource
from tracker.services.ai_assist_service import AIAssistService
from tracker.services.legislation_service import LegislationService
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService
from tracker.services.postgres_sequence_service import sync_postgres_id_sequences
from tracker.services.statements_service import StatementsService
from tracker.utils.source_types import is_government_url
from tracker.utils.text import compact_whitespace
from tracker.utils.web import parse_datetime

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - dependency is optional at runtime
    PdfReader = None


@dataclass
class ManualImportResult:
    created: int = 0
    updated: int = 0
    failed: int = 0
    items: list[dict[str, Any]] | None = None


class ManualUrlImportService:
    http_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    PERSON_TYPE_CONFIG: dict[str, dict[str, str | None]] = {
        "federal_official": {
            "office_name": "Federal Executive Official",
            "level": "federal",
            "branch": "executive",
            "chamber": None,
            "role_title": "Federal Official",
            "jurisdiction_name": "United States",
            "jurisdiction_type": "country",
        },
        "federal_senator": {
            "office_name": "United States Senate",
            "level": "federal",
            "branch": "legislative",
            "chamber": "senate",
            "role_title": "United States Senator",
            "jurisdiction_name": "United States",
            "jurisdiction_type": "country",
        },
        "federal_house": {
            "office_name": "United States House of Representatives",
            "level": "federal",
            "branch": "legislative",
            "chamber": "house",
            "role_title": "United States Representative",
            "jurisdiction_name": "United States",
            "jurisdiction_type": "country",
        },
        "state_official": {
            "office_name": "State Executive Official",
            "level": "state",
            "branch": "executive",
            "chamber": None,
            "role_title": "State Official",
            "jurisdiction_name": None,
            "jurisdiction_type": "state",
        },
        "state_legislator": {
            "office_name": "State Legislature",
            "level": "state",
            "branch": "legislative",
            "chamber": None,
            "role_title": "State Legislator",
            "jurisdiction_name": None,
            "jurisdiction_type": "state",
        },
    }

    CONGRESS_BILL_PATTERN = re.compile(r"/bill/(?P<congress>\d+)-congress/(?P<bill_type>[^/]+)/(?P<number>\d+)", re.I)
    BILL_TYPE_INFO = {
        "house-bill": ("H.R.", "house", "bill"),
        "senate-bill": ("S.", "senate", "bill"),
        "house-resolution": ("H.Res.", "house", "resolution"),
        "senate-resolution": ("S.Res.", "senate", "resolution"),
        "house-concurrent-resolution": ("H.Con.Res.", "house", "concurrent_resolution"),
        "senate-concurrent-resolution": ("S.Con.Res.", "senate", "concurrent_resolution"),
        "house-joint-resolution": ("H.J.Res.", "house", "joint_resolution"),
        "senate-joint-resolution": ("S.J.Res.", "senate", "joint_resolution"),
    }
    STATE_NAMES = [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
        "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
        "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
        "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico",
        "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
        "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
        "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
        "Guam", "Puerto Rico", "American Samoa", "Northern Mariana Islands", "U.S. Virgin Islands",
    ]
    STATE_ABBREVIATIONS = {
        "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas", "ca": "California",
        "co": "Colorado", "ct": "Connecticut", "de": "Delaware", "fl": "Florida", "ga": "Georgia",
        "hi": "Hawaii", "id": "Idaho", "il": "Illinois", "in": "Indiana", "ia": "Iowa",
        "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
        "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri",
        "mt": "Montana", "ne": "Nebraska", "nv": "Nevada", "nh": "New Hampshire", "nj": "New Jersey",
        "nm": "New Mexico", "ny": "New York", "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio",
        "ok": "Oklahoma", "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
        "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah", "vt": "Vermont",
        "va": "Virginia", "wa": "Washington", "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming",
        "dc": "District of Columbia",
    }

    def __init__(self, session: Session) -> None:
        self.session = session
        self.officials_service = OfficialsService(session)
        self.statements_service = StatementsService(session)
        self.legislation_service = LegislationService(session)
        self.ai_assist_service = AIAssistService()
        self._people_search_index: list[tuple[int, str]] | None = None

    def parse_urls(self, raw_text: str) -> list[str]:
        parts = re.split(r"[\n,\s]+", str(raw_text or ""))
        urls: list[str] = []
        seen: set[str] = set()
        for part in parts:
            value = part.strip()
            if not value:
                continue
            normalized = self._normalize_url(value)
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            urls.append(normalized)
        return urls

    def import_people_from_urls(
        self,
        raw_urls: str,
        person_type: str,
        state_name: str | None = None,
        chamber_hint: str | None = None,
    ) -> ManualImportResult:
        urls = self.parse_urls(raw_urls)
        result = ManualImportResult(items=[])

        for url in urls:
            try:
                page = self._fetch_page(url)
                final_url = str(page.get("final_url") or url)
                resolved_person_type = person_type
                if person_type == "auto":
                    resolved_person_type = self._infer_person_type(
                        final_url=final_url,
                        title=str(page.get("title") or ""),
                        body=str(page.get("body") or ""),
                    )
                config = self._build_person_type_config(
                    person_type=resolved_person_type,
                    state_name=state_name,
                    chamber_hint=chamber_hint,
                    final_url=final_url,
                    title=str(page.get("title") or ""),
                    body=str(page.get("body") or ""),
                )
                jurisdiction = self.officials_service.get_or_create_jurisdiction(
                    name=str(config["jurisdiction_name"]),
                    jurisdiction_type=str(config["jurisdiction_type"]),
                )
                office = self.officials_service.get_or_create_office(
                    office_name=str(config["office_name"]),
                    level=str(config["level"]),
                    branch=(str(config["branch"]) if config["branch"] else None),
                    chamber=(str(config["chamber"]) if config["chamber"] else None),
                    jurisdiction_id=jurisdiction.id,
                    source_url="manual://url-batch",
                    source_type="manual",
                )
                existing_person = self._find_person_by_url(final_url)
                full_name = existing_person.full_name if existing_person else self._infer_person_name(url=final_url, title=str(page.get("title") or ""))
                person, created = self.officials_service.upsert_person(
                    {
                        "full_name": full_name,
                        "source_url": final_url,
                        "source_type": self._source_type(final_url),
                        "seed_source_type": "manual_url",
                        "profile_status": "seeded",
                        "parser_identity": "manual_url_people_batch_v1",
                        "verification_status": "unverified",
                        "raw_payload": {
                            "seeded_from": "manual_url_people_batch_v1",
                            "manual_input_url": url,
                            "fetched_url": final_url,
                            "fetched_title": str(page.get("title") or ""),
                        },
                    }
                )
                appointment_created = self.officials_service.upsert_appointment(
                    person=person,
                    office=office,
                    jurisdiction_id=jurisdiction.id,
                    payload={
                        "role_title": str(config["role_title"]),
                        "status": "current",
                        "source_url": final_url,
                        "source_type": self._source_type(final_url),
                        "parser_identity": "manual_url_people_batch_v1",
                        "verification_status": "unverified",
                        "is_current": True,
                        "raw_payload": {"manual_input_url": url, "fetched_title": str(page.get("title") or "")},
                    },
                )
                if created:
                    result.created += 1
                else:
                    result.updated += 1
                if appointment_created and not created:
                    result.updated += 1
                result.items.append(
                    {
                        "status": "ok",
                        "url": url,
                        "name": full_name,
                        "person_id": person.id,
                        "created": bool(created),
                        "auto_person_type": resolved_person_type,
                    }
                )
            except (InvalidPersonNameError, ValueError) as exc:
                result.failed += 1
                result.items.append({"status": "failed", "url": url, "error": f"{type(exc).__name__}: {exc}"})
            except Exception as exc:
                result.failed += 1
                result.items.append({"status": "failed", "url": url, "error": f"{type(exc).__name__}: {exc}"})
        return result

    def import_events_from_urls(self, raw_urls: str) -> ManualImportResult:
        urls = self.parse_urls(raw_urls)
        result = ManualImportResult(items=[])
        for url in urls:
            try:
                page = self._fetch_page(url)
                final_url = str(page.get("final_url") or url)
                source_type = self._source_type(final_url)
                full_text = str(page.get("body") or "")
                title = str(page.get("title") or final_url)
                participant_ids = self._match_people(f"{title}\n{full_text}")
                primary_person_id = participant_ids[0] if participant_ids else None
                statement_type = self._statement_type(source_type)
                ai_category = self.ai_assist_service.classify_event_category(title=title, body=full_text, source_url=final_url)
                existing_statement = self._find_statement_by_url(final_url)
                inferred_categories = self._infer_event_categories(primary_person_id)
                if ai_category and ai_category != "other":
                    inferred_categories.add(ai_category)
                if existing_statement:
                    statement = existing_statement
                    statement.title = statement.title or title
                    if len(full_text) > len(statement.full_text or ""):
                        statement.full_text = full_text[:5000]
                    if len(full_text) > len(statement.excerpt or ""):
                        statement.excerpt = full_text[:1200]
                    if not statement.date_published and isinstance(page.get("published_at"), datetime):
                        statement.date_published = page.get("published_at")
                    if not statement.person_id and primary_person_id:
                        statement.person_id = primary_person_id
                    if not self.session.execute(
                        select(StatementSource.id).where(
                            StatementSource.statement_id == statement.id,
                            StatementSource.source_url == final_url,
                        )
                    ).scalar_one_or_none():
                        self.session.add(
                            StatementSource(
                                statement_id=statement.id,
                                source_url=final_url,
                                source_type=source_type,
                                source_title=title,
                                parser_identity="manual_url_events_batch_v1",
                                is_primary=source_type == "official",
                                raw_payload={"manual_input_url": url},
                            )
                        )
                    for person_id in participant_ids:
                        exists = self.session.execute(
                            select(StatementParticipant.id).where(
                                StatementParticipant.statement_id == statement.id,
                                StatementParticipant.person_id == person_id,
                            )
                        ).scalar_one_or_none()
                        if not exists:
                            self.session.add(
                                StatementParticipant(
                                    statement_id=statement.id,
                                    person_id=person_id,
                                    source_url=final_url,
                                    source_type=source_type,
                                )
                            )
                    created = False
                else:
                    statement, created = self.statements_service.ingest_statement(
                        {
                            "person_id": primary_person_id,
                            "participant_ids": participant_ids,
                            "title": title,
                            "source_title": title,
                            "date_published": page.get("published_at") if isinstance(page.get("published_at"), datetime) else None,
                            "source_url": final_url,
                            "source_type": source_type,
                            "statement_type": statement_type,
                            "excerpt": full_text[:1200],
                            "full_text": full_text[:5000],
                            "raw_text": full_text,
                            "is_primary_source": source_type == "official",
                            "parser_identity": "manual_url_events_batch_v1",
                            "raw_payload": {
                                "seeded_from": "manual_url_events_batch_v1",
                                "manual_input_url": url,
                                "matched_person_ids": participant_ids,
                                "auto_categories": sorted(inferred_categories),
                                "auto_category": sorted(inferred_categories)[0] if inferred_categories else "unknown",
                                "ai_auto_category": ai_category,
                            },
                        }
                    )
                if created:
                    result.created += 1
                else:
                    result.updated += 1
                result.items.append(
                    {
                        "status": "ok",
                        "url": url,
                        "statement_id": statement.id,
                        "title": statement.title,
                        "created": bool(created),
                        "matched_people": len(participant_ids),
                        "auto_categories": sorted(inferred_categories),
                        "ai_auto_category": ai_category,
                    }
                )
            except Exception as exc:
                result.failed += 1
                result.items.append({"status": "failed", "url": url, "error": f"{type(exc).__name__}: {exc}"})
        return result

    def import_legislation_from_urls(self, raw_urls: str) -> ManualImportResult:
        urls = self.parse_urls(raw_urls)
        result = ManualImportResult(items=[])
        sync_postgres_id_sequences(
            self.session,
            (
                "legislation",
                "legislation_sources",
                "legislation_sponsors",
                "persons",
                "aliases",
            ),
        )
        for url in urls:
            try:
                page = self._fetch_page(url)
                final_url = str(page.get("final_url") or url)
                title = str(page.get("title") or final_url)
                body = str(page.get("body") or "")
                meta = self._classify_legislation(final_url, title, body)
                ai_details = self.ai_assist_service.extract_legislation_metadata(title=title, body=body, source_url=final_url) or {}
                ai_scope = {}
                if ai_details:
                    ai_scope = {
                        "level": ai_details.get("level"),
                        "chamber": ai_details.get("chamber"),
                        "legislation_type": ai_details.get("legislation_type"),
                    }
                else:
                    ai_scope = self.ai_assist_service.classify_legislation_scope(title=title, body=body, source_url=final_url) or {}

                if ai_scope.get("level") in {"federal", "state", "other"}:
                    meta["level"] = ai_scope["level"]
                if ai_scope.get("chamber") in {"senate", "house"}:
                    meta["chamber"] = ai_scope["chamber"]
                if ai_scope.get("legislation_type") and ai_scope["legislation_type"] != "other":
                    meta["legislation_type"] = ai_scope["legislation_type"]
                if ai_details.get("title"):
                    meta["title"] = ai_details["title"]
                if ai_details.get("bill_number"):
                    meta["bill_number"] = ai_details["bill_number"]
                if ai_details.get("jurisdiction_name"):
                    meta["jurisdiction_name"] = ai_details["jurisdiction_name"]

                existing_legislation = self._find_legislation_by_url(final_url)
                page_date = page.get("published_at").date() if isinstance(page.get("published_at"), datetime) else None
                introduced_date = self._date_from_ai(ai_details.get("introduced_date")) or page_date
                last_action_date = self._date_from_ai(ai_details.get("last_action_date")) or page_date
                taiwan_related = ai_details.get("is_taiwan_related")
                if not isinstance(taiwan_related, bool):
                    taiwan_related = self._is_taiwan_related(f"{title} {body}")
                relevance_score = ai_details.get("relevance_score")
                if not isinstance(relevance_score, (int, float)):
                    relevance_score = 1.0 if taiwan_related else 0.0
                payload = {
                    "title": meta["title"],
                    "bill_number": meta.get("bill_number"),
                    "bill_slug": existing_legislation.bill_slug if existing_legislation else meta.get("bill_slug"),
                    "legislation_type": meta.get("legislation_type"),
                    "level": meta.get("level", "other"),
                    "jurisdiction_name": meta.get("jurisdiction_name"),
                    "chamber": meta.get("chamber"),
                    "summary": ai_details.get("summary") or body[:1800] or None,
                    "status_text": ai_details.get("status_text"),
                    "introduced_date": introduced_date,
                    "last_action_date": last_action_date,
                    "source_url": final_url,
                    "source_type": self._source_type(final_url),
                    "parser_identity": "manual_url_legislation_batch_v1",
                    "relevance_score": float(relevance_score),
                    "is_taiwan_related": bool(taiwan_related),
                    "raw_payload": {
                        "seeded_from": "manual_url_legislation_batch_v1",
                        "manual_input_url": url,
                        "auto_classification": meta,
                        "ai_classification": ai_scope,
                        "ai_extracted_metadata": ai_details,
                    },
                    "sources": [
                        {
                            "source_url": final_url,
                            "source_type": self._source_type(final_url),
                            "source_title": title,
                            "parser_identity": "manual_url_legislation_batch_v1",
                            "raw_payload": {"manual_input_url": url},
                        }
                    ],
                    "sponsors": self._ai_legislation_sponsors(ai_details, final_url),
                }
                legislation, created = self.legislation_service.upsert_legislation(payload)
                self.session.flush()
                if created:
                    result.created += 1
                else:
                    result.updated += 1
                skipped_sponsors = list(payload.get("skipped_sponsors") or [])
                result.items.append(
                    {
                        "status": "ok",
                        "url": url,
                        "legislation_id": legislation.id,
                        "title": legislation.title,
                        "bill_number": legislation.bill_number,
                        "created": bool(created),
                        "ai_classification": ai_scope,
                        "ai_details_used": bool(ai_details),
                        "ai_sponsors": max(0, len(payload["sponsors"]) - len(skipped_sponsors)),
                        "skipped_sponsors": skipped_sponsors,
                    }
                )
            except Exception as exc:
                result.failed += 1
                result.items.append({"status": "failed", "url": url, "error": f"{type(exc).__name__}: {exc}"})
        return result

    def _normalize_url(self, source_url: str) -> str:
        value = str(source_url or "").strip()
        if not value:
            raise ValueError("URL is required.")
        parsed = urlparse(value)
        if not parsed.scheme:
            value = f"https://{value}"
            parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http/https URLs are supported.")
        if not parsed.netloc:
            raise ValueError("Invalid URL.")
        return value

    def _safe_normalize_url(self, source_url: str | None) -> str | None:
        try:
            return self._normalize_url(str(source_url or ""))
        except ValueError:
            return None

    def _fetch_page(self, source_url: str) -> dict[str, object]:
        response = httpx.get(
            source_url,
            timeout=25.0,
            follow_redirects=True,
            trust_env=False,
            headers=self.http_headers,
        )
        response.raise_for_status()
        content_type = str(response.headers.get("content-type") or "").lower()
        final_url = str(response.url)
        if "pdf" in content_type or final_url.lower().split("?", 1)[0].endswith(".pdf"):
            return self._extract_pdf_page(response)
        soup = BeautifulSoup(response.text, "html.parser")
        title = self._extract_title(soup, fallback=final_url)
        published_at = self._extract_published_at(soup)
        body = self._extract_body_text(soup)
        return {
            "final_url": final_url,
            "title": title,
            "published_at": published_at,
            "body": body,
        }

    def _extract_pdf_page(self, response: httpx.Response) -> dict[str, object]:
        final_url = str(response.url)
        title = self._title_from_url(final_url)
        body = ""
        if PdfReader is not None:
            try:
                reader = PdfReader(io.BytesIO(response.content))
                metadata_title = getattr(reader.metadata, "title", None) if reader.metadata else None
                if metadata_title and str(metadata_title).strip():
                    title = compact_whitespace(str(metadata_title))
                pages: list[str] = []
                for page in reader.pages[:60]:
                    page_text = compact_whitespace(page.extract_text() or "")
                    if page_text:
                        pages.append(page_text)
                body = compact_whitespace(" ".join(pages))
            except Exception:
                body = ""
        published_at = parse_datetime(str(response.headers.get("last-modified") or ""))
        return {
            "final_url": final_url,
            "title": title,
            "published_at": published_at,
            "body": body,
        }

    def _title_from_url(self, source_url: str) -> str:
        parsed = urlparse(source_url)
        path = parsed.path.rstrip("/")
        if not path:
            return source_url
        filename = unquote(path.rsplit("/", 1)[-1])
        filename = re.sub(r"\.pdf$", "", filename, flags=re.I)
        filename = filename.replace("-", " ").replace("_", " ").strip()
        return compact_whitespace(filename) or source_url

    def _extract_title(self, soup: BeautifulSoup, fallback: str) -> str:
        meta_candidates = [
            ("meta", {"property": "og:title"}, "content"),
            ("meta", {"name": "twitter:title"}, "content"),
            ("meta", {"name": "title"}, "content"),
            ("meta", {"name": "headline"}, "content"),
        ]
        for tag_name, attrs, key in meta_candidates:
            tag = soup.find(tag_name, attrs=attrs)
            if tag and tag.get(key):
                text = compact_whitespace(str(tag.get(key)))
                if text:
                    return text
        for selector in ("h1", "title", "h2"):
            node = soup.select_one(selector)
            if node:
                text = compact_whitespace(node.get_text(" ", strip=True))
                if text:
                    return text
        return fallback

    def _extract_published_at(self, soup: BeautifulSoup) -> datetime | None:
        selectors = [
            ("meta", {"property": "article:published_time"}, "content"),
            ("meta", {"name": "article:published_time"}, "content"),
            ("meta", {"property": "og:published_time"}, "content"),
            ("meta", {"name": "pubdate"}, "content"),
            ("meta", {"name": "date"}, "content"),
            ("time", {}, "datetime"),
        ]
        for tag_name, attrs, attr in selectors:
            tag = soup.find(tag_name, attrs=attrs) if attrs else soup.find(tag_name)
            if tag and tag.get(attr):
                parsed = parse_datetime(str(tag.get(attr)))
                if parsed:
                    return parsed
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            text = script.get_text(strip=True)
            if not text:
                continue
            try:
                payload = json.loads(text)
            except Exception:
                continue
            for key in ("datePublished", "dateCreated", "uploadDate"):
                value = self._json_find(payload, key)
                if value:
                    parsed = parse_datetime(str(value))
                    if parsed:
                        return parsed
        return None

    def _extract_body_text(self, soup: BeautifulSoup) -> str:
        selector_groups = [
            "article p",
            "main p",
            ".entry-content p",
            ".post-content p",
            ".news p",
            ".content p",
            ".article p",
        ]
        lines: list[str] = []
        for selector in selector_groups:
            for node in soup.select(selector):
                text = compact_whitespace(node.get_text(" ", strip=True))
                if text:
                    lines.append(text)
            if lines:
                break
        if not lines:
            text = compact_whitespace(soup.get_text(" ", strip=True))
            text = re.sub(r"\s+", " ", text).strip()
            return text[:5000]
        merged = compact_whitespace(" ".join(lines))
        return merged[:5000]

    def _infer_person_name(self, url: str, title: str) -> str:
        parsed = urlparse(url)
        if "wikipedia.org" in parsed.netloc and "/wiki/" in parsed.path:
            slug = unquote(parsed.path.split("/wiki/", 1)[1]).replace("_", " ")
            slug = re.sub(r"\s*\([^)]*\)\s*$", "", slug).strip()
            if slug:
                return slug

        candidate = title
        for marker in (" | ", " - ", " — ", " – ", "｜"):
            if marker in candidate:
                candidate = candidate.split(marker, 1)[0].strip()
        candidate = re.sub(r"\b(official|biography|profile|news|home page)\b", "", candidate, flags=re.I)
        candidate = re.sub(r"\s+", " ", candidate).strip(" ,.-")
        if len(candidate.split()) >= 2:
            return candidate

        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts:
            fallback = unquote(path_parts[-1]).replace("-", " ").replace("_", " ").strip()
            fallback = re.sub(r"\s*\([^)]*\)\s*$", "", fallback).strip()
            if len(fallback.split()) >= 2:
                return fallback
        raise ValueError(f"Cannot infer person name from URL/title: {url}")

    def _source_type(self, source_url: str) -> str:
        domain = (urlparse(source_url).netloc or "").lower()
        if "wikipedia.org" in domain:
            return "wikipedia"
        if any(item in domain for item in ("x.com", "twitter.com", "facebook.com", "instagram.com", "youtube.com", "tiktok.com")):
            return "social"
        if is_government_url(source_url):
            return "official"
        return "media"

    def _statement_type(self, source_type: str) -> str:
        if source_type == "social":
            return "social_post"
        if source_type == "official":
            return "official_release"
        if source_type == "media":
            return "media_report"
        return "statement"

    def _find_person_by_url(self, source_url: str) -> Person | None:
        normalized = self._safe_normalize_url(source_url)
        if not normalized:
            return None
        rows = self.session.execute(select(Person).where(Person.source_url.is_not(None))).scalars().all()
        for person in rows:
            if self._safe_normalize_url(person.source_url) == normalized or self._safe_normalize_url(person.canonical_official_url) == normalized:
                return person
        return None

    def _find_statement_by_url(self, source_url: str) -> Statement | None:
        normalized = self._safe_normalize_url(source_url)
        if not normalized:
            return None
        rows = self.session.execute(select(Statement).where(Statement.source_url.is_not(None))).scalars().all()
        for item in rows:
            if self._safe_normalize_url(item.source_url) == normalized:
                return item
        source_rows = self.session.execute(select(StatementSource).where(StatementSource.source_url.is_not(None))).scalars().all()
        for source in source_rows:
            if self._safe_normalize_url(source.source_url) == normalized:
                statement = self.session.get(Statement, source.statement_id)
                if statement:
                    return statement
        return None

    def _find_legislation_by_url(self, source_url: str) -> Legislation | None:
        normalized = self._safe_normalize_url(source_url)
        if not normalized:
            return None
        rows = self.session.execute(select(Legislation).where(Legislation.source_url.is_not(None))).scalars().all()
        for item in rows:
            if self._safe_normalize_url(item.source_url) == normalized:
                return item
        source_rows = self.session.execute(select(LegislationSource).where(LegislationSource.source_url.is_not(None))).scalars().all()
        for source in source_rows:
            if self._safe_normalize_url(source.source_url) == normalized:
                bill = self.session.get(Legislation, source.legislation_id)
                if bill:
                    return bill
        return None

    def _build_people_search_index(self) -> list[tuple[int, str]]:
        rows = self.session.execute(select(Person.id, Person.full_name)).all()
        alias_rows = self.session.execute(select(Alias.person_id, Alias.alias)).all()
        index: list[tuple[int, str]] = []
        for person_id, full_name in rows:
            if full_name:
                index.append((int(person_id), str(full_name).strip()))
        for person_id, alias in alias_rows:
            alias_text = str(alias or "").strip()
            if alias_text:
                index.append((int(person_id), alias_text))
        index = [(person_id, token) for person_id, token in index if token and len(token) >= 2]
        index.sort(key=lambda item: len(item[1]), reverse=True)
        return index

    def _match_people(self, text: str, limit: int = 5) -> list[int]:
        if self._people_search_index is None:
            self._people_search_index = self._build_people_search_index()
        lowered = str(text or "").casefold()
        found: list[int] = []
        for person_id, token in self._people_search_index:
            if token.casefold() in lowered and person_id not in found:
                found.append(person_id)
                if len(found) >= limit:
                    break
        return found

    def _infer_event_categories(self, person_id: int | None) -> set[str]:
        categories: set[str] = set()
        if not person_id:
            return categories
        rows = self.session.execute(
            select(Office.level, Office.branch, Office.chamber)
            .join(Appointment, Appointment.office_id == Office.id)
            .where(Appointment.person_id == person_id)
        ).all()
        for level, branch, chamber in rows:
            if level == "federal" and branch == "executive":
                categories.add("federal_official")
            if level == "federal" and branch == "legislative":
                if chamber == "senate":
                    categories.add("federal_senator")
                elif chamber == "house":
                    categories.add("federal_house")
                categories.add("congress_member")
            if level == "state" and branch == "executive":
                categories.add("state_official")
            if level == "state" and branch == "legislative":
                categories.add("state_legislator")
        return categories

    def _classify_legislation(self, source_url: str, title: str, body: str) -> dict[str, Any]:
        parsed = urlparse(source_url)
        path = parsed.path
        congress_match = self.CONGRESS_BILL_PATTERN.search(path)
        if congress_match:
            bill_type = congress_match.group("bill_type").lower()
            number = congress_match.group("number")
            congress = congress_match.group("congress")
            prefix, chamber, legislation_type = self.BILL_TYPE_INFO.get(bill_type, ("Bill", None, "bill"))
            bill_number = f"{prefix} {number}".strip()
            return {
                "title": title,
                "bill_number": bill_number,
                "bill_slug": f"{congress}-{bill_type}-{number}",
                "legislation_type": legislation_type,
                "level": "federal",
                "jurisdiction_name": "United States",
                "chamber": chamber,
            }

        merged_text = f"{title} {body} {source_url}"
        lower = merged_text.lower()
        level = "state" if any(token in lower for token in ("state senate", "state house", "general assembly", "legislature", ".state.")) else "other"
        bill_number = self._extract_bill_number(title, f"{body} {source_url}")
        chamber = None
        if bill_number and bill_number.upper().startswith(("SB", "SJR", "SCR", "SR")):
            chamber = "senate"
        elif bill_number and bill_number.upper().startswith(("HB", "HJR", "HCR", "HR")):
            chamber = "house"
        elif "senate" in lower:
            chamber = "senate"
        elif "house" in lower or "assembly" in lower:
            chamber = "house"
        jurisdiction = self._extract_state_name(merged_text)
        if "joint resolution" in lower or (bill_number and "JR" in bill_number.upper()):
            legislation_type = "joint_resolution"
        elif "concurrent resolution" in lower or (bill_number and "CR" in bill_number.upper()):
            legislation_type = "concurrent_resolution"
        elif "resolution" in lower or (bill_number and bill_number.upper().endswith("R")):
            legislation_type = "resolution"
        else:
            legislation_type = "bill"
        bill_slug = re.sub(r"[^a-z0-9]+", "-", f"{jurisdiction or 'unknown'}-{bill_number or title}".lower()).strip("-")
        return {
            "title": title,
            "bill_number": bill_number,
            "bill_slug": bill_slug[:240] if bill_slug else None,
            "legislation_type": legislation_type,
            "level": level,
            "jurisdiction_name": jurisdiction,
            "chamber": chamber,
        }

    def _extract_state_name(self, text: str) -> str | None:
        for state in self.STATE_NAMES:
            if re.search(rf"\b{re.escape(state)}\b", text, flags=re.I):
                return state
        lower = str(text or "").lower()
        for pattern in (r"\.state\.([a-z]{2})\.us", r"legiscan\.com/([a-z]{2})/"):
            match = re.search(pattern, lower, flags=re.I)
            if match:
                state = self.STATE_ABBREVIATIONS.get(match.group(1).lower())
                if state:
                    return state
        return None

    def _extract_bill_number(self, title: str, body: str) -> str | None:
        text = f"{title} {body}"
        patterns = [
            r"\b(H\.?\s*R\.?\s*\d+)\b",
            r"\b(S\.?\s*\d+)\b",
            r"\b(H\.?\s*Res\.?\s*\d+)\b",
            r"\b(S\.?\s*Res\.?\s*\d+)\b",
            r"\b((?:HB|SB|HJR|SJR|HCR|SCR|HR|SR)\s*[-.]?\s*\d+)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.I)
            if match:
                normalized = re.sub(r"\s+", " ", match.group(1)).replace(" .", ".").replace(" -", "").strip()
                normalized = re.sub(r"\b(HB|SB|HJR|SJR|HCR|SCR|HR|SR)\s+", r"\1 ", normalized, flags=re.I)
                return normalized
        return None

    def _date_from_ai(self, value: object):
        text = str(value or "").strip()
        if not text:
            return None
        parsed = parse_datetime(text)
        return parsed.date() if parsed else None

    def _ai_legislation_sponsors(self, ai_details: dict[str, Any], source_url: str) -> list[dict[str, Any]]:
        sponsors: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for role, key in (("sponsor", "sponsor_names"), ("cosponsor", "cosponsor_names")):
            for name in ai_details.get(key) or []:
                full_name = str(name or "").strip()
                if not full_name:
                    continue
                dedupe_key = (role, full_name.casefold())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                sponsors.append(
                    {
                        "full_name": full_name,
                        "role": role,
                        "source_url": source_url,
                        "source_type": self._source_type(source_url),
                    }
                )
        return sponsors

    def _is_taiwan_related(self, text: str) -> bool:
        lowered = str(text or "").lower()
        return any(keyword in lowered for keyword in ("taiwan", "台灣", "臺灣", "台海"))

    def _build_person_type_config(
        self,
        person_type: str,
        state_name: str | None,
        chamber_hint: str | None,
        final_url: str,
        title: str,
        body: str,
    ) -> dict[str, str | None]:
        config = dict(self.PERSON_TYPE_CONFIG.get(person_type) or self.PERSON_TYPE_CONFIG["federal_official"])
        if str(person_type).startswith("state_"):
            inferred_state = self._extract_state_name(f"{title} {body} {final_url}")
            config["jurisdiction_name"] = (state_name or "").strip() or inferred_state or "Unknown State"
            if person_type == "state_legislator":
                normalized_hint = str(chamber_hint or "").strip().lower()
                if normalized_hint not in {"senate", "house"}:
                    merged = f"{title} {body} {final_url}".lower()
                    if "senate" in merged:
                        normalized_hint = "senate"
                    elif "house" in merged or "assembly" in merged:
                        normalized_hint = "house"
                if normalized_hint in {"senate", "house"}:
                    config["chamber"] = normalized_hint
                    config["office_name"] = f"{config['jurisdiction_name']} {normalized_hint.title()}"
                    config["role_title"] = "State Senator" if normalized_hint == "senate" else "State Representative"
                else:
                    config["office_name"] = f"{config['jurisdiction_name']} Legislature"
        return config

    def _infer_person_type(self, final_url: str, title: str, body: str) -> str:
        ai_result = self.ai_assist_service.classify_person_type(title=title, body=body, source_url=final_url)
        if ai_result:
            return ai_result
        merged = f"{title} {body} {final_url}".lower()
        if "senate.gov" in final_url.lower() or "u.s. senator" in merged or " united states senator" in merged:
            return "federal_senator"
        if "house.gov" in final_url.lower() or "u.s. representative" in merged or " united states representative" in merged:
            return "federal_house"
        if "governor" in merged or "state secretary" in merged or "attorney general" in merged:
            return "state_official" if "state" in merged else "federal_official"
        if "state senate" in merged or "state house" in merged or "general assembly" in merged or "state legislature" in merged:
            return "state_legislator"
        if self._extract_state_name(merged):
            return "state_official"
        return "federal_official"

    def _json_find(self, payload: object, target_key: str) -> str | None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if str(key) == target_key and isinstance(value, (str, int, float)):
                    return str(value)
                nested = self._json_find(value, target_key)
                if nested:
                    return nested
        elif isinstance(payload, list):
            for item in payload:
                nested = self._json_find(item, target_key)
                if nested:
                    return nested
        return None
