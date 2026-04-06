from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from tracker.collectors.base import BaseCollector, CollectorRunResult
from tracker.config import get_settings
from tracker.db import session_scope
from tracker.logging_utils import get_logger
from tracker.models import SyncRun
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService


logger = get_logger(__name__)


class SenateCollector(BaseCollector):
    collector_name = "congress_senate"
    source_name = "United States Senate"
    source_url = "https://www.senate.gov/general/contact_information/senators_cfm.xml"
    parser_identity = "senate_xml_v1"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> str:
        response = httpx.get(self.source_url, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        if self.settings.snapshot_raw_responses:
            snapshot_dir = Path(self.settings.snapshots_dir)
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = snapshot_dir / f"senate_{datetime.utcnow():%Y%m%d%H%M%S}.xml"
            snapshot_path.write_text(response.text, encoding="utf-8")
        return response.text

    def parse(self, payload: str) -> list[dict[str, Any]]:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(payload)
        parsed: list[dict[str, Any]] = []
        for member in root.findall(".//member"):
            first_name = (member.findtext("first_name") or "").strip()
            last_name = (member.findtext("last_name") or "").strip()
            full_name = f"{first_name} {last_name}".strip()
            state = (member.findtext("state") or "").strip()
            party = (member.findtext("party") or "").strip()
            website = (member.findtext("website") or "").strip()
            email = (member.findtext("email") or "").strip()
            parsed.append(
                {
                    "person": {
                        "full_name": full_name,
                        "given_name": first_name,
                        "family_name": last_name,
                        "source_url": website or self.source_url,
                        "source_type": "official",
                        "parser_identity": self.parser_identity,
                        "raw_payload": {"website": website, "email": email, "state": state, "party": party},
                    },
                    "jurisdiction": {"name": state, "type": "state", "code": state},
                    "office": {
                        "office_name": "United States Senator",
                        "level": "federal",
                        "branch": "legislative",
                        "chamber": "senate",
                        "source_url": self.source_url,
                        "source_type": "official",
                    },
                    "appointment": {
                        "role_title": "Senator",
                        "party": party,
                        "status": "current",
                        "source_url": website or self.source_url,
                        "source_type": "official",
                        "parser_identity": self.parser_identity,
                        "is_current": True,
                        "raw_payload": {"website": website, "email": email},
                    },
                    "aliases": [full_name.replace(" ", ", ", 1)] if full_name else [],
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
                payload = self.fetch()
                records = self.parse(payload)
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
                logger.exception("Senate collector failed.")
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


def run() -> dict[str, Any]:
    result = SenateCollector().sync()
    return {
        "job_name": result.job_name,
        "status": "failed" if result.errors else "success",
        "records_found": result.records_found,
        "records_created": result.records_created,
        "records_updated": result.records_updated,
        "errors": result.errors,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
