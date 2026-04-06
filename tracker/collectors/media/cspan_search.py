from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from tracker.utils.text import compact_whitespace
from tracker.utils.web import absolute_url, parse_datetime


class CSpanSearchCollector:
    parser_identity = "cspan_search_v1"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def search(self, target_url: str, person_id: int, tracker_id: int, tracker_target_id: int) -> list[dict[str, Any]]:
        response = httpx.get(
            target_url,
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
            headers=self.headers,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        candidates: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href:
                continue
            absolute = absolute_url(target_url, href)
            if absolute.rstrip("/") == target_url.rstrip("/"):
                continue
            absolute_lower = absolute.lower()
            if "c-span.org" not in absolute_lower:
                continue
            if not any(segment in absolute_lower for segment in ["/video/", "/person/", "/search/", "/clip/"]):
                continue
            if absolute in seen_urls:
                continue
            seen_urls.add(absolute)
            title = compact_whitespace(anchor.get_text(" ", strip=True) or anchor.get("title", "") or absolute)
            if len(title) < 4:
                continue
            page_type = self._page_type_from_url(absolute)
            summary, published_at = self._extract_context(anchor, absolute)
            detail_summary, detail_published_at = self._extract_from_detail_page(absolute, page_type)
            summary = detail_summary or summary
            published_at = detail_published_at or published_at
            combined_text = f"{title}\n{summary or ''}".lower()
            if page_type in {"cspan_person_page", "cspan_search_result"} and "taiwan" not in combined_text:
                continue
            excerpt = summary or title
            full_text = f"{title}\n\n{summary}" if summary and summary != title else title
            candidates.append(
                {
                    "person_id": person_id,
                    "tracker_id": tracker_id,
                    "tracker_target_id": tracker_target_id,
                    "title": title,
                    "date_published": published_at,
                    "source_url": absolute,
                    "source_type": "cspan",
                    "statement_type": "cspan_search_target",
                    "excerpt": excerpt,
                    "full_text": full_text,
                    "raw_text": full_text,
                    "source_title": title,
                    "parser_identity": self.parser_identity,
                    "is_primary_source": False,
                    "raw_payload": {
                        "search_url": target_url,
                        "page_type": page_type,
                        "summary": summary,
                        "detail_enriched": bool(detail_summary or detail_published_at),
                        "published_at_text": published_at.isoformat() if published_at else None,
                    },
                }
            )
            if len(candidates) >= 25:
                break
        return candidates

    def _extract_context(self, anchor: BeautifulSoup, absolute_url_value: str) -> tuple[str | None, datetime | None]:
        container = self._find_card_container(anchor)
        if not container:
            return None, None

        published_at = self._extract_date(container)
        summary = self._extract_summary(container, anchor)
        return summary, published_at

    def _find_card_container(self, anchor: BeautifulSoup) -> BeautifulSoup | None:
        current = anchor
        for _ in range(6):
            parent = current.parent
            if not parent:
                break
            classes = " ".join(parent.get("class", []))
            tag_name = getattr(parent, "name", "") or ""
            if any(token in classes.lower() for token in ["card", "result", "video", "listing", "search"]) or tag_name in {"article", "li"}:
                return parent
            current = parent
        return anchor.parent if anchor.parent else None

    def _extract_summary(self, container: BeautifulSoup, anchor: BeautifulSoup) -> str | None:
        selectors = [
            "p",
            ".description",
            ".result-description",
            ".search-result-description",
            ".overview",
            ".deck",
        ]
        anchor_text = compact_whitespace(anchor.get_text(" ", strip=True))
        for selector in selectors:
            node = container.select_one(selector)
            if not node:
                continue
            text = compact_whitespace(node.get_text(" ", strip=True))
            if text and text != anchor_text:
                return text[:1200]

        text = compact_whitespace(container.get_text(" ", strip=True))
        if text and text != anchor_text:
            collapsed = re.sub(r"\s+", " ", text)
            if anchor_text in collapsed:
                collapsed = collapsed.replace(anchor_text, "", 1).strip(" |-")
            if collapsed:
                return collapsed[:1200]
        return None

    def _extract_date(self, container: BeautifulSoup) -> datetime | None:
        for time_node in container.find_all("time"):
            datetime_value = time_node.get("datetime")
            parsed = parse_datetime(datetime_value) or parse_datetime(compact_whitespace(time_node.get_text(" ", strip=True)))
            if parsed:
                return parsed

        text = compact_whitespace(container.get_text(" ", strip=True))
        patterns = [
            r"\b[A-Z][a-z]+ \d{1,2}, \d{4}\b",
            r"\b[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}\b",
            r"\b\d{4}-\d{2}-\d{2}\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                parsed = parse_datetime(match.group(0))
                if parsed:
                    return parsed
                for fmt in ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(match.group(0), fmt)
                    except ValueError:
                        continue
        return None

    def _page_type_from_url(self, url: str) -> str:
        lowered = url.lower()
        if "/video/" in lowered:
            return "cspan_video"
        if "/clip/" in lowered:
            return "cspan_clip"
        if "/person/" in lowered:
            return "cspan_person_page"
        return "cspan_search_result"

    def _extract_from_detail_page(self, url: str, page_type: str) -> tuple[str | None, datetime | None]:
        if page_type not in {"cspan_video", "cspan_clip"}:
            return None, None
        try:
            response = httpx.get(
                url,
                timeout=20.0,
                follow_redirects=True,
                trust_env=False,
                headers=self.headers,
            )
            response.raise_for_status()
        except Exception:
            return None, None

        soup = BeautifulSoup(response.text, "lxml")
        summary = self._extract_detail_summary(soup)
        published_at = self._extract_detail_date(soup)
        return summary, published_at

    def _extract_detail_summary(self, soup: BeautifulSoup) -> str | None:
        selectors = [
            "meta[name='description']",
            "meta[property='og:description']",
            ".program-description",
            ".video-description",
            ".episode-description",
            ".description",
            "article p",
            "main p",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if not node:
                continue
            if node.name == "meta":
                content = compact_whitespace(node.get("content", ""))
                if content:
                    return content[:1200]
                continue
            text = compact_whitespace(node.get_text(" ", strip=True))
            if text:
                return text[:1200]
        return None

    def _extract_detail_date(self, soup: BeautifulSoup) -> datetime | None:
        for selector in [
            "meta[property='article:published_time']",
            "meta[name='date']",
            "meta[itemprop='uploadDate']",
        ]:
            node = soup.select_one(selector)
            if node and node.get("content"):
                parsed = parse_datetime(compact_whitespace(node.get("content", "")))
                if parsed:
                    return parsed

        for time_node in soup.find_all("time"):
            parsed = parse_datetime(time_node.get("datetime")) or parse_datetime(compact_whitespace(time_node.get_text(" ", strip=True)))
            if parsed:
                return parsed

        body_text = compact_whitespace(soup.get_text(" ", strip=True))
        patterns = [
            r"\b[A-Z][a-z]+ \d{1,2}, \d{4}\b",
            r"\b[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}\b",
            r"\b\d{4}-\d{2}-\d{2}\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, body_text)
            if match:
                parsed = parse_datetime(match.group(0))
                if parsed:
                    return parsed
                for fmt in ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(match.group(0), fmt)
                    except ValueError:
                        continue
        return None
