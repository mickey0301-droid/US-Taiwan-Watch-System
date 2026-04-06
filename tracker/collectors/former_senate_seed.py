from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
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


class FormerSenateSeedCollector(BaseCollector):
    collector_name = "former_congress_senate_seed"
    source_name = "Wikipedia seed list for former United States senators"
    source_url = "https://en.wikipedia.org/wiki/List_of_former_United_States_senators"
    parser_identity = "wikipedia_former_senators_v1"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.cutoff_year = self.settings.historical_seed_cutoff_year

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
            (snapshot_dir / f"former_senate_seed_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(response.text, encoding="utf-8")
        return response.text

    def parse(self, payload: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(payload, "html.parser")
        parsed: list[dict[str, Any]] = []
        for table in soup.select("table.wikitable"):
            headers = [th.get_text(" ", strip=True) for th in table.select("tr th")]
            if not {"Senator", "State", "Years"}.issubset(set(headers)):
                continue
            for row in table.select("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) < 5:
                    continue
                senator_cell = cells[0]
                name_anchor = senator_cell.find("a", href=True)
                full_name = " ".join(senator_cell.get_text(" ", strip=True).split())
                state = " ".join(cells[3].get_text(" ", strip=True).split())
                party = " ".join(cells[4].get_text(" ", strip=True).split())
                years = " ".join(cells[1].get_text(" ", strip=True).split())
                if not full_name or not state or not self._is_recent_enough(years):
                    continue
                person_url = urljoin(self.source_url, name_anchor["href"]) if name_anchor else self.source_url
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
                            "raw_payload": {"wikipedia_url": person_url, "state": state, "party": party, "years": years},
                        },
                        "jurisdiction": {"name": state, "type": "state", "code": state},
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
                            "status": "former",
                            "source_url": person_url,
                            "source_type": "wikipedia",
                            "parser_identity": self.parser_identity,
                            "is_current": False,
                            "raw_payload": {"wikipedia_url": person_url, "state": state, "party": party, "years": years},
                        },
                        "aliases": [full_name],
                    }
                )
            break
        return parsed

    def _is_recent_enough(self, years_text: str) -> bool:
        years = [int(value) for value in re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", years_text)]
        if not years:
            return False
        return max(years) >= self.cutoff_year

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
                    for roster in roster_service.congress_rosters_for_years_text(record["appointment"]["raw_payload"].get("years", "")):
                        if roster_service.ensure_membership(
                            roster,
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
                logger.exception("Former senate seed collector failed.")
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

