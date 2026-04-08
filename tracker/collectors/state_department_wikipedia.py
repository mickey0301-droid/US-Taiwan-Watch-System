from __future__ import annotations

from datetime import datetime
from html import unescape
import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from tracker.collectors.base import BaseCollector, CollectorRunResult
from tracker.config import get_settings
from tracker.db import session_scope
from tracker.logging_utils import get_logger
from tracker.models import SyncRun
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService
from tracker.services.roster_service import RosterService
from tracker.utils.social import discover_social_profiles


logger = get_logger(__name__)

TARGET_EXECUTIVE_TITLE_KEYWORDS = (
    "principal deputy assistant secretary",
    "deputy assistant secretary",
    "assistant secretary",
)
STATE_DEPARTMENT_BIOGRAPHIES_LIST_URL = "https://www.state.gov/biographies-list/"
STATE_DEPARTMENT_BIOGRAPHIES_WP_API_URL = "https://www.state.gov/wp-json/wp/v2/state_biography?per_page=100&page={page}"
STATE_DEPARTMENT_BIOGRAPHIES_FEED_URL = "https://www.state.gov/biographies-list/feed/"
STATE_DEPARTMENT_SITEMAP_INDEX_URL = "https://www.state.gov/sitemap_index.xml"
STATE_DEPARTMENT_BIOGRAPHIES_MAX_PAGES = 24
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class _BaseStateDepartmentWikipediaCollector(BaseCollector):
    source_type = "wikipedia"
    department_name = "Department of State"
    subdepartment_name = ""

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> str:
        response = httpx.get(
            self.source_url,
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
            headers=REQUEST_HEADERS,
        )
        response.raise_for_status()
        if self.settings.snapshot_raw_responses:
            snapshot_dir = Path(self.settings.snapshots_dir)
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            (snapshot_dir / f"{self.collector_name}_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(
                response.text,
                encoding="utf-8",
            )
        return response.text

    def parse(self, payload: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(payload, "html.parser")
        content = soup.select_one("#mw-content-text .mw-parser-output") or soup
        parsed: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for table in content.select("table.wikitable"):
            headers = [th.get_text(" ", strip=True).lower() for th in table.select("tr th")]
            if "office" not in headers or "incumbent" not in headers:
                continue
            parsed.extend(self._parse_current_table(table, seen))
        return parsed

    def sync(self) -> CollectorRunResult:
        result = CollectorRunResult(job_name=self.collector_name, source_name=self.source_name, started_at=datetime.utcnow())
        with session_scope() as session:
            sync_run = SyncRun(job_name=self.collector_name, job_type="collector", source_name=self.source_name)
            session.add(sync_run)
            session.flush()
            service: OfficialsService | None = None
            try:
                records = self.parse(self.fetch())
                service = OfficialsService(session)
                roster_service = RosterService(session)
                current_term = roster_service.presidential_term_roster(47)
                result.records_found = len(records)
                usa = service.get_or_create_jurisdiction("United States", "country", code="US")
                seen_keys: set[tuple[int, int, int | None, str]] = set()
                for record in records:
                    office = service.get_or_create_office(
                        record["office"]["office_name"],
                        record["office"]["level"],
                        record["office"].get("branch"),
                        record["office"].get("chamber"),
                        usa.id,
                        record["office"]["source_url"],
                        record["office"]["source_type"],
                    )
                    try:
                        person, created = service.upsert_person(record["person"])
                    except InvalidPersonNameError as exc:
                        result.errors.append(str(exc))
                        continue
                    result.records_created += 1 if created else 0
                    result.records_updated += 0 if created else 1
                    for alias in record.get("aliases", []):
                        service.ensure_alias(person.id, alias, record["person"]["source_url"], record["person"]["source_type"])
                    if roster_service.ensure_membership(
                        current_term,
                        person,
                        office,
                        usa.id,
                        record["appointment"]["role_title"],
                        record["appointment"].get("party"),
                        record["appointment"].get("status"),
                        record["appointment"].get("source_url"),
                        record["appointment"].get("source_type"),
                        record["appointment"].get("parser_identity"),
                        raw_payload=record["appointment"].get("raw_payload"),
                    ):
                        result.records_created += 1
                    if service.upsert_appointment(person, office, usa.id, record["appointment"]):
                        result.records_created += 1
                    seen_keys.add((person.id, office.id, usa.id, record["appointment"]["role_title"]))
                result.records_deactivated = service.reconcile_current_appointments(self.parser_identity, seen_keys)
                sync_run.status = "success"
            except Exception as exc:
                logger.exception("%s failed.", self.collector_name)
                result.errors.append(str(exc))
                sync_run.status = "failed"
                sync_run.error_message = str(exc)
            finally:
                validation_log = service.validation_log if service else []
                result.metadata["validation_log"] = validation_log
                result.metadata["validation_count"] = len(validation_log)
                result.ended_at = datetime.utcnow()
                sync_run.started_at = result.started_at
                sync_run.ended_at = result.ended_at
                sync_run.records_found = result.records_found
                sync_run.records_created = result.records_created
                sync_run.records_updated = result.records_updated
                sync_run.records_deactivated = result.records_deactivated
                sync_run.meta = {
                    "errors": result.errors,
                    "validation_log": validation_log,
                    "validation_count": len(validation_log),
                    "records_deactivated": result.records_deactivated,
                }
        return result

    def _parse_current_table(self, table: Tag, seen: set[tuple[str, str]]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for row in table.select("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            office_title = " ".join(cells[0].get_text(" ", strip=True).split())
            if not office_title or office_title.lower() in {"office", "under secretaries of state", "assistant secretaries of state"}:
                continue
            full_name, person_url = self._extract_person_info(cells[1])
            if not full_name:
                continue
            office_name = f"{self.department_name}: {office_title}"
            key = (full_name, office_name)
            if key in seen:
                continue
            seen.add(key)
            person_page_data = self._fetch_person_page(person_url) if person_url else {}
            parsed.append(
                {
                    "person": {
                        "full_name": full_name,
                        "source_url": person_url or self.source_url,
                        "source_type": "wikipedia",
                        "seed_source_type": "wikipedia",
                        "profile_status": "seeded",
                        "portrait_url": person_page_data.get("portrait_url"),
                        "portrait_source_url": person_url if person_page_data.get("portrait_url") else None,
                        "portrait_source_type": "wikipedia" if person_page_data.get("portrait_url") else None,
                        "social_profiles": person_page_data.get("social_profiles") or {},
                        "parser_identity": self.parser_identity,
                        "verification_status": "unverified",
                        "raw_payload": {
                            "wikipedia_url": person_url,
                            "source_page": self.source_url,
                            "top_department_name": self.department_name,
                            "subdepartment_name": self.subdepartment_name,
                            "department_name": self.department_name,
                            "office_title": office_title,
                        },
                    },
                    "jurisdiction": {"name": "United States", "type": "country", "code": "US"},
                    "office": {
                        "office_name": office_name,
                        "level": "federal",
                        "branch": "executive",
                        "chamber": None,
                        "source_url": self.source_url,
                        "source_type": "wikipedia",
                    },
                    "appointment": {
                        "role_title": office_name,
                        "status": "current",
                        "source_url": self.source_url,
                        "source_type": "wikipedia",
                        "parser_identity": self.parser_identity,
                        "is_current": True,
                        "raw_payload": {
                            "top_department_name": self.department_name,
                            "subdepartment_name": self.subdepartment_name,
                            "department_name": self.department_name,
                            "office_title": office_title,
                            "wikipedia_person_url": person_url,
                        },
                    },
                    "aliases": [full_name],
                }
            )
        return parsed

    def _extract_person_info(self, cell: Tag) -> tuple[str | None, str | None]:
        text = " ".join(cell.get_text(" ", strip=True).split())
        if not text:
            return None, None
        cleaned = text.replace("(Acting)", "").replace("Acting", "").strip(" ,;")
        if not cleaned:
            return None, None
        anchor = None
        for candidate in cell.find_all("a", href=True):
            href = candidate.get("href", "")
            if "/wiki/" in href and ":" not in href.split("/wiki/")[-1]:
                anchor = candidate
                break
        if anchor:
            return " ".join(anchor.get_text(" ", strip=True).split()), urljoin(self.source_url, anchor["href"].strip())
        return cleaned, None

    def _is_target_executive_title(self, office_title: str | None) -> bool:
        normalized = " ".join((office_title or "").lower().split())
        return any(keyword in normalized for keyword in TARGET_EXECUTIVE_TITLE_KEYWORDS)

    def _fetch_person_page(self, person_url: str) -> dict[str, Any]:
        try:
            response = httpx.get(
                person_url,
                timeout=20.0,
                follow_redirects=True,
                trust_env=False,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            response.raise_for_status()
        except Exception:
            return {}
        soup = BeautifulSoup(response.text, "html.parser")
        portrait_url = None
        infobox = soup.select_one("table.infobox")
        if infobox:
            image = infobox.select_one("img")
            if image and image.get("src"):
                portrait_url = urljoin(person_url, image["src"].strip())
        return {
            "portrait_url": portrait_url,
            "social_profiles": discover_social_profiles(person_url, soup),
        }


class StateDepartmentUnderSecretariesWikipediaCollector(_BaseStateDepartmentWikipediaCollector):
    collector_name = "state_department_under_secretaries_wikipedia"
    source_name = "Wikipedia State Department under secretaries"
    source_url = "https://en.wikipedia.org/wiki/United_States_Under_Secretary_of_State"
    parser_identity = "wikipedia_state_under_secretaries_v1"
    subdepartment_name = "Under Secretaries"


class StateDepartmentAssistantSecretariesWikipediaCollector(_BaseStateDepartmentWikipediaCollector):
    collector_name = "state_department_assistant_secretaries_wikipedia"
    source_name = "Wikipedia State Department assistant secretaries"
    source_url = "https://en.wikipedia.org/wiki/United_States_Assistant_Secretary_of_State"
    parser_identity = "wikipedia_state_assistant_secretaries_v1"
    subdepartment_name = "Assistant Secretaries"


class StateDepartmentBiographiesOfficialCollector(_BaseStateDepartmentWikipediaCollector):
    collector_name = "state_department_biographies_official"
    source_name = "State.gov biographies list"
    source_url = STATE_DEPARTMENT_BIOGRAPHIES_LIST_URL
    parser_identity = "state_gov_biographies_v1"
    source_type = "official"
    subdepartment_name = "Department leadership"

    def fetch(self) -> str:
        return ""

    def parse(self, payload: str) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        seen_people_offices: set[tuple[str, str]] = set()
        biography_items = self._fetch_biographies_from_wp_api()
        if not biography_items:
            biography_items = self._fetch_biographies_from_feed()
        sitemap_urls = self._fetch_biography_urls_from_sitemap()
        if sitemap_urls:
            existing_links = {
                (self._normalize_biography_url(str(item.get("link") or "")) or str(item.get("link") or "").strip())
                for item in biography_items
            }
            for url in sitemap_urls:
                if url not in existing_links:
                    biography_items.append({"link": url})
        if not biography_items:
            biography_urls = self._collect_biography_urls(payload)
            biography_items = [{"link": url} for url in biography_urls]
        for biography_item in biography_items:
            page_data = self._build_page_data_from_biography_item(biography_item)
            full_name = page_data.get("full_name") or ""
            role_title = page_data.get("role_title") or ""
            biography_url = page_data.get("biography_url") or ""
            if not full_name or not role_title:
                continue
            office_name = f"{self.department_name}: {role_title}"
            unique_key = (full_name, office_name)
            if unique_key in seen_people_offices:
                continue
            seen_people_offices.add(unique_key)
            parsed.append(
                {
                    "person": {
                        "full_name": full_name,
                        "source_url": biography_url or self.source_url,
                        "source_type": "official",
                        "seed_source_type": "official",
                        "profile_status": "seeded",
                        "portrait_url": page_data.get("portrait_url"),
                        "portrait_source_url": biography_url if page_data.get("portrait_url") else None,
                        "portrait_source_type": "official" if page_data.get("portrait_url") else None,
                        "social_profiles": page_data.get("social_profiles") or {},
                        "parser_identity": self.parser_identity,
                        "verification_status": "unverified",
                        "raw_payload": {
                            "official_bio_url": biography_url,
                            "source_page": self.source_url,
                            "top_department_name": self.department_name,
                            "subdepartment_name": self.subdepartment_name,
                            "department_name": self.department_name,
                            "office_title": role_title,
                        },
                    },
                    "jurisdiction": {"name": "United States", "type": "country", "code": "US"},
                    "office": {
                        "office_name": office_name,
                        "level": "federal",
                        "branch": "executive",
                        "chamber": None,
                        "source_url": biography_url,
                        "source_type": "official",
                    },
                    "appointment": {
                        "role_title": office_name,
                        "status": "current",
                        "source_url": biography_url,
                        "source_type": "official",
                        "parser_identity": self.parser_identity,
                        "is_current": True,
                        "raw_payload": {
                            "top_department_name": self.department_name,
                            "subdepartment_name": self.subdepartment_name,
                            "department_name": self.department_name,
                            "office_title": role_title,
                            "official_bio_url": biography_url,
                        },
                    },
                    "aliases": [full_name],
                }
            )
        return parsed

    def _fetch_biographies_from_wp_api(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        total_pages = 1
        for page in range(1, STATE_DEPARTMENT_BIOGRAPHIES_MAX_PAGES + 1):
            if page > total_pages:
                break
            url = STATE_DEPARTMENT_BIOGRAPHIES_WP_API_URL.format(page=page)
            try:
                response = httpx.get(
                    url,
                    timeout=30.0,
                    follow_redirects=True,
                    trust_env=False,
                    headers=REQUEST_HEADERS,
                )
                response.raise_for_status()
                batch = response.json()
            except Exception:
                break
            if not isinstance(batch, list) or not batch:
                break
            if page == 1:
                header_total_pages = response.headers.get("X-WP-TotalPages", "").strip()
                if header_total_pages.isdigit():
                    total_pages = min(int(header_total_pages), STATE_DEPARTMENT_BIOGRAPHIES_MAX_PAGES)
            items.extend(batch)
        return items

    def _fetch_biographies_from_feed(self) -> list[dict[str, Any]]:
        xml = self._fetch_html(STATE_DEPARTMENT_BIOGRAPHIES_FEED_URL)
        if not xml:
            return []
        soup = BeautifulSoup(xml, "xml")
        items: list[dict[str, Any]] = []
        for item in soup.select("channel > item"):
            title_node = item.find("title")
            link_node = item.find("link")
            title = (title_node.get_text(" ", strip=True) if title_node else "").strip()
            link = (link_node.get_text(" ", strip=True) if link_node else "").strip()
            if not title or not link:
                continue
            items.append({"title": title, "link": link})
        return items

    def _fetch_biography_urls_from_sitemap(self) -> list[str]:
        index_xml = self._fetch_html(STATE_DEPARTMENT_SITEMAP_INDEX_URL)
        if not index_xml:
            return []
        index_soup = BeautifulSoup(index_xml, "xml")
        sitemap_urls: list[str] = []
        for loc in index_soup.select("sitemapindex sitemap loc"):
            url = (loc.get_text(" ", strip=True) or "").strip()
            if "state_biography-sitemap" in url:
                sitemap_urls.append(url)
        if not sitemap_urls:
            return []

        biography_urls: list[str] = []
        seen_urls: set[str] = set()
        for sitemap_url in sitemap_urls:
            sitemap_xml = self._fetch_html(sitemap_url)
            if not sitemap_xml:
                continue
            soup = BeautifulSoup(sitemap_xml, "xml")
            for loc in soup.select("urlset url loc"):
                raw = (loc.get_text(" ", strip=True) or "").strip()
                normalized = self._normalize_biography_url(raw)
                if not normalized or normalized in seen_urls:
                    continue
                seen_urls.add(normalized)
                biography_urls.append(normalized)
        return biography_urls

    def _build_page_data_from_biography_item(self, item: dict[str, Any]) -> dict[str, Any]:
        biography_url = self._normalize_biography_url(str(item.get("link") or "")) or str(item.get("link") or "").strip()
        full_name = self._extract_full_name_from_item(item)
        role_title = self._extract_role_title_from_item(item)
        portrait_url = self._extract_portrait_url_from_item(item)
        if not full_name and biography_url:
            fallback = self._fetch_biography_page_data(biography_url)
            return {
                "full_name": fallback.get("full_name"),
                "role_title": fallback.get("role_title"),
                "portrait_url": fallback.get("portrait_url"),
                "social_profiles": fallback.get("social_profiles") or {},
                "biography_url": biography_url,
            }
        return {
            "full_name": full_name,
            "role_title": role_title or "State Department official",
            "portrait_url": portrait_url,
            "social_profiles": {},
            "biography_url": biography_url,
        }

    def _fetch_html(self, url: str) -> str | None:
        try:
            response = httpx.get(
                url,
                timeout=30.0,
                follow_redirects=True,
                trust_env=False,
                headers=REQUEST_HEADERS,
            )
            response.raise_for_status()
            return response.text
        except Exception:
            return None

    def _collect_biography_urls(self, first_page_html: str) -> list[str]:
        urls: list[str] = []
        seen_urls: set[str] = set()
        next_url: str | None = self.source_url
        cached_first_page = first_page_html
        visited_pages: set[str] = set()
        for _ in range(STATE_DEPARTMENT_BIOGRAPHIES_MAX_PAGES):
            if not next_url or next_url in visited_pages:
                break
            visited_pages.add(next_url)
            html = cached_first_page if next_url == self.source_url else self._fetch_html(next_url)
            if not html:
                break
            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.select("a[href]"):
                absolute_url = urljoin(next_url, anchor.get("href", "").strip())
                normalized = self._normalize_biography_url(absolute_url)
                if not normalized or normalized in seen_urls:
                    continue
                seen_urls.add(normalized)
                urls.append(normalized)
            next_anchor = soup.select_one('a[rel="next"]')
            if not next_anchor:
                next_link_tag = soup.select_one('link[rel="next"]')
                next_href = next_link_tag.get("href", "").strip() if next_link_tag else ""
            else:
                next_href = next_anchor.get("href", "").strip()
            next_url = urljoin(next_url, next_href) if next_href else None
        return urls

    def _normalize_biography_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        if parsed.netloc.lower() != "www.state.gov":
            return None
        path = (parsed.path or "").strip("/")
        if not path.startswith("biographies/"):
            return None
        slug = path.removeprefix("biographies/").strip("/")
        if not slug or "/" in slug:
            return None
        if slug in {"feed"}:
            return None
        return f"https://www.state.gov/biographies/{slug}/"

    def _fetch_biography_page_data(self, biography_url: str) -> dict[str, Any]:
        html = self._fetch_html(biography_url)
        if not html:
            return {}
        soup = BeautifulSoup(html, "html.parser")
        role_title = self._extract_role_title(soup)
        full_name = self._extract_full_name(soup)
        if not full_name:
            return {}
        portrait_url = self._extract_portrait_url(soup)
        return {
            "full_name": full_name,
            "role_title": role_title or "State Department official",
            "portrait_url": portrait_url,
            "social_profiles": discover_social_profiles(biography_url, soup),
        }

    def _extract_role_title(self, soup: BeautifulSoup) -> str | None:
        headline = soup.select_one("section.biography-header h1")
        if not headline:
            headline = soup.select_one("article h1")
        if not headline:
            return None
        title = " ".join(headline.get_text(" ", strip=True).split())
        return title or None

    def _extract_role_title_from_item(self, item: dict[str, Any]) -> str | None:
        acf = item.get("acf") or {}
        rep_positions = acf.get("rep_positions") if isinstance(acf, dict) else None
        if isinstance(rep_positions, list):
            for position in rep_positions:
                if not isinstance(position, dict):
                    continue
                text_position = " ".join(str(position.get("text_position") or "").split())
                if text_position:
                    return text_position
        content_obj = item.get("content") if isinstance(item.get("content"), dict) else {}
        rendered = str(content_obj.get("rendered") or "")
        if rendered:
            soup = BeautifulSoup(rendered, "html.parser")
            paragraph = soup.select_one("p")
            if paragraph:
                text = " ".join(paragraph.get_text(" ", strip=True).split())
                marker = " serves as "
                lower = text.lower()
                idx = lower.find(marker)
                if idx >= 0:
                    role = text[idx + len(marker):].split(".", 1)[0].strip(" ,;")
                    if role:
                        return role
        return None

    def _extract_full_name(self, soup: BeautifulSoup) -> str | None:
        og_title = soup.select_one('meta[property="og:title"]')
        candidate = (og_title.get("content", "") if og_title else "").strip()
        if not candidate:
            title_tag = soup.select_one("title")
            candidate = title_tag.get_text(" ", strip=True) if title_tag else ""
        if " - United States Department of State" in candidate:
            candidate = candidate.replace(" - United States Department of State", "").strip()
        candidate = " ".join(candidate.split())
        if candidate.lower() in {"biographies", "biographies list"}:
            return None
        return candidate or None

    def _extract_full_name_from_item(self, item: dict[str, Any]) -> str | None:
        raw_title = item.get("title")
        if isinstance(raw_title, dict):
            rendered = unescape(str(raw_title.get("rendered") or "")).strip()
        else:
            rendered = unescape(str(raw_title or "")).strip()
        rendered = " ".join(BeautifulSoup(rendered, "html.parser").get_text(" ", strip=True).split())
        return rendered or None

    def _extract_portrait_url(self, soup: BeautifulSoup) -> str | None:
        candidate_selectors = [
            "section.biography-header img[src]",
            ".profile-card__image img[src]",
            "article img[src]",
        ]
        for selector in candidate_selectors:
            image = soup.select_one(selector)
            if not image:
                continue
            src = (image.get("src") or image.get("data-src") or "").strip()
            if not src:
                continue
            url = urljoin(self.source_url, src)
            lowered = url.lower()
            if "seal_only" in lowered or "dos_seal" in lowered:
                continue
            return url

        og_image = soup.select_one('meta[property="og:image"]')
        url = (og_image.get("content", "") if og_image else "").strip()
        if not url:
            return None
        lowered = url.lower()
        if "seal_only" in lowered or "dos_seal" in lowered:
            return None
        return url

    def _extract_portrait_url_from_item(self, item: dict[str, Any]) -> str | None:
        acf = item.get("acf") or {}
        if not isinstance(acf, dict):
            return None
        portrait = acf.get("img_profile-thumbnail")
        if not isinstance(portrait, dict):
            return None
        url = str(portrait.get("url") or "").strip()
        if not url:
            return None
        lowered = url.lower()
        if "seal_only" in lowered or "dos_seal" in lowered:
            return None
        return url


STATE_DEPARTMENT_ORG_LINKS = [
    {"title": "Bureau of African Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_African_Affairs", "subdepartment": "Under Secretary for Political Affairs"},
    {"title": "Bureau of Counterterrorism", "url": "https://en.wikipedia.org/wiki/Bureau_of_Counterterrorism", "subdepartment": "Under Secretary for Political Affairs"},
    {"title": "Bureau of East Asian and Pacific Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_East_Asian_and_Pacific_Affairs", "subdepartment": "Under Secretary for Political Affairs"},
    {"title": "Bureau of European and Eurasian Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_European_and_Eurasian_Affairs", "subdepartment": "Under Secretary for Political Affairs"},
    {"title": "Bureau of International Organization Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_International_Organization_Affairs", "subdepartment": "Under Secretary for Political Affairs"},
    {"title": "Bureau of Near Eastern Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_Near_Eastern_Affairs", "subdepartment": "Under Secretary for Political Affairs"},
    {"title": "Bureau of South and Central Asian Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_South_and_Central_Asian_Affairs", "subdepartment": "Under Secretary for Political Affairs"},
    {"title": "Bureau of Western Hemisphere Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_Western_Hemisphere_Affairs", "subdepartment": "Under Secretary for Political Affairs"},
    {"title": "Bureau of Economic and Business Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_Economic_and_Business_Affairs", "subdepartment": "Under Secretary for Economic Growth, Energy, and the Environment"},
    {"title": "Bureau of Energy Resources", "url": "https://en.wikipedia.org/wiki/Bureau_of_Energy_Resources", "subdepartment": "Under Secretary for Economic Growth, Energy, and the Environment"},
    {"title": "Bureau of Oceans and International Environmental and Scientific Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_Oceans_and_International_Environmental_and_Scientific_Affairs", "subdepartment": "Under Secretary for Economic Growth, Energy, and the Environment"},
    {"title": "Bureau of Arms Control, Verification and Compliance", "url": "https://en.wikipedia.org/wiki/Bureau_of_Arms_Control,_Deterrence,_and_Stability", "subdepartment": "Under Secretary for Arms Control and International Security"},
    {"title": "Bureau of International Security and Nonproliferation", "url": "https://en.wikipedia.org/wiki/Bureau_of_International_Security_and_Nonproliferation", "subdepartment": "Under Secretary for Arms Control and International Security"},
    {"title": "Bureau of Political-Military Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_Political-Military_Affairs", "subdepartment": "Under Secretary for Arms Control and International Security"},
    {"title": "Bureau of International Narcotics and Law Enforcement Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_International_Narcotics_and_Law_Enforcement_Affairs", "subdepartment": "Under Secretary for Arms Control and International Security"},
    {"title": "Bureau of Educational and Cultural Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_Educational_and_Cultural_Affairs", "subdepartment": "Under Secretary for Public Diplomacy and Public Affairs"},
    {"title": "Bureau of Global Public Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_Global_Public_Affairs", "subdepartment": "Under Secretary for Public Diplomacy and Public Affairs"},
    {"title": "Global Engagement Center", "url": "https://en.wikipedia.org/wiki/Global_Engagement_Center", "subdepartment": "Under Secretary for Public Diplomacy and Public Affairs"},
    {"title": "Bureau of Administration", "url": "https://en.wikipedia.org/wiki/Bureau_of_Administration", "subdepartment": "Under Secretary for Management"},
    {"title": "Bureau of Budget and Planning", "url": "https://en.wikipedia.org/wiki/Bureau_of_Budget_and_Planning", "subdepartment": "Under Secretary for Management"},
    {"title": "Bureau of Consular Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_Consular_Affairs", "subdepartment": "Under Secretary for Management"},
    {"title": "Bureau of Diplomatic Security", "url": "https://en.wikipedia.org/wiki/Bureau_of_Diplomatic_Security", "subdepartment": "Under Secretary for Management"},
    {"title": "Bureau of Medical Services", "url": "https://en.wikipedia.org/wiki/Bureau_of_Medical_Services", "subdepartment": "Under Secretary for Management"},
    {"title": "Bureau of Overseas Buildings Operations", "url": "https://en.wikipedia.org/wiki/Bureau_of_Overseas_Buildings_Operations", "subdepartment": "Under Secretary for Management"},
    {"title": "Foreign Service Institute", "url": "https://en.wikipedia.org/wiki/Foreign_Service_Institute", "subdepartment": "Under Secretary for Management"},
    {"title": "Bureau of Conflict and Stabilization Operations", "url": "https://en.wikipedia.org/wiki/Bureau_of_Conflict_and_Stabilization_Operations", "subdepartment": "Under Secretary for Foreign Assistance, Humanitarian Affairs and Religious Freedom"},
    {"title": "Bureau of Democracy, Human Rights, and Labor", "url": "https://en.wikipedia.org/wiki/Bureau_of_Democracy,_Human_Rights,_and_Labor", "subdepartment": "Under Secretary for Foreign Assistance, Humanitarian Affairs and Religious Freedom"},
    {"title": "Bureau of Population, Refugees, and Migration", "url": "https://en.wikipedia.org/wiki/Bureau_of_Population,_Refugees,_and_Migration", "subdepartment": "Under Secretary for Foreign Assistance, Humanitarian Affairs and Religious Freedom"},
    {"title": "Bureau of Intelligence and Research", "url": "https://en.wikipedia.org/wiki/Bureau_of_Intelligence_and_Research", "subdepartment": "Offices Reporting Directly to the Secretary"},
    {"title": "Bureau of Legislative Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_Legislative_Affairs", "subdepartment": "Offices Reporting Directly to the Secretary"},
    {"title": "Executive Secretariat", "url": "https://en.wikipedia.org/wiki/Executive_Secretariat_(United_States_Department_of_State)", "subdepartment": "Offices Reporting Directly to the Secretary"},
    {"title": "Policy Planning Staff", "url": "https://en.wikipedia.org/wiki/Policy_Planning_Staff", "subdepartment": "Offices Reporting Directly to the Secretary"},
]


class StateDepartmentOrganizationWikipediaCollector(BaseCollector):
    collector_name = "state_department_organization_wikipedia"
    source_name = "Wikipedia State Department organization"
    source_url = "https://en.wikipedia.org/wiki/United_States_Department_of_State"
    parser_identity = "wikipedia_state_department_organization_v1"
    department_name = "Department of State"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> list[str]:
        return [item["url"] for item in STATE_DEPARTMENT_ORG_LINKS]

    def parse(self, payload: list[str]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in STATE_DEPARTMENT_ORG_LINKS:
            page_data = self._fetch_page(item["url"])
            if not page_data:
                continue
            full_name = page_data.get("full_name")
            office_title = page_data.get("role_title") or page_data.get("office_title") or item["title"]
            if not full_name or not office_title:
                continue
            office_name = f"{self.department_name}: {office_title}"
            key = (full_name, office_name)
            if key in seen:
                continue
            seen.add(key)
            person_url = page_data.get("person_url") or item["url"]
            parsed.append(
                {
                    "person": {
                        "full_name": full_name,
                        "source_url": person_url,
                        "source_type": "wikipedia",
                        "seed_source_type": "wikipedia",
                        "profile_status": "seeded",
                        "portrait_url": page_data.get("portrait_url"),
                        "portrait_source_url": person_url if page_data.get("portrait_url") else None,
                        "portrait_source_type": "wikipedia" if page_data.get("portrait_url") else None,
                        "social_profiles": page_data.get("social_profiles") or {},
                        "parser_identity": self.parser_identity,
                        "verification_status": "unverified",
                        "raw_payload": {
                            "wikipedia_url": person_url,
                            "source_page": item["url"],
                            "top_department_name": self.department_name,
                            "subdepartment_name": item["subdepartment"],
                            "department_name": self.department_name,
                            "unit_name": item["title"],
                            "office_title": office_title,
                        },
                    },
                    "jurisdiction": {"name": "United States", "type": "country", "code": "US"},
                    "office": {
                        "office_name": office_name,
                        "level": "federal",
                        "branch": "executive",
                        "chamber": None,
                        "source_url": item["url"],
                        "source_type": "wikipedia",
                    },
                    "appointment": {
                        "role_title": office_name,
                        "status": "current",
                        "source_url": item["url"],
                        "source_type": "wikipedia",
                        "parser_identity": self.parser_identity,
                        "is_current": True,
                        "raw_payload": {
                            "top_department_name": self.department_name,
                            "subdepartment_name": item["subdepartment"],
                            "department_name": self.department_name,
                            "unit_name": item["title"],
                            "office_title": office_title,
                            "wikipedia_person_url": person_url,
                        },
                    },
                    "aliases": [full_name],
                }
            )
        return parsed

    def sync(self) -> CollectorRunResult:
        result = CollectorRunResult(job_name=self.collector_name, source_name=self.source_name, started_at=datetime.utcnow())
        with session_scope() as session:
            sync_run = SyncRun(job_name=self.collector_name, job_type="collector", source_name=self.source_name)
            session.add(sync_run)
            session.flush()
            service: OfficialsService | None = None
            try:
                records = self.parse(self.fetch())
                service = OfficialsService(session)
                roster_service = RosterService(session)
                current_term = roster_service.presidential_term_roster(47)
                result.records_found = len(records)
                usa = service.get_or_create_jurisdiction("United States", "country", code="US")
                seen_keys: set[tuple[int, int, int | None, str]] = set()
                for record in records:
                    office = service.get_or_create_office(
                        record["office"]["office_name"],
                        record["office"]["level"],
                        record["office"].get("branch"),
                        record["office"].get("chamber"),
                        usa.id,
                        record["office"]["source_url"],
                        record["office"]["source_type"],
                    )
                    try:
                        person, created = service.upsert_person(record["person"])
                    except InvalidPersonNameError as exc:
                        result.errors.append(str(exc))
                        continue
                    result.records_created += 1 if created else 0
                    result.records_updated += 0 if created else 1
                    for alias in record.get("aliases", []):
                        service.ensure_alias(person.id, alias, record["person"]["source_url"], record["person"]["source_type"])
                    if roster_service.ensure_membership(
                        current_term,
                        person,
                        office,
                        usa.id,
                        record["appointment"]["role_title"],
                        record["appointment"].get("party"),
                        record["appointment"].get("status"),
                        record["appointment"].get("source_url"),
                        record["appointment"].get("source_type"),
                        record["appointment"].get("parser_identity"),
                        raw_payload=record["appointment"].get("raw_payload"),
                    ):
                        result.records_created += 1
                    if service.upsert_appointment(person, office, usa.id, record["appointment"]):
                        result.records_created += 1
                    seen_keys.add((person.id, office.id, usa.id, record["appointment"]["role_title"]))
                result.records_deactivated = service.reconcile_current_appointments(self.parser_identity, seen_keys)
                sync_run.status = "success"
            except Exception as exc:
                logger.exception("%s failed.", self.collector_name)
                result.errors.append(str(exc))
                sync_run.status = "failed"
                sync_run.error_message = str(exc)
            finally:
                validation_log = service.validation_log if service else []
                result.metadata["validation_log"] = validation_log
                result.metadata["validation_count"] = len(validation_log)
                result.ended_at = datetime.utcnow()
                sync_run.started_at = result.started_at
                sync_run.ended_at = result.ended_at
                sync_run.records_found = result.records_found
                sync_run.records_created = result.records_created
                sync_run.records_updated = result.records_updated
                sync_run.records_deactivated = result.records_deactivated
                sync_run.meta = {
                    "errors": result.errors,
                    "validation_log": validation_log,
                    "validation_count": len(validation_log),
                    "records_deactivated": result.records_deactivated,
                }
        return result

    def _fetch_page(self, url: str) -> dict[str, Any] | None:
        try:
            response = httpx.get(
                url,
                timeout=25.0,
                follow_redirects=True,
                trust_env=False,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            response.raise_for_status()
        except Exception:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        infobox = soup.select_one("table.infobox")
        if not infobox:
            return None

        person_anchor = None
        role_title = None
        for row in infobox.select("tr"):
            header = " ".join(row.find("th").get_text(" ", strip=True).split()).lower() if row.find("th") else ""
            value_cell = row.find("td")
            if not value_cell:
                continue
            if header in {"bureau executive", "agency executive", "executive", "incumbent"}:
                first_link = None
                for anchor in value_cell.find_all("a", href=True):
                    href = anchor.get("href", "")
                    if "/wiki/" in href and ":" not in href.split("/wiki/")[-1]:
                        first_link = anchor
                        break
                if first_link:
                    person_anchor = first_link
                    siblings_text = " ".join(value_cell.get_text(" ", strip=True).split())
                    role_title = siblings_text.replace(first_link.get_text(" ", strip=True), "", 1).strip(" ,;â€“-")
                    break

        if not person_anchor:
            return None

        full_name = " ".join(person_anchor.get_text(" ", strip=True).split())
        person_url = urljoin(url, person_anchor["href"].strip())
        portrait_url = None
        image = infobox.select_one("img")
        if image and image.get("src"):
            portrait_url = urljoin(url, image["src"].strip())

        social_profiles = {}
        try:
            person_response = httpx.get(
                person_url,
                timeout=20.0,
                follow_redirects=True,
                trust_env=False,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            person_response.raise_for_status()
            person_soup = BeautifulSoup(person_response.text, "html.parser")
            social_profiles = discover_social_profiles(person_url, person_soup)
        except Exception:
            social_profiles = {}

        return {
            "full_name": full_name,
            "person_url": person_url,
            "portrait_url": portrait_url,
            "role_title": role_title,
            "social_profiles": social_profiles,
        }

