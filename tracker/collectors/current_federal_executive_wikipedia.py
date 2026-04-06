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


EXCLUDED_OFFICES = {
    "Cabinet",
    "Cabinet-level officials",
    "Selected candidates for Cabinet positions",
    "Selected candidates for Cabinet-level positions",
    "Acting Cabinet officials",
    "Confirmation process",
}


class CurrentFederalExecutiveWikipediaCollector(BaseCollector):
    collector_name = "current_federal_executive_wikipedia"
    source_name = "Wikipedia current federal executive roster"
    source_url = "https://en.wikipedia.org/wiki/Second_cabinet_of_Donald_Trump"
    parser_identity = "wikipedia_second_trump_cabinet_v1"

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
            (snapshot_dir / f"current_federal_executive_wikipedia_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(
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
            office_name = self._extract_office_name(table)
            if not office_name or office_name in EXCLUDED_OFFICES:
                continue
            if not self._looks_like_person_table(table):
                continue
            person_info = self._extract_person_from_table(table)
            if not person_info:
                continue
            full_name, person_url, portrait_url = person_info
            key = (full_name, office_name)
            if key in seen:
                continue
            seen.add(key)
            social_profiles = self._fetch_wikipedia_social_profiles(person_url) if person_url else {}
            parsed.append(
                {
                    "person": {
                        "full_name": full_name,
                        "source_url": person_url or self.source_url,
                        "source_type": "wikipedia",
                        "seed_source_type": "wikipedia",
                        "profile_status": "seeded",
                        "portrait_url": portrait_url,
                        "portrait_source_url": person_url if portrait_url else None,
                        "portrait_source_type": "wikipedia" if portrait_url else None,
                        "social_profiles": social_profiles,
                        "parser_identity": self.parser_identity,
                        "verification_status": "unverified",
                        "raw_payload": {
                            "wikipedia_url": person_url,
                            "source_page": self.source_url,
                            "office_title": office_name,
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
                            "source_page": self.source_url,
                            "wikipedia_person_url": person_url,
                            "office_title": office_name,
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
                logger.exception("Current federal executive Wikipedia collector failed.")
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

    def _looks_like_person_table(self, table: Tag) -> bool:
        header_cells = [cell.get_text(" ", strip=True).lower() for cell in table.select("tr th")]
        return "name" in header_cells and "portrait" in header_cells

    def _extract_office_name(self, table: Tag) -> str:
        top_header = table.select_one("tr th")
        if not top_header:
            return ""
        return " ".join(top_header.get_text(" ", strip=True).split()).replace("[edit]", "").strip()

    def _extract_person_from_table(self, table: Tag) -> tuple[str, str | None, str | None] | None:
        data_row = None
        for row in table.select("tr"):
            if row.find("td"):
                data_row = row
                break
        if not data_row:
            return None
        cells = data_row.find_all(["td", "th"])
        if len(cells) < 2:
            return None

        portrait_url = None
        portrait_img = cells[0].find("img")
        if portrait_img and portrait_img.get("src"):
            portrait_url = urljoin(self.source_url, portrait_img["src"].strip())

        name_cell = cells[1]
        person_anchor = None
        for anchor in name_cell.find_all("a", href=True):
            href = anchor.get("href", "")
            if "/wiki/" in href and ":" not in href.split("/wiki/")[-1]:
                person_anchor = anchor
                break
        if person_anchor:
            full_name = " ".join(person_anchor.get_text(" ", strip=True).split())
            person_url = urljoin(self.source_url, person_anchor["href"].strip())
        else:
            full_name = " ".join(name_cell.get_text(" ", strip=True).split())
            person_url = None

        if not full_name:
            return None
        return full_name, person_url, portrait_url

    def _fetch_wikipedia_social_profiles(self, person_url: str) -> dict[str, str]:
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
        return discover_social_profiles(person_url, soup)

