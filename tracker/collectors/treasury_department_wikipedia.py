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


GENERIC_NON_PERSON_PREFIXES = (
    "director of ",
    "administrator of ",
    "commissioner of ",
    "assistant secretary",
    "under secretary",
    "office of ",
    "bureau of ",
    "department of ",
    "united states ",
)


TREASURY_ORG_LINKS = [
    {"title": "Treasurer of the United States", "url": "https://en.wikipedia.org/wiki/Treasurer_of_the_United_States", "subdepartment": "Office of the Secretary"},
    {"title": "Under Secretary for Domestic Finance", "url": "https://en.wikipedia.org/wiki/Under_Secretary_of_the_Treasury_for_Domestic_Finance", "subdepartment": "Domestic Finance"},
    {"title": "Assistant Secretary for Financial Institutions", "url": "https://en.wikipedia.org/wiki/Assistant_Secretary_of_the_Treasury_for_Financial_Institutions", "subdepartment": "Domestic Finance"},
    {"title": "Assistant Secretary for Financial Markets", "url": "https://en.wikipedia.org/wiki/Assistant_Secretary_of_the_Treasury_for_Financial_Markets", "subdepartment": "Domestic Finance"},
    {"title": "Fiscal Assistant Secretary", "url": "https://en.wikipedia.org/wiki/Fiscal_Assistant_Secretary_of_the_Treasury", "subdepartment": "Domestic Finance"},
    {"title": "Office of Financial Research", "url": "https://en.wikipedia.org/wiki/Office_of_Financial_Research", "subdepartment": "Domestic Finance"},
    {"title": "Bureau of the Fiscal Service", "url": "https://en.wikipedia.org/wiki/Bureau_of_the_Fiscal_Service", "subdepartment": "Operating Bureaus"},
    {"title": "Internal Revenue Service", "url": "https://en.wikipedia.org/wiki/Internal_Revenue_Service", "subdepartment": "Operating Bureaus"},
    {"title": "Commissioner of Internal Revenue", "url": "https://en.wikipedia.org/wiki/Commissioner_of_Internal_Revenue", "subdepartment": "Operating Bureaus"},
    {"title": "Bureau of Engraving and Printing", "url": "https://en.wikipedia.org/wiki/Bureau_of_Engraving_and_Printing", "subdepartment": "Operating Bureaus"},
    {"title": "United States Mint", "url": "https://en.wikipedia.org/wiki/United_States_Mint", "subdepartment": "Operating Bureaus"},
    {"title": "Office of the Comptroller of the Currency", "url": "https://en.wikipedia.org/wiki/Office_of_the_Comptroller_of_the_Currency", "subdepartment": "Operating Bureaus"},
    {"title": "Financial Crimes Enforcement Network", "url": "https://en.wikipedia.org/wiki/Financial_Crimes_Enforcement_Network", "subdepartment": "Operating Bureaus"},
    {"title": "Alcohol and Tobacco Tax and Trade Bureau", "url": "https://en.wikipedia.org/wiki/Alcohol_and_Tobacco_Tax_and_Trade_Bureau", "subdepartment": "Operating Bureaus"},
    {"title": "Office of Foreign Assets Control", "url": "https://en.wikipedia.org/wiki/Office_of_Foreign_Assets_Control", "subdepartment": "International Affairs"},
    {"title": "Office of Terrorism and Financial Intelligence", "url": "https://en.wikipedia.org/wiki/Office_of_Terrorism_and_Financial_Intelligence", "subdepartment": "International Affairs"},
]


class TreasuryDepartmentWikipediaCollector(BaseCollector):
    collector_name = "treasury_department_wikipedia"
    source_name = "Wikipedia Treasury Department organization"
    source_url = "https://en.wikipedia.org/wiki/United_States_Department_of_the_Treasury"
    parser_identity = "wikipedia_treasury_department_v1"
    department_name = "Department of the Treasury"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> list[dict[str, str]]:
        return TREASURY_ORG_LINKS

    def parse(self, payload: list[dict[str, str]]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in payload:
            page_data = self._fetch_page(item["url"])
            if not page_data or not page_data.get("full_name"):
                continue
            office_title = page_data.get("role_title") or item["title"]
            office_name = f"{self.department_name}: {office_title}"
            key = (str(page_data["full_name"]), office_name)
            if key in seen:
                continue
            seen.add(key)
            person_url = page_data.get("person_url") or item["url"]
            parsed.append(
                {
                    "person": {
                        "full_name": page_data["full_name"],
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
                    "aliases": [page_data["full_name"]],
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
        person_url = None
        for row in infobox.select("tr"):
            header_cell = row.find("th")
            value_cell = row.find("td")
            if not header_cell or not value_cell:
                continue
            header = " ".join(header_cell.get_text(" ", strip=True).split()).lower()
            if header not in {
                "chief1 name",
                "chief2 name",
                "chief3 name",
                "agency executive",
                "bureau executive",
                "executive",
                "incumbent",
                "commissioner",
                "administrator",
                "director",
            }:
                continue
            person_anchor = self._pick_person_anchor(value_cell)
            if person_anchor:
                person_url = urljoin(url, person_anchor.get("href", "").strip())
            if person_anchor:
                text = " ".join(value_cell.get_text(" ", strip=True).split())
                role_title = text.replace(person_anchor.get_text(" ", strip=True), "", 1).strip(" ,;â€“-")
                break

        if not person_anchor and infobox:
            for row in infobox.select("tr"):
                header_cell = row.find("th")
                value_cell = row.find("td")
                if not header_cell or not value_cell:
                    continue
                header = " ".join(header_cell.get_text(" ", strip=True).split()).lower()
                if header in {"chief1 name", "chief2 name", "chief3 name"}:
                    person_anchor = self._pick_person_anchor(value_cell)
                    if person_anchor:
                        person_url = urljoin(url, person_anchor.get("href", "").strip())
                if person_anchor:
                    break

        if not person_anchor:
            return None

        full_name = " ".join(person_anchor.get_text(" ", strip=True).split())
        if not self._looks_like_person_name(full_name):
            return None
        portrait_url = None
        image = infobox.select_one("img")
        if image and image.get("src"):
            portrait_url = urljoin(url, image["src"].strip())

        social_profiles = {}
        if person_url:
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

    def _pick_person_anchor(self, value_cell: Tag) -> Tag | None:
        candidates: list[Tag] = []
        for anchor in value_cell.find_all("a", href=True):
            href = anchor.get("href", "")
            if "/wiki/" not in href or ":" in href.split("/wiki/")[-1]:
                continue
            candidates.append(anchor)
        for anchor in candidates:
            text = " ".join(anchor.get_text(" ", strip=True).split())
            if self._looks_like_person_name(text):
                return anchor
        return None

    def _looks_like_person_name(self, text: str) -> bool:
        cleaned = " ".join(text.split()).strip()
        if len(cleaned.split()) < 2:
            return False
        if cleaned.lower().startswith(GENERIC_NON_PERSON_PREFIXES):
            return False
        return True

