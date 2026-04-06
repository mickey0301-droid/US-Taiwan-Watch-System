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
    "federal ",
    "national ",
)


FEDERAL_DEPARTMENT_UNIT_PAGES = [
    {"department_name": "Department of Agriculture", "subdepartment_name": "Agencies", "unit_name": "Farm Service Agency", "url": "https://en.wikipedia.org/wiki/Farm_Service_Agency", "default_role_title": "Administrator of the Farm Service Agency"},
    {"department_name": "Department of Agriculture", "subdepartment_name": "Agencies", "unit_name": "Foreign Agricultural Service", "url": "https://en.wikipedia.org/wiki/Foreign_Agricultural_Service", "default_role_title": "Administrator of the Foreign Agricultural Service"},
    {"department_name": "Department of Agriculture", "subdepartment_name": "Agencies", "unit_name": "Food Safety and Inspection Service", "url": "https://en.wikipedia.org/wiki/Food_Safety_and_Inspection_Service", "default_role_title": "Administrator of the Food Safety and Inspection Service"},
    {"department_name": "Department of Commerce", "subdepartment_name": "Bureaus", "unit_name": "National Oceanic and Atmospheric Administration", "url": "https://en.wikipedia.org/wiki/National_Oceanic_and_Atmospheric_Administration", "default_role_title": "Administrator of the National Oceanic and Atmospheric Administration"},
    {"department_name": "Department of Commerce", "subdepartment_name": "Bureaus", "unit_name": "International Trade Administration", "url": "https://en.wikipedia.org/wiki/International_Trade_Administration", "default_role_title": "Under Secretary of Commerce for International Trade"},
    {"department_name": "Department of Commerce", "subdepartment_name": "Bureaus", "unit_name": "Bureau of Industry and Security", "url": "https://en.wikipedia.org/wiki/Bureau_of_Industry_and_Security", "default_role_title": "Under Secretary of Commerce for Industry and Security"},
    {"department_name": "Department of Defense", "subdepartment_name": "Office of the Secretary of Defense", "unit_name": "Under Secretary of Defense", "url": "https://en.wikipedia.org/wiki/United_States_Under_Secretary_of_Defense", "default_role_title": "Under Secretary of Defense"},
    {"department_name": "Department of Defense", "subdepartment_name": "Office of the Secretary of Defense", "unit_name": "Assistant Secretary of Defense", "url": "https://en.wikipedia.org/wiki/United_States_Assistant_Secretary_of_Defense", "default_role_title": "Assistant Secretary of Defense"},
    {"department_name": "Department of Defense", "subdepartment_name": "Defense Agencies", "unit_name": "Defense Intelligence Agency", "url": "https://en.wikipedia.org/wiki/Defense_Intelligence_Agency", "default_role_title": "Director of the Defense Intelligence Agency"},
    {"department_name": "Department of Defense", "subdepartment_name": "Defense Agencies", "unit_name": "Missile Defense Agency", "url": "https://en.wikipedia.org/wiki/Missile_Defense_Agency", "default_role_title": "Director of the Missile Defense Agency"},
    {"department_name": "Department of Defense", "subdepartment_name": "Defense Agencies", "unit_name": "National Security Agency", "url": "https://en.wikipedia.org/wiki/National_Security_Agency", "default_role_title": "Director of the National Security Agency"},
    {"department_name": "Department of Defense", "subdepartment_name": "Defense Agencies", "unit_name": "Defense Logistics Agency", "url": "https://en.wikipedia.org/wiki/Defense_Logistics_Agency", "default_role_title": "Director of the Defense Logistics Agency"},
    {"department_name": "Department of Education", "subdepartment_name": "Offices", "unit_name": "Office of Elementary and Secondary Education", "url": "https://en.wikipedia.org/wiki/Office_of_Elementary_and_Secondary_Education", "default_role_title": "Assistant Secretary for Elementary and Secondary Education"},
    {"department_name": "Department of Education", "subdepartment_name": "Offices", "unit_name": "Office of Postsecondary Education", "url": "https://en.wikipedia.org/wiki/Office_of_Postsecondary_Education", "default_role_title": "Assistant Secretary for Postsecondary Education"},
    {"department_name": "Department of Education", "subdepartment_name": "Offices", "unit_name": "Office of Special Education and Rehabilitative Services", "url": "https://en.wikipedia.org/wiki/Office_of_Special_Education_and_Rehabilitative_Services", "default_role_title": "Assistant Secretary for Special Education and Rehabilitative Services"},
    {"department_name": "Department of Energy", "subdepartment_name": "Administrations", "unit_name": "National Nuclear Security Administration", "url": "https://en.wikipedia.org/wiki/National_Nuclear_Security_Administration", "default_role_title": "Administrator of the National Nuclear Security Administration"},
    {"department_name": "Department of Energy", "subdepartment_name": "Offices", "unit_name": "Office of Science", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Energy_Office_of_Science", "default_role_title": "Director of the Office of Science"},
    {"department_name": "Department of Energy", "subdepartment_name": "Offices", "unit_name": "Office of Energy Efficiency and Renewable Energy", "url": "https://en.wikipedia.org/wiki/Office_of_Energy_Efficiency_and_Renewable_Energy", "default_role_title": "Assistant Secretary for Energy Efficiency and Renewable Energy"},
    {"department_name": "Department of Health and Human Services", "subdepartment_name": "Operating Divisions", "unit_name": "Centers for Medicare & Medicaid Services", "url": "https://en.wikipedia.org/wiki/Centers_for_Medicare_%26_Medicaid_Services", "default_role_title": "Administrator of the Centers for Medicare & Medicaid Services"},
    {"department_name": "Department of Health and Human Services", "subdepartment_name": "Operating Divisions", "unit_name": "Food and Drug Administration", "url": "https://en.wikipedia.org/wiki/Food_and_Drug_Administration", "default_role_title": "Commissioner of Food and Drugs"},
    {"department_name": "Department of Health and Human Services", "subdepartment_name": "Operating Divisions", "unit_name": "Centers for Disease Control and Prevention", "url": "https://en.wikipedia.org/wiki/Centers_for_Disease_Control_and_Prevention", "default_role_title": "Director of the Centers for Disease Control and Prevention"},
    {"department_name": "Department of Health and Human Services", "subdepartment_name": "Operating Divisions", "unit_name": "National Institutes of Health", "url": "https://en.wikipedia.org/wiki/National_Institutes_of_Health", "default_role_title": "Director of the National Institutes of Health"},
    {"department_name": "Department of Health and Human Services", "subdepartment_name": "Operating Divisions", "unit_name": "Administration for Children and Families", "url": "https://en.wikipedia.org/wiki/Administration_for_Children_and_Families", "default_role_title": "Assistant Secretary for Children and Families"},
    {"department_name": "Department of Health and Human Services", "subdepartment_name": "Operating Divisions", "unit_name": "Administration for Community Living", "url": "https://en.wikipedia.org/wiki/Administration_for_Community_Living", "default_role_title": "Administrator of the Administration for Community Living"},
    {"department_name": "Department of Homeland Security", "subdepartment_name": "Components", "unit_name": "Federal Emergency Management Agency", "url": "https://en.wikipedia.org/wiki/Federal_Emergency_Management_Agency", "default_role_title": "Administrator of the Federal Emergency Management Agency"},
    {"department_name": "Department of Homeland Security", "subdepartment_name": "Components", "unit_name": "United States Citizenship and Immigration Services", "url": "https://en.wikipedia.org/wiki/U.S._Citizenship_and_Immigration_Services", "default_role_title": "Director of United States Citizenship and Immigration Services"},
    {"department_name": "Department of Homeland Security", "subdepartment_name": "Components", "unit_name": "Transportation Security Administration", "url": "https://en.wikipedia.org/wiki/Transportation_Security_Administration", "default_role_title": "Administrator of the Transportation Security Administration"},
    {"department_name": "Department of Homeland Security", "subdepartment_name": "Components", "unit_name": "United States Customs and Border Protection", "url": "https://en.wikipedia.org/wiki/U.S._Customs_and_Border_Protection", "default_role_title": "Commissioner of U.S. Customs and Border Protection"},
    {"department_name": "Department of Homeland Security", "subdepartment_name": "Components", "unit_name": "United States Secret Service", "url": "https://en.wikipedia.org/wiki/United_States_Secret_Service", "default_role_title": "Director of the United States Secret Service"},
    {"department_name": "Department of Homeland Security", "subdepartment_name": "Components", "unit_name": "Cybersecurity and Infrastructure Security Agency", "url": "https://en.wikipedia.org/wiki/Cybersecurity_and_Infrastructure_Security_Agency", "default_role_title": "Director of the Cybersecurity and Infrastructure Security Agency"},
    {"department_name": "Department of Housing and Urban Development", "subdepartment_name": "Offices", "unit_name": "Federal Housing Administration", "url": "https://en.wikipedia.org/wiki/Federal_Housing_Administration", "default_role_title": "Commissioner of the Federal Housing Administration"},
    {"department_name": "Department of Housing and Urban Development", "subdepartment_name": "Offices", "unit_name": "Government National Mortgage Association", "url": "https://en.wikipedia.org/wiki/Government_National_Mortgage_Association", "default_role_title": "President of the Government National Mortgage Association"},
    {"department_name": "Department of Housing and Urban Development", "subdepartment_name": "Offices", "unit_name": "Office of Public and Indian Housing", "url": "https://en.wikipedia.org/wiki/Office_of_Public_and_Indian_Housing", "default_role_title": "Assistant Secretary for Public and Indian Housing"},
    {"department_name": "Department of the Interior", "subdepartment_name": "Bureaus", "unit_name": "Bureau of Land Management", "url": "https://en.wikipedia.org/wiki/Bureau_of_Land_Management", "default_role_title": "Director of the Bureau of Land Management"},
    {"department_name": "Department of the Interior", "subdepartment_name": "Bureaus", "unit_name": "National Park Service", "url": "https://en.wikipedia.org/wiki/National_Park_Service", "default_role_title": "Director of the National Park Service"},
    {"department_name": "Department of the Interior", "subdepartment_name": "Bureaus", "unit_name": "United States Geological Survey", "url": "https://en.wikipedia.org/wiki/United_States_Geological_Survey", "default_role_title": "Director of the United States Geological Survey"},
    {"department_name": "Department of the Interior", "subdepartment_name": "Bureaus", "unit_name": "Bureau of Indian Affairs", "url": "https://en.wikipedia.org/wiki/Bureau_of_Indian_Affairs", "default_role_title": "Assistant Secretary for Indian Affairs"},
    {"department_name": "Department of the Interior", "subdepartment_name": "Bureaus", "unit_name": "Bureau of Reclamation", "url": "https://en.wikipedia.org/wiki/Bureau_of_Reclamation", "default_role_title": "Commissioner of the Bureau of Reclamation"},
    {"department_name": "Department of Labor", "subdepartment_name": "Agencies", "unit_name": "Occupational Safety and Health Administration", "url": "https://en.wikipedia.org/wiki/Occupational_Safety_and_Health_Administration", "default_role_title": "Assistant Secretary of Labor for Occupational Safety and Health"},
    {"department_name": "Department of Labor", "subdepartment_name": "Agencies", "unit_name": "Mine Safety and Health Administration", "url": "https://en.wikipedia.org/wiki/Mine_Safety_and_Health_Administration", "default_role_title": "Assistant Secretary of Labor for Mine Safety and Health"},
    {"department_name": "Department of Labor", "subdepartment_name": "Agencies", "unit_name": "Bureau of Labor Statistics", "url": "https://en.wikipedia.org/wiki/Bureau_of_Labor_Statistics", "default_role_title": "Commissioner of Labor Statistics"},
    {"department_name": "Department of Transportation", "subdepartment_name": "Administrations", "unit_name": "Federal Aviation Administration", "url": "https://en.wikipedia.org/wiki/Federal_Aviation_Administration", "default_role_title": "Administrator of the Federal Aviation Administration"},
    {"department_name": "Department of Transportation", "subdepartment_name": "Administrations", "unit_name": "Federal Highway Administration", "url": "https://en.wikipedia.org/wiki/Federal_Highway_Administration", "default_role_title": "Administrator of the Federal Highway Administration"},
    {"department_name": "Department of Transportation", "subdepartment_name": "Administrations", "unit_name": "Federal Railroad Administration", "url": "https://en.wikipedia.org/wiki/Federal_Railroad_Administration", "default_role_title": "Administrator of the Federal Railroad Administration"},
    {"department_name": "Department of Transportation", "subdepartment_name": "Administrations", "unit_name": "Federal Transit Administration", "url": "https://en.wikipedia.org/wiki/Federal_Transit_Administration", "default_role_title": "Administrator of the Federal Transit Administration"},
    {"department_name": "Department of Transportation", "subdepartment_name": "Administrations", "unit_name": "National Highway Traffic Safety Administration", "url": "https://en.wikipedia.org/wiki/National_Highway_Traffic_Safety_Administration", "default_role_title": "Administrator of the National Highway Traffic Safety Administration"},
    {"department_name": "Department of Transportation", "subdepartment_name": "Administrations", "unit_name": "Maritime Administration", "url": "https://en.wikipedia.org/wiki/Maritime_Administration", "default_role_title": "Administrator of the Maritime Administration"},
    {"department_name": "Department of Veterans Affairs", "subdepartment_name": "Administrations", "unit_name": "Veterans Health Administration", "url": "https://en.wikipedia.org/wiki/Veterans_Health_Administration", "default_role_title": "Under Secretary of Veterans Affairs for Health"},
    {"department_name": "Department of Veterans Affairs", "subdepartment_name": "Administrations", "unit_name": "Veterans Benefits Administration", "url": "https://en.wikipedia.org/wiki/Veterans_Benefits_Administration", "default_role_title": "Under Secretary of Veterans Affairs for Benefits"},
    {"department_name": "Department of Veterans Affairs", "subdepartment_name": "Administrations", "unit_name": "National Cemetery Administration", "url": "https://en.wikipedia.org/wiki/National_Cemetery_Administration", "default_role_title": "Under Secretary of Veterans Affairs for Memorial Affairs"},
]


class FederalDepartmentUnitsWikipediaCollector(BaseCollector):
    collector_name = "federal_department_units_wikipedia"
    source_name = "Wikipedia federal department units"
    source_url = "https://en.wikipedia.org/wiki/Cabinet_of_the_United_States"
    parser_identity = "wikipedia_federal_department_units_v1"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> list[dict[str, str]]:
        return FEDERAL_DEPARTMENT_UNIT_PAGES

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
            if header not in {
                "administrator",
                "director",
                "commissioner",
                "agency executive",
                "bureau executive",
                "incumbent",
                "chief1 name",
                "chief2 name",
                "chief3 name",
                "leader",
            }:
                continue
            person_anchor = self._pick_person_anchor(value_cell)
            if person_anchor:
                role_title = " ".join(value_cell.get_text(" ", strip=True).split()).replace(person_anchor.get_text(" ", strip=True), "", 1).strip(" ,;â€“-")
                break

        if not person_anchor:
            return None

        full_name = " ".join(person_anchor.get_text(" ", strip=True).split())
        if not self._looks_like_person_name(full_name):
            return None
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
        lower = cleaned.lower()
        if lower.startswith(GENERIC_NON_PERSON_PREFIXES):
            return False
        return True

