from __future__ import annotations

import re
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


logger = get_logger(__name__)


class GovernorsCollector(BaseCollector):
    collector_name = "governors"
    source_name = "National Governors Association"
    source_url = "https://www.nga.org/cms/governors/bios"
    parser_identity = "nga_governors_html_v1"

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
            (snapshot_dir / f"governors_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(response.text, encoding="utf-8")
        return response.text

    def parse(self, payload: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(payload, "lxml")
        parsed: list[dict[str, Any]] = []
        heading = soup.find(lambda tag: tag.name in {"h2", "h3"} and "Current Governors" in tag.get_text(" ", strip=True))
        if not heading:
            return parsed
        container = heading.find_next("section") or heading.find_next(["ul", "div"])
        if not container:
            return parsed
        container_text = " ".join(container.get_text(" ", strip=True).split())
        pattern = re.compile(
            r"(Alabama|Alaska|American Samoa|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Guam|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Northern Mariana Islands|Ohio|Oklahoma|Oregon|Pennsylvania|Puerto Rico|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|U.S. Virgin Islands|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming)\s+Gov\.\s+(.+?)(?=\s+(?:Alabama|Alaska|American Samoa|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Guam|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Northern Mariana Islands|Ohio|Oklahoma|Oregon|Pennsylvania|Puerto Rico|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|U\.S\. Virgin Islands|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming)\s+Gov\.|$)"
        )
        seen: set[str] = set()
        for match in pattern.finditer(container_text):
            state = match.group(1).strip()
            governor_name = match.group(2).strip()
            key = f"{state}|{governor_name}"
            if key in seen:
                continue
            seen.add(key)
            profile_url = self.source_url
            parsed.append(
                {
                    "person": {
                        "full_name": governor_name,
                        "source_url": profile_url,
                        "source_type": "official",
                        "profile_status": "officially_enriched",
                        "canonical_official_url": profile_url,
                        "parser_identity": self.parser_identity,
                        "raw_payload": {"state": state, "profile_url": profile_url},
                    },
                    "jurisdiction": {"name": state, "type": "state", "code": state},
                    "office": {
                        "office_name": "Governor",
                        "level": "state",
                        "branch": "executive",
                        "chamber": None,
                        "source_url": self.source_url,
                        "source_type": "official",
                    },
                    "appointment": {
                        "role_title": "Governor",
                        "status": "current",
                        "source_url": profile_url,
                        "source_type": "official",
                        "parser_identity": self.parser_identity,
                        "is_current": True,
                        "raw_payload": {"state": state, "profile_url": profile_url},
                    },
                    "aliases": [f"Gov. {governor_name}"],
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
                    if service.upsert_appointment(person, office, state.id, record["appointment"]):
                        result.records_created += 1
                sync_run.status = "success"
            except Exception as exc:
                logger.exception("Governors collector failed.")
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
