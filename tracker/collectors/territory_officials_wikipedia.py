from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
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
from tracker.utils.social import discover_social_profiles
from tracker.utils.state_sources import merge_sources
from tracker.utils.territory_sources import (
    TERRITORY_CONGRESSIONAL_PEOPLE,
    TERRITORY_EXECUTIVE_PEOPLE,
    TERRITORY_LEGISLATURE_SOURCES,
)
from tracker.utils.text import compact_whitespace


logger = get_logger(__name__)


class TerritoryOfficialsWikipediaCollector(BaseCollector):
    collector_name = "territory_officials_wikipedia"
    source_name = "Wikipedia territory officials"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> dict[str, Any]:
        return {
            "executive_people": TERRITORY_EXECUTIVE_PEOPLE,
            "congressional_people": TERRITORY_CONGRESSIONAL_PEOPLE,
            "legislature_sources": TERRITORY_LEGISLATURE_SOURCES,
        }

    def parse(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for person_seed in payload.get("executive_people", []):
            record = self._parse_manual_person(person_seed, level="territory", branch="executive", chamber=None)
            if record:
                records.append(record)
        for person_seed in payload.get("congressional_people", []):
            record = self._parse_manual_person(person_seed, level="federal", branch="legislative", chamber="house")
            if record:
                records.append(record)
        for source in payload.get("legislature_sources", []):
            try:
                html = self._fetch_html(source["source_url"])
                records.extend(self._parse_legislature_page(source, html))
            except Exception as exc:
                logger.exception("Territory legislature parse failed for %s", source.get("territory"))
                records.append({"_error": {"territory": source.get("territory"), "message": str(exc)}})
        return records

    def sync(self) -> CollectorRunResult:
        result = CollectorRunResult(job_name=self.collector_name, source_name=self.source_name, started_at=datetime.utcnow())
        with session_scope() as session:
            sync_run = SyncRun(job_name=self.collector_name, job_type="collector", source_name=self.source_name)
            session.add(sync_run)
            session.flush()
            service: OfficialsService | None = None
            seen_keys_by_parser: dict[str, set[tuple[int, int, int | None, str]]] = {}
            try:
                parsed = self.parse(self.fetch())
                service = OfficialsService(session)
                usa = service.get_or_create_jurisdiction("United States", "country", code="US")
                for record in parsed:
                    if "_error" in record:
                        result.errors.append(str(record["_error"]))
                        continue
                    result.records_found += 1
                    jurisdiction = service.get_or_create_jurisdiction(
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
                        jurisdiction.id,
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
                    if service.upsert_appointment(person, office, jurisdiction.id, record["appointment"]):
                        result.records_created += 1
                    parser_identity = record["appointment"]["parser_identity"]
                    seen_keys_by_parser.setdefault(parser_identity, set()).add(
                        (person.id, office.id, jurisdiction.id, record["appointment"]["role_title"])
                    )
                for parser_identity, seen_keys in seen_keys_by_parser.items():
                    result.records_deactivated += service.reconcile_current_appointments(parser_identity, seen_keys)
                sync_run.status = "success"
            except Exception as exc:
                logger.exception("Territory officials wikipedia collector failed.")
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
                sync_run.meta = {"errors": result.errors, "validation_log": validation_log, "validation_count": len(validation_log)}
        return result

    def _fetch_html(self, url: str) -> str:
        response = httpx.get(
            url,
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        response.raise_for_status()
        if self.settings.snapshot_raw_responses:
            snapshot_dir = Path(self.settings.snapshots_dir)
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            slug = url.rstrip("/").split("/")[-1].replace(":", "_")
            (snapshot_dir / f"{slug}_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(response.text, encoding="utf-8")
        return response.text

    def _parse_manual_person(self, seed: dict[str, str], level: str, branch: str, chamber: str | None) -> dict[str, Any] | None:
        page_data = self._fetch_person_page(seed["source_url"])
        territory = seed["territory"]
        raw_payload = {
            "wikipedia_url": seed["source_url"],
            "territory": territory,
            "office_title": seed["role_title"],
            "department_name": territory,
            "top_department_name": territory,
            "subdepartment_name": seed["office_name"],
            "unit_name": seed["office_name"],
        }
        return {
            "person": {
                "full_name": seed["full_name"],
                "source_url": seed["source_url"],
                "source_type": "seed",
                "seed_source_type": "wikipedia",
                "profile_status": "seeded",
                "canonical_official_url": None,
                "portrait_url": page_data.get("portrait_url"),
                "portrait_source_url": seed["source_url"] if page_data.get("portrait_url") else None,
                "portrait_source_type": "wikipedia" if page_data.get("portrait_url") else None,
                "social_profiles": page_data.get("social_profiles") or {},
                "parser_identity": seed["parser_identity"],
                "verification_status": "unverified",
                "raw_payload": raw_payload,
            },
            "jurisdiction": {"name": territory, "type": "territory", "code": territory},
            "office": {
                "office_name": seed["office_name"],
                "level": level,
                "branch": branch,
                "chamber": chamber,
                "source_url": seed["source_url"],
                "source_type": "seed",
            },
            "appointment": {
                "role_title": seed["role_title"],
                "status": "current",
                "source_url": seed["source_url"],
                "source_type": "seed",
                "parser_identity": seed["parser_identity"],
                "is_current": True,
                "raw_payload": raw_payload,
            },
            "aliases": [f"{seed['role_title']} {seed['full_name']}"],
        }

    def _parse_legislature_page(self, source: dict[str, str], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        table = self._find_members_table(soup)
        if table is None:
            return []
        records: list[dict[str, Any]] = []
        for row in table.select("tr"):
            if not row.find("td"):
                continue
            record = self._parse_legislature_row(source, row)
            if record:
                records.append(record)
        return records

    def _find_members_table(self, soup: BeautifulSoup) -> Tag | None:
        for table in soup.select("table.wikitable"):
            headers = [compact_whitespace(cell.get_text(" ", strip=True)).lower() for cell in table.select("tr:first-child th")]
            header_text = " | ".join(headers)
            if "district" in header_text and any(token in header_text for token in ["senator", "representative", "name", "member", "delegate"]):
                return table
        for headline in soup.find_all(["h2", "h3"]):
            title = compact_whitespace(headline.get_text(" ", strip=True)).lower()
            if "current members" in title or title == "members":
                table = headline.find_next("table", class_="wikitable")
                if table:
                    return table
        return None

    def _parse_legislature_row(self, source: dict[str, str], row: Tag) -> dict[str, Any] | None:
        cells = row.find_all("td")
        if len(cells) < 2:
            return None
        text_cells = [compact_whitespace(cell.get_text(" ", strip=True)) for cell in cells]
        if not any(text_cells):
            return None
        district = text_cells[0]
        anchor = self._select_person_anchor(cells[1:4])
        if not anchor:
            return None
        full_name = self._person_name_from_anchor(anchor)
        if not full_name:
            return None
        territory = source["territory"]
        person_url = urljoin(source["source_url"], anchor["href"])
        page_data = self._fetch_person_page(person_url)
        raw_payload = {
            "wikipedia_url": person_url,
            "territory": territory,
            "district": district,
            "office_title": source["role_title"],
            "department_name": territory,
            "top_department_name": territory,
            "subdepartment_name": source["subdepartment_name"],
            "unit_name": district,
        }
        return {
            "person": {
                "full_name": full_name,
                "source_url": person_url,
                "source_type": "seed",
                "seed_source_type": "wikipedia",
                "profile_status": "seeded",
                "canonical_official_url": None,
                "portrait_url": page_data.get("portrait_url"),
                "portrait_source_url": person_url if page_data.get("portrait_url") else None,
                "portrait_source_type": "wikipedia" if page_data.get("portrait_url") else None,
                "social_profiles": page_data.get("social_profiles") or {},
                "parser_identity": "wikipedia_territory_legislatures_v1",
                "verification_status": "unverified",
                "raw_payload": raw_payload,
            },
            "jurisdiction": {"name": territory, "type": "territory", "code": territory},
            "office": {
                "office_name": source["office_name"],
                "level": "territory",
                "branch": "legislative",
                "chamber": source.get("chamber"),
                "source_url": source["source_url"],
                "source_type": "seed",
            },
            "appointment": {
                "role_title": source["role_title"],
                "district": district,
                "status": "current",
                "source_url": source["source_url"],
                "source_type": "seed",
                "parser_identity": "wikipedia_territory_legislatures_v1",
                "is_current": True,
                "raw_payload": raw_payload,
            },
            "aliases": [f"{source['role_title']} {full_name}"],
        }

    def _select_person_anchor(self, cells: list[Tag]) -> Tag | None:
        for cell in cells:
            for anchor in cell.find_all("a", href=True):
                candidate = self._person_name_from_anchor(anchor)
                if candidate:
                    return anchor
        return None

    def _person_name_from_anchor(self, anchor: Tag) -> str | None:
        candidate = compact_whitespace(anchor.get("title") or anchor.get_text(" ", strip=True))
        href = compact_whitespace(anchor.get("href") or "")
        if any(token in candidate.lower() for token in ["county", "district", "village", "island"]):
            return None
        if any(token in href.lower() for token in ["_county", "_district", "_village", "_island"]):
            return None
        if not self._looks_like_person_name(candidate):
            return None
        return candidate

    def _looks_like_person_name(self, value: str) -> bool:
        if not value:
            return False
        lowered = value.lower()
        if any(token in lowered for token in ["vacant", "democratic", "republican", "independent", "district", "legislature", "assembly", "senate", "house", "county", "village", "island"]):
            return False
        if re.search(r"\d", value):
            return False
        cleaned = re.sub(r"\s+", " ", value).strip(" ,")
        parts = [part for part in re.split(r"[\s,]+", cleaned) if part]
        if len(parts) < 2 or len(parts) > 6:
            return False
        person_token_count = 0
        for part in parts:
            token = part.strip(".")
            if token.lower() in {"jr", "sr", "ii", "iii", "iv"}:
                continue
            if re.fullmatch(r"[A-Z]\.?", token) or re.fullmatch(r"[A-Z][a-zA-Z'`-]+", token):
                person_token_count += 1
                continue
            return False
        return person_token_count >= 2

    def _fetch_person_page(self, url: str) -> dict[str, Any]:
        try:
            html = self._fetch_html(url)
        except Exception:
            return {"portrait_url": None, "social_profiles": {}}
        soup = BeautifulSoup(html, "lxml")
        infobox = soup.select_one("table.infobox")
        portrait_url = None
        if infobox:
            image = infobox.select_one("img")
            if image and image.get("src"):
                portrait_url = urljoin(url, image["src"].strip())
        try:
            social_profiles = discover_social_profiles(url, soup)
        except Exception:
            social_profiles = {}
        return {"portrait_url": portrait_url, "social_profiles": social_profiles}
