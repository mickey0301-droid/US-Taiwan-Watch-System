from __future__ import annotations

from datetime import datetime
from pathlib import Path
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

TARGET_EXECUTIVE_TITLE_KEYWORDS = (
    "principal deputy assistant secretary",
    "deputy assistant secretary",
    "assistant secretary",
)


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
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
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

