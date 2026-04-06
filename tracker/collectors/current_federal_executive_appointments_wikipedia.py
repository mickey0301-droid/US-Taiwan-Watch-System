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


logger = get_logger(__name__)


SKIP_DEPARTMENTS = {
    "Color key",
    "Other independent agencies",
}


class CurrentFederalExecutiveAppointmentsWikipediaCollector(BaseCollector):
    collector_name = "current_federal_executive_appointments_wikipedia"
    source_name = "Wikipedia current federal executive appointments"
    source_url = "https://en.wikipedia.org/wiki/Political_appointments_of_the_second_Trump_administration"
    parser_identity = "wikipedia_second_trump_appointments_v1"

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
            (snapshot_dir / f"current_federal_exec_appointments_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(
                response.text,
                encoding="utf-8",
            )
        return response.text

    def parse(self, payload: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(payload, "html.parser")
        content = soup.select_one("#mw-content-text .mw-parser-output") or soup
        parsed: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        current_department = ""
        current_subdepartment = None
        for child in content.children:
            if not getattr(child, "name", None):
                continue
            if child.name == "h2":
                current_department = self._heading_text(child)
                current_subdepartment = None
                continue
            if child.name == "h3":
                subheading = self._heading_text(child)
                if current_department == "Independent intelligence agencies":
                    current_subdepartment = subheading
                elif current_department == "Cabinet-level independent agencies":
                    current_subdepartment = subheading
                elif current_department == "Other independent agencies":
                    current_subdepartment = subheading
                continue
            if child.name != "table" or "wikitable" not in (child.get("class") or []):
                continue
            if current_department in SKIP_DEPARTMENTS:
                continue
            headers = [th.get_text(" ", strip=True) for th in child.select("tr th")[:8]]
            if "Office" not in headers or "Nominee" not in headers:
                continue
            parsed.extend(self._parse_department_table(child, current_department, current_subdepartment, seen))
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
                logger.exception("Current federal executive appointments collector failed.")
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

    def _parse_department_table(
        self,
        table: Tag,
        department_name: str,
        subdepartment_name: str | None,
        seen: set[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for row in table.select("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 4:
                continue
            office_title = " ".join(cells[0].get_text(" ", strip=True).split())
            nominee_cell = cells[1]
            assumed_office = " ".join(cells[2].get_text(" ", strip=True).split())
            left_office = " ".join(cells[3].get_text(" ", strip=True).split())
            if not office_title or not assumed_office or left_office:
                continue
            anchor = None
            for candidate_anchor in nominee_cell.find_all("a", href=True):
                href = candidate_anchor.get("href", "")
                if "/wiki/" in href and ":" not in href.split("/wiki/")[-1]:
                    anchor = candidate_anchor
                    break
            full_name = " ".join((anchor.get_text(" ", strip=True) if anchor else nominee_cell.get_text(" ", strip=True)).split())
            if not full_name:
                continue
            person_url = urljoin(self.source_url, anchor["href"].strip()) if anchor else self.source_url
            office_name = f"{department_name}: {office_title}"
            key = (full_name, office_name)
            if key in seen:
                continue
            seen.add(key)
            parsed.append(
                {
                    "person": {
                        "full_name": full_name,
                        "source_url": person_url,
                        "source_type": "wikipedia",
                        "seed_source_type": "wikipedia",
                        "profile_status": "seeded",
                        "parser_identity": self.parser_identity,
                        "verification_status": "unverified",
                        "raw_payload": {
                            "wikipedia_url": person_url,
                            "source_page": self.source_url,
                            "top_department_name": department_name,
                            "subdepartment_name": subdepartment_name,
                            "department_name": department_name,
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
                            "top_department_name": department_name,
                            "subdepartment_name": subdepartment_name,
                            "department_name": department_name,
                            "office_title": office_title,
                            "assumed_office": assumed_office,
                            "wikipedia_person_url": person_url,
                        },
                    },
                    "aliases": [full_name],
                }
            )
        return parsed

    def _heading_text(self, heading: Tag) -> str:
        return " ".join(heading.get_text(" ", strip=True).split()).replace("[edit]", "").strip()

