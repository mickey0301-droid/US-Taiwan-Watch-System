from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from tracker.services.statements_service import StatementsService
from tracker.utils.source_types import is_government_url
from tracker.utils.text import compact_whitespace
from tracker.utils.web import parse_datetime


class ManualStatementIngestService:
    http_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def __init__(self, session: Session) -> None:
        self.session = session
        self.statements_service = StatementsService(session)

    def ingest_from_url(self, person_id: int, source_url: str) -> tuple[object, bool]:
        normalized_url = self._normalize_url(source_url)
        page = self._fetch_page(normalized_url)
        payload = self._build_payload(person_id=person_id, source_url=normalized_url, page=page)
        return self.statements_service.ingest_statement(payload)

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

    def _fetch_page(self, source_url: str) -> dict[str, object]:
        response = httpx.get(
            source_url,
            timeout=25.0,
            follow_redirects=True,
            trust_env=False,
            headers=self.http_headers,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title = self._extract_title(soup, fallback=str(response.url))
        published_at = self._extract_published_at(soup)
        body = self._extract_body_text(soup)
        return {
            "final_url": str(response.url),
            "title": title,
            "published_at": published_at,
            "body": body,
        }

    def _build_payload(self, person_id: int, source_url: str, page: dict[str, object]) -> dict[str, object]:
        final_url = str(page.get("final_url") or source_url)
        source_type = self._source_type(final_url)
        statement_type = "social_post" if source_type == "social" else "statement"
        title = str(page.get("title") or final_url)
        body = str(page.get("body") or "")
        date_published = page.get("published_at")
        return {
            "person_id": person_id,
            "participant_ids": [person_id],
            "title": title,
            "source_title": title,
            "date_published": date_published if isinstance(date_published, datetime) else None,
            "source_url": final_url,
            "source_type": source_type,
            "statement_type": statement_type,
            "excerpt": body[:1000],
            "full_text": body[:5000],
            "raw_text": body,
            "is_primary_source": source_type != "media",
            "parser_identity": "manual_url_ingest_v1",
            "raw_payload": {
                "seeded_from": "manual_url_ingest_v1",
                "manual_input_url": source_url,
                "fetched_url": final_url,
                "fetched_at": datetime.utcnow().isoformat(),
            },
        }

    def _source_type(self, source_url: str) -> str:
        domain = (urlparse(source_url).netloc or "").lower()
        if any(item in domain for item in ("x.com", "twitter.com", "facebook.com", "instagram.com", "youtube.com", "tiktok.com")):
            return "social"
        if is_government_url(source_url):
            return "official"
        return "media"

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
        time_node = soup.find("time")
        if time_node:
            parsed = parse_datetime(time_node.get_text(" ", strip=True))
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

