from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from tracker.collectors.base import BaseCollector, CollectorRunResult
from tracker.config import get_settings, get_source_registry
from tracker.db import session_scope
from tracker.logging_utils import get_logger
from tracker.models import SyncRun
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService
from tracker.utils.social import discover_social_profiles
from tracker.utils.state_sources import STATE_HOUSE_WIKIPEDIA_SOURCES, merge_sources
from tracker.utils.text import compact_whitespace


logger = get_logger(__name__)


class StateRepresentativesWikipediaCollector(BaseCollector):
    collector_name = "state_representatives_wikipedia"
    source_name = "Wikipedia state house rosters"
    parser_identity = "wikipedia_state_representatives_v1"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> list[dict[str, Any]]:
        configured = get_source_registry().get("state_house_wikipedia_sources", [])
        return merge_sources(configured, STATE_HOUSE_WIKIPEDIA_SOURCES)

    def parse(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for source in payload:
            try:
                html = self._fetch_html(source["source_url"])
                records.extend(self._parse_state_page(source, html))
            except Exception as exc:
                logger.exception("State house wikipedia parse failed for %s", source.get("state"))
                records.append(
                    {
                        "_error": {
                            "state": source.get("state"),
                            "source_url": source.get("source_url"),
                            "message": str(exc),
                        }
                    }
                )
        return records

    def sync(self) -> CollectorRunResult:
        result = CollectorRunResult(job_name=self.collector_name, source_name=self.source_name, started_at=datetime.utcnow())
        with session_scope() as session:
            sync_run = SyncRun(job_name=self.collector_name, job_type="collector", source_name=self.source_name)
            session.add(sync_run)
            session.flush()
            service: OfficialsService | None = None
            try:
                parsed = self.parse(self.fetch())
                service = OfficialsService(session)
                usa = service.get_or_create_jurisdiction("United States", "country", code="US")
                for record in parsed:
                    if "_error" in record:
                        result.errors.append(str(record["_error"]))
                        continue
                    result.records_found += 1
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
                logger.exception("State representatives wikipedia collector failed.")
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

    def _fetch_html(self, url: str) -> str:
        response = httpx.get(
            url,
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
            slug = url.rstrip("/").split("/")[-1].replace(":", "_")
            (snapshot_dir / f"{slug}_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(response.text, encoding="utf-8")
        return response.text

    def _parse_state_page(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        state = source["state"]
        page_url = source["source_url"]
        table = self._find_members_table(soup)
        if table is None:
            return []

        parsed: list[dict[str, Any]] = []
        for row in table.select("tr"):
            cells = row.find_all(["td", "th"])
            if not cells or not row.find("td"):
                continue
            member = self._parse_member_row(row, source, state, page_url)
            if member:
                parsed.append(member)
        return parsed

    def _find_members_table(self, soup: BeautifulSoup) -> Tag | None:
        for table in soup.select("table.wikitable"):
            headers = [compact_whitespace(cell.get_text(" ", strip=True)).lower() for cell in table.select("tr:first-child th")]
            header_text = " | ".join(headers)
            if "district" not in header_text:
                continue
            if any(token in header_text for token in ["representative", "member", "name", "delegate", "assembly"]):
                return table
        for headline in soup.find_all(["h2", "h3"]):
            title = compact_whitespace(headline.get_text(" ", strip=True)).lower()
            if "current members" in title or title == "members":
                table = headline.find_next("table", class_="wikitable")
                if table:
                    return table
        return None

    def _parse_member_row(self, row: Tag, source: dict[str, Any], state: str, page_url: str) -> dict[str, Any] | None:
        cells = row.find_all("td")
        if len(cells) < 2:
            return None
        text_cells = [compact_whitespace(cell.get_text(" ", strip=True)) for cell in cells]
        if not any(text_cells):
            return None

        district = text_cells[0]
        if district.lower() in {"district", ""}:
            return None

        anchor = self._select_person_anchor(cells[1:4])
        if not anchor:
            return None

        full_name = self._person_name_from_anchor(anchor)
        if not full_name:
            return None

        person_url = urljoin(page_url, anchor["href"])
        page_data = self._fetch_person_page(person_url)
        office_name = source.get("office_name") or f"{state} State House"
        role_title = source.get("role_title") or "State Representative"
        subdepartment_name = source.get("subdepartment_name") or "State House"
        chamber = source.get("chamber") or "house"
        aliases = [f"Rep. {full_name}", f"{state} state representative {full_name}"]
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
                "parser_identity": self.parser_identity,
                "verification_status": "unverified",
                "raw_payload": {
                    "wikipedia_url": person_url,
                    "state": state,
                    "district": district,
                    "office_title": role_title,
                    "department_name": state,
                    "top_department_name": state,
                    "subdepartment_name": subdepartment_name,
                    "unit_name": district,
                },
            },
            "jurisdiction": {"name": state, "type": "state", "code": state},
            "office": {
                "office_name": office_name,
                "level": "state",
                "branch": "legislative",
                "chamber": chamber,
                "source_url": page_url,
                "source_type": "seed",
            },
            "appointment": {
                "role_title": role_title,
                "district": district,
                "status": "current",
                "source_url": page_url,
                "source_type": "seed",
                "parser_identity": self.parser_identity,
                "is_current": True,
                "raw_payload": {
                    "wikipedia_url": person_url,
                    "state": state,
                    "district": district,
                    "office_title": role_title,
                    "department_name": state,
                    "top_department_name": state,
                    "subdepartment_name": subdepartment_name,
                    "unit_name": district,
                },
            },
            "aliases": aliases,
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
        if not self._looks_like_person_name(candidate):
            return None
        return candidate

    def _looks_like_person_name(self, value: str) -> bool:
        if not value:
            return False
        lowered = value.lower()
        banned_tokens = {
            "vacant",
            "democratic",
            "republican",
            "independent",
            "member",
            "speaker",
            "majority",
            "minority",
            "district",
            "at-large",
        }
        if lowered in banned_tokens:
            return False
        if any(token in lowered for token in ["party", "caucus", "conference", "election", "legislature"]):
            return False
        if re.search(r"\d", value):
            return False
        cleaned = re.sub(r"\s+", " ", value).strip(" ,")
        parts = [part for part in re.split(r"[\s,]+", cleaned) if part]
        if len(parts) < 2:
            return False
        if len(parts) > 5:
            return False
        person_token_count = 0
        for part in parts:
            token = part.strip(".")
            if token.lower() in {"jr", "sr", "ii", "iii", "iv"}:
                continue
            if re.fullmatch(r"[A-Z]\.?", token):
                person_token_count += 1
                continue
            if re.fullmatch(r"[A-Z][a-zA-Z'`-]+", token):
                person_token_count += 1
                continue
            return False
        return person_token_count >= 2

    def _fetch_person_page(self, url: str) -> dict[str, Any]:
        try:
            html = self._fetch_html(url)
        except Exception:
            return {"portrait_url": None, "social_profiles": {}}
        soup = BeautifulSoup(html, "html.parser")
        infobox = soup.select_one("table.infobox")
        portrait_url = None
        if infobox:
            image = infobox.select_one("img")
            if image and image.get("src"):
                portrait_url = urljoin(url, image["src"].strip())
        social_profiles = {}
        try:
            social_profiles = discover_social_profiles(url, soup)
        except Exception:
            social_profiles = {}
        return {"portrait_url": portrait_url, "social_profiles": social_profiles}

