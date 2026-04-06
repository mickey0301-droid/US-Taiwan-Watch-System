from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from tracker.collectors.base import BaseCollector, CollectorRunResult
from tracker.config import get_settings
from tracker.db import session_scope
from tracker.logging_utils import get_logger
from tracker.models import SyncRun
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService


logger = get_logger(__name__)


class CongressApiMembersCollector(BaseCollector):
    collector_name = "congress_api_members"
    source_name = "Congress.gov API Members"
    parser_identity = "congress_api_members_v1"

    def __init__(self, current_member: bool = True) -> None:
        self.settings = get_settings()
        self.current_member = current_member
        self.congress_number = self.settings.congress_current_number
        self.source_url = f"https://api.congress.gov/v3/member/congress/{self.congress_number}"

    def fetch(self) -> list[dict[str, Any]]:
        if not self.settings.congress_api_key:
            raise RuntimeError("CONGRESS_API_KEY is not configured.")

        members: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = httpx.get(
                self.source_url,
                headers={
                    "X-API-Key": self.settings.congress_api_key,
                    "Accept": "application/json",
                    "User-Agent": "US-Taiwan-Watch/1.0",
                },
                params={
                    "format": "json",
                    "limit": 250,
                    "offset": offset,
                    "currentMember": str(self.current_member).lower(),
                },
                timeout=30.0,
                follow_redirects=True,
                trust_env=False,
            )
            response.raise_for_status()
            data = response.json()
            batch = data.get("members", [])
            if not batch:
                break
            members.extend(batch)
            if len(batch) < 250:
                break
            offset += 250
        return members

    def parse(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for member in payload:
            name = member.get("name", "").strip()
            chamber_name = ""
            terms = member.get("terms", {}).get("item", [])
            if terms:
                chamber_name = terms[-1].get("chamber", "")
            chamber = "senate" if "Senate" in chamber_name else "house" if "House" in chamber_name else None
            if chamber not in {"senate", "house"}:
                continue
            office_name = "United States Senator" if chamber == "senate" else "United States Representative"
            role_title = "Senator" if chamber == "senate" else "Representative"
            district = str(member.get("district")) if member.get("district") is not None else None
            member_url = member.get("url")
            image_url = ((member.get("depiction") or {}).get("imageUrl")) or None
            current_term = terms[-1] if terms else {}
            enriched_raw_payload = dict(member)
            enriched_raw_payload.update(
                {
                    "congress_profile_url": member_url,
                    "party": member.get("partyName") or member.get("party") or current_term.get("party"),
                    "district": district,
                    "state": member.get("state"),
                    "committees": member.get("committees", []),
                    "congress_service_history": self._build_service_history(terms),
                }
            )
            appointment_raw_payload = dict(enriched_raw_payload)
            parsed.append(
                {
                    "person": {
                        "full_name": name.replace(",", ""),
                        "source_url": member_url,
                        "source_type": "official",
                        "seed_source_type": "official",
                        "profile_status": "officially_enriched",
                        "canonical_official_url": member_url,
                        "portrait_url": image_url,
                        "parser_identity": self.parser_identity,
                        "verification_status": "official_api",
                        "raw_payload": enriched_raw_payload,
                    },
                    "jurisdiction": {"name": member.get("state"), "type": "state", "code": member.get("state")},
                    "office": {
                        "office_name": office_name,
                        "level": "federal",
                        "branch": "legislative",
                        "chamber": chamber,
                        "source_url": self.source_url,
                        "source_type": "official",
                    },
                    "appointment": {
                        "role_title": role_title,
                        "district": district,
                        "party": member.get("partyName") or member.get("party"),
                        "status": "current" if self.current_member else "former",
                        "source_url": member_url or self.source_url,
                        "source_type": "official",
                        "parser_identity": self.parser_identity,
                        "is_current": self.current_member,
                        "raw_payload": appointment_raw_payload,
                    },
                    "aliases": [name] if name else [],
                }
            )
        return parsed

    def _build_service_history(self, terms: list[dict[str, Any]]) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        for term in terms:
            chamber_name = term.get("chamber") or ""
            chamber = "senate" if "Senate" in chamber_name else "house" if "House" in chamber_name else chamber_name
            district = term.get("district")
            district_text = str(district) if district not in (None, "") else None
            history.append(
                {
                    "chamber": chamber,
                    "label": chamber_name or chamber,
                    "congress": term.get("congress"),
                    "start_year": term.get("startYear"),
                    "end_year": term.get("endYear"),
                    "state_code": term.get("stateCode"),
                    "district": district_text,
                    "party": term.get("party"),
                }
            )
        return history

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
                    state_name = record["jurisdiction"]["name"] or "United States"
                    state = service.get_or_create_jurisdiction(
                        state_name,
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
                logger.exception("Congress API members collector failed.")
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
                validation_log = service.validation_log if service else []
                result.metadata["validation_log"] = validation_log
                result.metadata["validation_count"] = len(validation_log)
                sync_run.meta = {
                    "errors": result.errors,
                    "current_member": self.current_member,
                    "validation_log": validation_log,
                    "validation_count": len(validation_log),
                }
        return result
