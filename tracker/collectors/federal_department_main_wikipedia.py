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


FEDERAL_DEPARTMENT_WIKIPEDIA_PAGES = [
    {"department_name": "Department of State", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_State"},
    {"department_name": "Department of the Treasury", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_the_Treasury"},
    {"department_name": "Department of Defense", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Defense"},
    {"department_name": "Department of Justice", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Justice"},
    {"department_name": "Department of the Interior", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_the_Interior"},
    {"department_name": "Department of Agriculture", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Agriculture"},
    {"department_name": "Department of Commerce", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Commerce"},
    {"department_name": "Department of Labor", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Labor"},
    {"department_name": "Department of Health and Human Services", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Health_and_Human_Services"},
    {"department_name": "Department of Housing and Urban Development", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Housing_and_Urban_Development"},
    {"department_name": "Department of Transportation", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Transportation"},
    {"department_name": "Department of Energy", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Energy"},
    {"department_name": "Department of Education", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Education"},
    {"department_name": "Department of Veterans Affairs", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Veterans_Affairs"},
    {"department_name": "Department of Homeland Security", "url": "https://en.wikipedia.org/wiki/United_States_Department_of_Homeland_Security"},
]


class FederalDepartmentMainWikipediaCollector(BaseCollector):
    collector_name = "federal_department_main_wikipedia"
    source_name = "Wikipedia federal department main pages"
    source_url = "https://en.wikipedia.org/wiki/Cabinet_of_the_United_States"
    parser_identity = "wikipedia_federal_department_main_v1"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> list[dict[str, str]]:
        return FEDERAL_DEPARTMENT_WIKIPEDIA_PAGES

    def parse(self, payload: list[dict[str, str]]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in payload:
            page_data = self._fetch_department_page(item["url"])
            if not page_data or not page_data.get("full_name"):
                continue
            office_title = page_data.get("role_title") or self._default_role_title(item["department_name"])
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
                            "department_name": item["department_name"],
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
                            "department_name": item["department_name"],
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
                sync_run.meta = {
                    "errors": result.errors,
                    "validation_log": validation_log,
                    "validation_count": len(validation_log),
                }
        return result

    def _fetch_department_page(self, url: str) -> dict[str, Any] | None:
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

        if self.settings.snapshot_raw_responses:
            snapshot_dir = Path(self.settings.snapshots_dir)
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            filename = url.rstrip("/").split("/")[-1]
            (snapshot_dir / f"federal_department_main_{filename}_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(
                response.text,
                encoding="utf-8",
            )

        soup = BeautifulSoup(response.text, "lxml")
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
            if header not in {"agency executive", "secretary", "administrator", "incumbent"}:
                continue
            for anchor in value_cell.find_all("a", href=True):
                href = anchor.get("href", "")
                if "/wiki/" in href and ":" not in href.split("/wiki/")[-1]:
                    person_anchor = anchor
                    break
            if person_anchor:
                text = " ".join(value_cell.get_text(" ", strip=True).split())
                role_title = text.replace(person_anchor.get_text(" ", strip=True), "", 1).strip(" ,;–-")
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
            person_soup = BeautifulSoup(person_response.text, "lxml")
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

    def _default_role_title(self, department_name: str) -> str:
        if department_name == "Department of Justice":
            return "Attorney General"
        if department_name == "Department of Veterans Affairs":
            return "Secretary of Veterans Affairs"
        if department_name == "Department of Homeland Security":
            return "Secretary of Homeland Security"
        if department_name == "Department of Health and Human Services":
            return "Secretary of Health and Human Services"
        if department_name.startswith("Department of the "):
            return f"Secretary of the {department_name.replace('Department of the ', '')}"
        if department_name.startswith("Department of "):
            return f"Secretary of {department_name.replace('Department of ', '')}"
        return department_name
