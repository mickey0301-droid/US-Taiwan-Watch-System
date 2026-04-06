from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from tracker.collectors.base import BaseCollector, CollectorRunResult
from tracker.config import get_settings
from tracker.db import session_scope
from tracker.logging_utils import get_logger
from tracker.models import SyncRun
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService
from tracker.services.roster_service import RosterService
from tracker.utils.social import discover_social_profiles


logger = get_logger(__name__)


CABINET_LEVEL_WIKIPEDIA_PAGES = [
    {
        "department_name": "Environmental Protection Agency",
        "subdepartment_name": "Administrator",
        "unit_name": "Environmental Protection Agency",
        "url": "https://en.wikipedia.org/wiki/Environmental_Protection_Agency",
        "default_role_title": "Administrator of the Environmental Protection Agency",
    },
    {
        "department_name": "Small Business Administration",
        "subdepartment_name": "Administrator",
        "unit_name": "Small Business Administration",
        "url": "https://en.wikipedia.org/wiki/Small_Business_Administration",
        "default_role_title": "Administrator of the Small Business Administration",
    },
    {
        "department_name": "Office of Management and Budget",
        "subdepartment_name": "Director",
        "unit_name": "Office of Management and Budget",
        "url": "https://en.wikipedia.org/wiki/Office_of_Management_and_Budget",
        "default_role_title": "Director of the Office of Management and Budget",
    },
    {
        "department_name": "Office of the Director of National Intelligence",
        "subdepartment_name": "Director",
        "unit_name": "Office of the Director of National Intelligence",
        "url": "https://en.wikipedia.org/wiki/Office_of_the_Director_of_National_Intelligence",
        "default_role_title": "Director of National Intelligence",
    },
    {
        "department_name": "Office of the United States Trade Representative",
        "subdepartment_name": "Trade Representative",
        "unit_name": "Office of the United States Trade Representative",
        "url": "https://en.wikipedia.org/wiki/Office_of_the_United_States_Trade_Representative",
        "default_role_title": "United States Trade Representative",
    },
    {
        "department_name": "United States Mission to the United Nations",
        "subdepartment_name": "Ambassador",
        "unit_name": "United States Mission to the United Nations",
        "url": "https://en.wikipedia.org/wiki/United_States_Ambassador_to_the_United_Nations",
        "default_role_title": "United States Ambassador to the United Nations",
    },
    {
        "department_name": "Council of Economic Advisers",
        "subdepartment_name": "Chair",
        "unit_name": "Council of Economic Advisers",
        "url": "https://en.wikipedia.org/wiki/Council_of_Economic_Advisers",
        "default_role_title": "Chair of the Council of Economic Advisers",
    },
    {
        "department_name": "Central Intelligence Agency",
        "subdepartment_name": "Director",
        "unit_name": "Central Intelligence Agency",
        "url": "https://en.wikipedia.org/wiki/Central_Intelligence_Agency",
        "default_role_title": "Director of the Central Intelligence Agency",
    },
    {
        "department_name": "Office of Science and Technology Policy",
        "subdepartment_name": "Director",
        "unit_name": "Office of Science and Technology Policy",
        "url": "https://en.wikipedia.org/wiki/Office_of_Science_and_Technology_Policy",
        "default_role_title": "Director of the Office of Science and Technology Policy",
    },
    {
        "department_name": "General Services Administration",
        "subdepartment_name": "Administrator",
        "unit_name": "General Services Administration",
        "url": "https://en.wikipedia.org/wiki/General_Services_Administration",
        "default_role_title": "Administrator of General Services",
    },
    {
        "department_name": "Office of Personnel Management",
        "subdepartment_name": "Director",
        "unit_name": "Office of Personnel Management",
        "url": "https://en.wikipedia.org/wiki/Office_of_Personnel_Management",
        "default_role_title": "Director of the Office of Personnel Management",
    },
    {
        "department_name": "Social Security Administration",
        "subdepartment_name": "Commissioner",
        "unit_name": "Social Security Administration",
        "url": "https://en.wikipedia.org/wiki/Social_Security_Administration",
        "default_role_title": "Commissioner of Social Security",
    },
    {
        "department_name": "National Aeronautics and Space Administration",
        "subdepartment_name": "Administrator",
        "unit_name": "National Aeronautics and Space Administration",
        "url": "https://en.wikipedia.org/wiki/NASA",
        "default_role_title": "Administrator of the National Aeronautics and Space Administration",
    },
    {
        "department_name": "Exportâ€“Import Bank of the United States",
        "subdepartment_name": "President",
        "unit_name": "Exportâ€“Import Bank of the United States",
        "url": "https://en.wikipedia.org/wiki/Export%E2%80%93Import_Bank_of_the_United_States",
        "default_role_title": "President of the Exportâ€“Import Bank of the United States",
    },
    {
        "department_name": "Federal Communications Commission",
        "subdepartment_name": "Chair",
        "unit_name": "Federal Communications Commission",
        "url": "https://en.wikipedia.org/wiki/Federal_Communications_Commission",
        "default_role_title": "Chairman of the Federal Communications Commission",
    },
    {
        "department_name": "Securities and Exchange Commission",
        "subdepartment_name": "Chair",
        "unit_name": "Securities and Exchange Commission",
        "url": "https://en.wikipedia.org/wiki/U.S._Securities_and_Exchange_Commission",
        "default_role_title": "Chair of the Securities and Exchange Commission",
    },
    {
        "department_name": "Federal Trade Commission",
        "subdepartment_name": "Chair",
        "unit_name": "Federal Trade Commission",
        "url": "https://en.wikipedia.org/wiki/Federal_Trade_Commission",
        "default_role_title": "Chair of the Federal Trade Commission",
    },
]


class CabinetLevelWikipediaCollector(BaseCollector):
    collector_name = "cabinet_level_wikipedia"
    source_name = "Wikipedia cabinet-level agencies"
    source_url = "https://en.wikipedia.org/wiki/Cabinet_of_the_United_States"
    parser_identity = "wikipedia_cabinet_level_v1"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> list[dict[str, str]]:
        return CABINET_LEVEL_WIKIPEDIA_PAGES

    def parse(self, payload: list[dict[str, str]]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in payload:
            page_data = self._fetch_page(item["url"])
            if not page_data or not page_data.get("full_name"):
                continue
            office_title = page_data.get("role_title") or item["default_role_title"]
            office_name = f"{item['department_name']}: {office_title}"
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
                            "top_department_name": item["department_name"],
                            "subdepartment_name": item["subdepartment_name"],
                            "department_name": item["department_name"],
                            "unit_name": item["unit_name"],
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
                            "top_department_name": item["department_name"],
                            "subdepartment_name": item["subdepartment_name"],
                            "department_name": item["department_name"],
                            "unit_name": item["unit_name"],
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
        for row in infobox.select("tr"):
            header_cell = row.find("th")
            value_cell = row.find("td")
            if not header_cell or not value_cell:
                continue
            header = " ".join(header_cell.get_text(" ", strip=True).split()).lower()
            if header not in {"administrator", "director", "chair", "chairman", "incumbent", "agency executive", "ambassador"}:
                continue
            for anchor in value_cell.find_all("a", href=True):
                href = anchor.get("href", "")
                if "/wiki/" in href and ":" not in href.split("/wiki/")[-1]:
                    person_anchor = anchor
                    break
            if person_anchor:
                role_title = " ".join(value_cell.get_text(" ", strip=True).split()).replace(person_anchor.get_text(" ", strip=True), "", 1).strip(" ,;â€“-")
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

