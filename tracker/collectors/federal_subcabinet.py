from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from tracker.collectors.base import BaseCollector, CollectorRunResult
from tracker.config import get_settings, get_source_registry
from tracker.db import session_scope
from tracker.logging_utils import get_logger
from tracker.models import SyncRun
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService
from tracker.services.roster_service import RosterService


logger = get_logger(__name__)


class FederalSubcabinetCollector(BaseCollector):
    collector_name = "federal_subcabinet"
    source_name = "Federal sub-cabinet registry"
    parser_identity = "federal_subcabinet_registry_v1"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.registry = get_source_registry().get("federal_subcabinet_sources", [])

    def fetch(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for source in self.registry:
            response = httpx.get(
                source["source_url"],
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
                source_name = source["department_name"].lower().replace(" ", "_").replace("/", "_")
                (snapshot_dir / f"{source_name}_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(response.text, encoding="utf-8")
            payloads.append({"source": source, "html": response.text})
        return payloads

    def parse(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in payload:
            source = item["source"]
            soup = BeautifulSoup(item["html"], "lxml")
            if source["parser_type"] == "officials_table":
                parsed.extend(self._parse_officials_table(source, soup, seen))
            elif source["parser_type"] == "person_cards":
                parsed.extend(self._parse_person_cards(source, soup, seen))
        return parsed

    def _parse_officials_table(self, source: dict[str, Any], soup: BeautifulSoup, seen: set[tuple[str, str]]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        table = soup.find("table")
        if not table:
            return parsed
        for row in table.select("tr")[1:]:
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            role_title = " ".join(cells[0].get_text(" ", strip=True).split())
            full_name = " ".join(cells[1].get_text(" ", strip=True).split())
            if not full_name or not role_title or "Vacant" in full_name or self._should_skip_role(source, role_title):
                continue
            key = (full_name, role_title)
            if key in seen:
                continue
            seen.add(key)
            parsed.append(self._build_record(source, full_name, role_title, None))
        return parsed

    def _parse_person_cards(self, source: dict[str, Any], soup: BeautifulSoup, seen: set[tuple[str, str]]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for card in soup.select(".collection-item article, .collection-item"):
            name_link = card.select_one(".collection-item__title a, .field--title a")
            title_node = card.select_one(".collection-item__summary, .field--field_person_title")
            image = card.select_one("img")
            if not name_link or not title_node:
                continue
            full_name = " ".join(name_link.get_text(" ", strip=True).split())
            role_title = " ".join(title_node.get_text(" ", strip=True).split())
            portrait_url = None
            if image and image.get("src"):
                portrait_url = urljoin(source["source_url"], image["src"].strip())
            if not full_name or not role_title or self._should_skip_role(source, role_title):
                continue
            key = (full_name, role_title)
            if key in seen:
                continue
            seen.add(key)
            parsed.append(self._build_record(source, full_name, role_title, portrait_url))
        return parsed

    def _build_record(
        self,
        source: dict[str, Any],
        full_name: str,
        role_title: str,
        portrait_url: str | None,
    ) -> dict[str, Any]:
        office_name = f"{source['department_name']}: {role_title}"
        return {
            "person": {
                "full_name": full_name,
                "source_url": source["source_url"],
                "source_type": "official",
                "seed_source_type": "official",
                "profile_status": "officially_enriched",
                "canonical_official_url": source["source_url"],
                "portrait_url": portrait_url,
                "portrait_source_url": source["source_url"] if portrait_url else None,
                "portrait_source_type": "official" if portrait_url else None,
                "parser_identity": source.get("parser_identity", self.parser_identity),
                "verification_status": "official",
                "raw_payload": {"department_name": source["department_name"], "office_title": role_title, "source_page": source["source_url"]},
            },
            "jurisdiction": {"name": "United States", "type": "country", "code": "US"},
            "office": {
                "office_name": office_name,
                "level": "federal",
                "branch": "executive",
                "chamber": None,
                "source_url": source["source_url"],
                "source_type": "official",
            },
            "appointment": {
                "role_title": office_name,
                "status": "current",
                "source_url": source["source_url"],
                "source_type": "official",
                "parser_identity": source.get("parser_identity", self.parser_identity),
                "is_current": True,
                "raw_payload": {"department_name": source["department_name"], "office_title": role_title},
            },
            "aliases": [full_name],
        }

    def _should_skip_role(self, source: dict[str, Any], role_title: str) -> bool:
        skip_titles = {item.lower() for item in source.get("skip_role_titles", [])}
        return role_title.lower() in skip_titles

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
                logger.exception("Federal sub-cabinet collector failed.")
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
