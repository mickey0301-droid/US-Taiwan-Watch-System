from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from tracker.collectors.base import BaseCollector, CollectorRunResult
from tracker.config import get_settings
from tracker.db import session_scope
from tracker.logging_utils import get_logger
from tracker.models import SyncRun
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService
from tracker.services.roster_service import RosterService


logger = get_logger(__name__)


class HouseCollector(BaseCollector):
    collector_name = "congress_house"
    source_name = "United States House of Representatives"
    source_url = "https://www.house.gov/representatives"
    parser_identity = "house_html_v1"

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
            (snapshot_dir / f"house_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(response.text, encoding="utf-8")
        return response.text

    def parse(self, payload: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(payload, "html.parser")
        parsed: list[dict[str, Any]] = []
        for table in soup.select("table"):
            caption = table.find("caption")
            state_name = caption.get_text(" ", strip=True) if caption else ""
            if not state_name:
                prev_heading = table.find_previous(["h2", "h3", "h4"])
                state_name = prev_heading.get_text(" ", strip=True) if prev_heading else ""
            header_cells = [cell.get_text(" ", strip=True).lower() for cell in table.select("tr th")]
            has_expected_header = "district" in header_cells and "name" in header_cells
            if not state_name or not has_expected_header:
                continue

            for row in table.select("tr"):
                data_cells = row.find_all("td")
                if len(data_cells) < 3:
                    continue
                district = data_cells[0].get_text(" ", strip=True)
                name_anchor = data_cells[1].find("a", href=True)
                name = name_anchor.get_text(" ", strip=True) if name_anchor else data_cells[1].get_text(" ", strip=True)
                party = data_cells[2].get_text(" ", strip=True) if len(data_cells) > 2 else None
                if not name_anchor or not name or not district:
                    continue
                if any(token in name.lower() for token in ["district", "name", "party"]):
                    continue
                website = name_anchor["href"] or self.source_url
                parsed.append(
                    {
                        "person": {
                            "full_name": name.replace(",", ""),
                            "source_url": website,
                            "source_type": "official",
                            "parser_identity": self.parser_identity,
                            "profile_status": "officially_enriched",
                            "canonical_official_url": website,
                            "raw_payload": {"state": state_name, "district": district, "party": party},
                        },
                        "jurisdiction": {"name": state_name, "type": "state", "code": state_name},
                        "office": {
                            "office_name": "United States Representative",
                            "level": "federal",
                            "branch": "legislative",
                            "chamber": "house",
                            "source_url": self.source_url,
                            "source_type": "official",
                        },
                        "appointment": {
                            "role_title": "Representative",
                            "district": district,
                            "party": party,
                            "status": "current",
                            "source_url": website,
                            "source_type": "official",
                            "parser_identity": self.parser_identity,
                            "is_current": True,
                            "raw_payload": {"state": state_name, "district": district},
                        },
                        "aliases": [name],
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
                current_congress = roster_service.current_congress_roster()
                result.records_found = len(records)
                usa = service.get_or_create_jurisdiction("United States", "country", code="US")
                for record in records:
                    state = service.get_or_create_jurisdiction(
                        record["jurisdiction"]["name"],
                        record["jurisdiction"]["type"],
                        code=record["jurisdiction"].get("code"),
                        parent_id=usa.id,
                    )
                    office = service.get_or_create_office(
                        record["office"]["office_name"],
                        record["office"]["level"],
                        record["office"].get("branch"),
                        record["office"].get("chamber"),
                        state.id,
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
                        current_congress,
                        person,
                        office,
                        state.id,
                        record["appointment"]["role_title"],
                        record["appointment"].get("party"),
                        record["appointment"].get("status"),
                        record["appointment"].get("source_url"),
                        record["appointment"].get("source_type"),
                        record["appointment"].get("parser_identity"),
                        raw_payload=record["appointment"].get("raw_payload"),
                    ):
                        result.records_created += 1
                    if service.upsert_appointment(person, office, state.id, record["appointment"]):
                        result.records_created += 1
                sync_run.status = "success"
            except Exception as exc:
                logger.exception("House collector failed.")
                result.errors.append(str(exc))
                sync_run.status = "failed"
                sync_run.error_message = str(exc)
            finally:
                result.ended_at = datetime.utcnow()
                sync_run.started_at = result.started_at
                sync_run.ended_at = result.ended_at
                sync_run.records_found = result.records_found
                sync_run.records_created = result.records_created
                sync_run.records_updated = result.records_updated
                sync_run.records_deactivated = result.records_deactivated
                validation_log = service.validation_log if service else []
                result.metadata["validation_log"] = validation_log
                result.metadata["validation_count"] = len(validation_log)
                sync_run.meta = {
                    "errors": result.errors,
                    "validation_log": validation_log,
                    "validation_count": len(validation_log),
                }
        return result

