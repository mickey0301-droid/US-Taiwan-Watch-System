from __future__ import annotations

from datetime import datetime
from pathlib import Path
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


logger = get_logger(__name__)


class SenateSeedCollector(BaseCollector):
    collector_name = "congress_senate_seed"
    source_name = "Wikipedia seed list for current United States senators"
    source_url = "https://en.wikipedia.org/wiki/List_of_current_United_States_senators"
    parser_identity = "wikipedia_current_senators_v2"

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
            (snapshot_dir / f"senate_seed_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(response.text, encoding="utf-8")
        return response.text

    def parse(self, payload: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(payload, "html.parser")
        table = None
        for candidate in soup.select("table.wikitable"):
            headers = [th.get_text(" ", strip=True) for th in candidate.select("tr th")]
            if "State" in headers and "Senator" in headers and "Party" in headers:
                table = candidate
                break
        if table is None:
            return []

        parsed: list[dict[str, Any]] = []
        current_state = ""
        for row in table.select("tr")[1:]:
            cells = row.find_all("td")
            senator_cell = row.find("th")
            if len(cells) < 10 or not senator_cell:
                continue
            state_text = " ".join(cells[0].get_text(" ", strip=True).split()) if len(cells) == 11 else ""
            if state_text:
                current_state = state_text
            if not current_state:
                continue
            senator_anchor = senator_cell.find("a", href=True)
            full_name = " ".join(senator_cell.get_text(" ", strip=True).split())
            if not full_name:
                continue
            party_index = 4 if len(cells) == 11 else 3
            party = " ".join(cells[party_index].get_text(" ", strip=True).split())
            portrait_img = cells[1 if len(cells) == 11 else 0].find("img")
            portrait_url = None
            if portrait_img and portrait_img.get("src"):
                portrait_src = portrait_img["src"].strip()
                portrait_url = f"https:{portrait_src}" if portrait_src.startswith("//") else urljoin(self.source_url, portrait_src)
            person_url = urljoin(self.source_url, senator_anchor["href"]) if senator_anchor else self.source_url
            parsed.append(
                {
                    "person": {
                        "full_name": full_name,
                        "source_url": person_url,
                        "source_type": "wikipedia",
                        "seed_source_type": "wikipedia",
                        "profile_status": "seeded",
                        "portrait_url": portrait_url,
                        "portrait_source_url": person_url if portrait_url else None,
                        "portrait_source_type": "wikipedia" if portrait_url else None,
                        "parser_identity": self.parser_identity,
                        "verification_status": "unverified",
                        "raw_payload": {"wikipedia_url": person_url, "state": current_state, "party": party},
                    },
                    "jurisdiction": {"name": current_state, "type": "state", "code": current_state},
                    "office": {
                        "office_name": "United States Senator",
                        "level": "federal",
                        "branch": "legislative",
                        "chamber": "senate",
                        "source_url": self.source_url,
                        "source_type": "wikipedia",
                    },
                    "appointment": {
                        "role_title": "Senator",
                        "party": party,
                        "status": "current",
                        "source_url": person_url,
                        "source_type": "wikipedia",
                        "parser_identity": self.parser_identity,
                        "is_current": True,
                        "raw_payload": {"wikipedia_url": person_url, "state": current_state, "party": party},
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
                logger.exception("Senate seed collector failed.")
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

