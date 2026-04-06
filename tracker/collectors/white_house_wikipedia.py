from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urljoin

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


class WhiteHouseWikipediaCollector(BaseCollector):
    collector_name = "white_house_wikipedia"
    source_name = "Wikipedia White House and National Security Council"
    source_url = "https://en.wikipedia.org/wiki/White_House_Office"
    parser_identity = "wikipedia_white_house_v1"
    department_name = "White House"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> dict[str, str]:
        return {
            "white_house_office": "https://en.wikipedia.org/wiki/White_House_Office",
            "national_security_council": "https://en.wikipedia.org/wiki/United_States_National_Security_Council",
        }

    def parse(self, payload: dict[str, str]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        parsed.extend(self._parse_white_house_office(payload["white_house_office"], seen))
        parsed.extend(self._parse_national_security_council(payload["national_security_council"], seen))
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

    def _parse_white_house_office(self, url: str, seen: set[tuple[str, str]]) -> list[dict[str, Any]]:
        soup = self._fetch_soup(url)
        if not soup:
            return []
        parsed: list[dict[str, Any]] = []
        content = soup.select_one("#mw-content-text .mw-parser-output") or soup
        for heading in content.select("h3, h4"):
            heading_text = " ".join(heading.get_text(" ", strip=True).split()).replace("[edit]", "").strip()
            if not heading_text:
                continue
            next_element = heading.find_next_sibling()
            while next_element and next_element.name not in {"h2", "h3", "h4"}:
                if getattr(next_element, "name", None) == "ul":
                    for li in next_element.select("li"):
                        record = self._parse_white_house_list_item(li, heading_text, url)
                        if not record:
                            continue
                        key = (record["person"]["full_name"], record["office"]["office_name"])
                        if key in seen:
                            continue
                        seen.add(key)
                        parsed.append(record)
                next_element = next_element.find_next_sibling()
        return parsed

    def _parse_white_house_list_item(self, li: Tag, section_name: str, source_url: str) -> dict[str, Any] | None:
        text = " ".join(li.get_text(" ", strip=True).split())
        if not text or ":" not in text or "Vacant" in text:
            return None
        role_title, _, _ = text.partition(":")
        if not role_title:
            return None
        person_anchor = None
        for anchor in li.find_all("a", href=True):
            href = anchor.get("href", "")
            if "/wiki/" in href and ":" not in href.split("/wiki/")[-1]:
                person_anchor = anchor
                break
        if not person_anchor:
            return None
        full_name = " ".join(person_anchor.get_text(" ", strip=True).split())
        if not full_name:
            return None
        person_url = urljoin(source_url, person_anchor["href"].strip())
        page_data = self._fetch_person_page(person_url)
        page_data["full_name"] = full_name
        page_data["person_url"] = person_url
        subdepartment = "National Security Council" if "national security" in role_title.lower() or "national security" in section_name.lower() else section_name
        office_name = f"{self.department_name}: {role_title}"
        return {
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
                    "source_page": source_url,
                    "top_department_name": self.department_name,
                    "subdepartment_name": subdepartment,
                    "department_name": self.department_name,
                    "unit_name": section_name,
                    "office_title": role_title,
                },
            },
            "jurisdiction": {"name": "United States", "type": "country", "code": "US"},
            "office": {
                "office_name": office_name,
                "level": "federal",
                "branch": "executive",
                "chamber": None,
                "source_url": source_url,
                "source_type": "wikipedia",
            },
            "appointment": {
                "role_title": office_name,
                "status": "current",
                "source_url": source_url,
                "source_type": "wikipedia",
                "parser_identity": self.parser_identity,
                "is_current": True,
                "raw_payload": {
                    "top_department_name": self.department_name,
                    "subdepartment_name": subdepartment,
                    "department_name": self.department_name,
                    "unit_name": section_name,
                    "office_title": role_title,
                    "wikipedia_person_url": person_url,
                },
            },
            "aliases": [full_name],
        }

    def _parse_national_security_council(self, url: str, seen: set[tuple[str, str]]) -> list[dict[str, Any]]:
        soup = self._fetch_soup(url)
        if not soup:
            return []
        infobox = soup.select_one("table.infobox")
        if not infobox:
            return []
        rows: list[dict[str, Any]] = []
        for index in range(1, 6):
            name_cell = infobox.select_one(f'tr:-soup-contains("Chief{index} Name") td')
            position_cell = infobox.select_one(f'tr:-soup-contains("Chief{index} Position") td')
            if not name_cell or not position_cell:
                continue
            anchor = None
            for candidate in name_cell.find_all("a", href=True):
                href = candidate.get("href", "")
                if "/wiki/" in href and ":" not in href.split("/wiki/")[-1]:
                    anchor = candidate
                    break
            if not anchor:
                continue
            full_name = " ".join(anchor.get_text(" ", strip=True).split())
            role_title = " ".join(position_cell.get_text(" ", strip=True).split())
            if not full_name or not role_title:
                continue
            person_url = urljoin(url, anchor["href"].strip())
            page_data = self._fetch_person_page(person_url)
            office_name = f"{self.department_name}: {role_title}"
            key = (full_name, office_name)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
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
                            "source_page": url,
                            "top_department_name": self.department_name,
                            "subdepartment_name": "National Security Council",
                            "department_name": self.department_name,
                            "unit_name": "National Security Council",
                            "office_title": role_title,
                        },
                    },
                    "jurisdiction": {"name": "United States", "type": "country", "code": "US"},
                    "office": {
                        "office_name": office_name,
                        "level": "federal",
                        "branch": "executive",
                        "chamber": None,
                        "source_url": url,
                        "source_type": "wikipedia",
                    },
                    "appointment": {
                        "role_title": office_name,
                        "status": "current",
                        "source_url": url,
                        "source_type": "wikipedia",
                        "parser_identity": self.parser_identity,
                        "is_current": True,
                        "raw_payload": {
                            "top_department_name": self.department_name,
                            "subdepartment_name": "National Security Council",
                            "department_name": self.department_name,
                            "unit_name": "National Security Council",
                            "office_title": role_title,
                            "wikipedia_person_url": person_url,
                        },
                    },
                    "aliases": [full_name],
                }
            )
        return rows

    def _fetch_soup(self, url: str) -> BeautifulSoup | None:
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
        return BeautifulSoup(response.text, "lxml")

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
        soup = BeautifulSoup(response.text, "lxml")
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
