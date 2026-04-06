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
from tracker.utils.text import compact_whitespace


logger = get_logger(__name__)


class StateExecutiveOfficialPagesCollector(BaseCollector):
    collector_name = "state_executive_official_pages"
    source_name = "State executive official pages"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> list[dict[str, Any]]:
        return get_source_registry().get("state_executive_official_sources", [])

    def parse(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for source in payload:
            try:
                html = self._fetch_html(source["source_url"])
                records.extend(self._parse_source(source, html))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 403 and source.get("fallback_records"):
                    logger.warning("Using fallback records for blocked official page %s", source.get("source_url"))
                    records.extend(self._parse_fallback_records(source))
                    continue
                logger.exception("State executive official page parse failed for %s", source.get("source_url"))
                records.append(
                    {
                        "_error": {
                            "state": source.get("state"),
                            "source_url": source.get("source_url"),
                            "message": str(exc),
                        }
                    }
                )
            except Exception as exc:
                logger.exception("State executive official page parse failed for %s", source.get("source_url"))
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

    def _parse_fallback_records(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in source.get("fallback_records", []):
            full_name = item.get("full_name")
            role_title = item.get("role_title")
            if not full_name or not role_title:
                continue
            records.append(
                self._build_record(
                    {**source, **{k: v for k, v in item.items() if k in {"office_name", "subdepartment_name"}}},
                    full_name,
                    role_title,
                    item.get("profile_url") or source["source_url"],
                )
            )
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
                    parser_identity = record["appointment"]["parser_identity"]
                    seen_keys_by_parser.setdefault(parser_identity, set()).add(
                        (person.id, office.id, state.id, record["appointment"]["role_title"])
                    )
                for parser_identity, seen_keys in seen_keys_by_parser.items():
                    result.records_deactivated += service.reconcile_current_appointments(parser_identity, seen_keys)
                sync_run.status = "success"
            except Exception as exc:
                logger.exception("State executive official pages collector failed.")
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
            slug = url.rstrip("/").split("/")[-1].replace(":", "_").replace("?", "_").replace("&", "_").replace("=", "_")
            (snapshot_dir / f"{slug}_{datetime.utcnow():%Y%m%d%H%M%S}.html").write_text(response.text, encoding="utf-8")
        return response.text

    def _parse_source(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        parser_type = source.get("parser_type")
        if parser_type == "mass_key_contacts":
            return self._parse_mass_key_contacts(source, html)
        if parser_type == "mass_single_official":
            return self._parse_mass_single_official(source, html)
        if parser_type == "ca_governor_cabinet_cards":
            return self._parse_ca_governor_cabinet_cards(source, html)
        if parser_type == "flgov_leadership_people_links":
            return self._parse_flgov_leadership_people_links(source, html)
        return []

    def _parse_mass_key_contacts(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        records: list[dict[str, Any]] = []
        table = soup.find("table")
        if table:
            records.extend(self._parse_mass_contact_table(source, table))
        if not records:
            text = compact_whitespace(soup.get_text("\n", strip=True))
            records.extend(self._parse_mass_key_contact_lines(source, text))
        return records

    def _parse_mass_contact_table(self, source: dict[str, Any], table: Tag) -> list[dict[str, Any]]:
        rows = table.find_all("tr")
        results: list[dict[str, Any]] = []
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            name = compact_whitespace(cells[0].get_text(" ", strip=True))
            title = compact_whitespace(cells[1].get_text(" ", strip=True))
            if not self._looks_like_person_name(name) or not self._looks_like_office_title(title):
                continue
            results.append(self._build_record(source, name, title, source["source_url"]))
        return results

    def _parse_mass_key_contact_lines(self, source: dict[str, Any], text: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        pattern = re.compile(
            r"([A-Z][A-Za-z.\-']+(?:\s+[A-Z][A-Za-z.\-']+){1,4})\s*,?\s+(Secretary|Undersecretary(?:\s+and\s+Chief\s+of\s+Staff)?|Chief of Staff(?:\s+and\s+Undersecretary)?|Chief Operating Officer|Assistant Secretary(?:\s+for\s+[A-Za-z &-]+)?|Chief Legal Counsel|Chief Financial Officer|Communications Director)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            name = compact_whitespace(match.group(1))
            title = compact_whitespace(match.group(2))
            if not self._looks_like_person_name(name):
                continue
            results.append(self._build_record(source, name, title, source["source_url"]))
        return results

    def _parse_mass_single_official(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        heading = soup.find(["h1", "h2"])
        text = compact_whitespace(soup.get_text("\n", strip=True))
        role_title = source.get("role_title") or compact_whitespace((heading.get_text(" ", strip=True) if heading else "Secretary"))
        pattern = re.compile(r"([A-Z][A-Za-z.\-']+(?:\s+[A-Z][A-Za-z.\-']+){1,4})\s*,?\s+" + re.escape(role_title), re.IGNORECASE)
        name = None
        match = pattern.search(text)
        if match:
            name = compact_whitespace(match.group(1))
        if not name:
            heading_text = compact_whitespace((heading.get_text(" ", strip=True) if heading else ""))
            if role_title.lower() in heading_text.lower():
                name_match = re.search(r"([A-Z][A-Za-z.\-']+(?:\s+[A-Z][A-Za-z.\-']+){1,4})", heading_text)
                if name_match:
                    name = compact_whitespace(name_match.group(1))
        if not name or not self._looks_like_person_name(name):
            return []
        return [self._build_record(source, name, role_title, source["source_url"])]

    def _parse_ca_governor_cabinet_cards(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        results: list[dict[str, Any]] = []
        for item in soup.select("ul.cards li"):
            heading = item.find("h2")
            title_tag = item.select_one("p.title")
            if not heading or not title_tag:
                continue
            office_name = compact_whitespace(heading.get_text(" ", strip=True))
            role_line = compact_whitespace(title_tag.get_text(" ", strip=True))
            if not office_name or not role_line:
                continue
            role_title = role_line
            full_name = role_line
            prefixes = [
                "Secretary ",
                "Director ",
                "Executive Director ",
                "Chief Service Officer ",
                "Adjutant General ",
            ]
            for prefix in prefixes:
                if role_line.startswith(prefix):
                    role_title = prefix.strip()
                    full_name = role_line[len(prefix):].strip()
                    break
            if role_title == role_line:
                parts = role_line.split(" ", 1)
                if len(parts) == 2:
                    role_title, full_name = parts[0], parts[1]
            if not self._looks_like_person_name(full_name):
                continue
            link = heading.find("a", href=True)
            profile_url = link["href"] if link else source["source_url"]
            record_source = dict(source)
            record_source["office_name"] = office_name
            record_source["subdepartment_name"] = office_name
            results.append(self._build_record(record_source, full_name, role_title, profile_url))
        return results

    def _parse_flgov_leadership_people_links(self, source: dict[str, Any], html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            text = compact_whitespace(anchor.get_text(" ", strip=True))
            href = anchor.get("href", "")
            if "/eog/leadership/people/" not in href:
                continue
            if text.startswith("Governor "):
                full_name = text.replace("Governor ", "", 1).strip()
                role_title = "Governor"
                office_name = "Governor"
            elif text.startswith("Lieutenant Governor "):
                full_name = text.replace("Lieutenant Governor ", "", 1).strip()
                role_title = "Lieutenant Governor"
                office_name = "Lieutenant Governor"
            else:
                continue
            if not self._looks_like_person_name(full_name) or full_name in seen:
                continue
            profile_url = urljoin(source["source_url"], href)
            record_source = dict(source)
            record_source["office_name"] = office_name
            record_source["subdepartment_name"] = "Executive Office of the Governor"
            results.append(self._build_record(record_source, full_name, role_title, profile_url))
            seen.add(full_name)
        return results

    def _build_record(self, source: dict[str, Any], full_name: str, role_title: str, profile_url: str) -> dict[str, Any]:
        state = source["state"]
        office_name = source.get("office_name") or role_title
        subdepartment_name = source.get("subdepartment_name") or office_name
        parser_identity = source["parser_identity"]
        raw_payload = {
            "state": state,
            "office_title": role_title,
            "department_name": state,
            "top_department_name": state,
            "subdepartment_name": subdepartment_name,
            "unit_name": office_name,
            "official_roster_url": source["source_url"],
        }
        return {
            "person": {
                "full_name": full_name,
                "source_url": profile_url,
                "source_type": "official",
                "seed_source_type": "official",
                "profile_status": "officially_enriched",
                "canonical_official_url": profile_url,
                "parser_identity": parser_identity,
                "verification_status": "official_link",
                "raw_payload": raw_payload,
            },
            "jurisdiction": {"name": state, "type": "state", "code": state},
            "office": {
                "office_name": office_name,
                "level": "state",
                "branch": "executive",
                "chamber": None,
                "source_url": source["source_url"],
                "source_type": "official",
            },
            "appointment": {
                "role_title": role_title,
                "status": "current",
                "source_url": profile_url,
                "source_type": "official",
                "parser_identity": parser_identity,
                "is_current": True,
                "raw_payload": raw_payload,
            },
            "aliases": [f"{role_title} {full_name}"],
        }

    def _looks_like_person_name(self, value: str) -> bool:
        if not value:
            return False
        if re.search(r"\d", value):
            return False
        parts = value.replace(",", " ").split()
        if len(parts) < 2 or len(parts) > 5:
            return False
        return all(re.fullmatch(r"[A-Z][A-Za-z.\-']+", part) or re.fullmatch(r"[A-Z]\.?", part) for part in parts)

    def _looks_like_office_title(self, value: str) -> bool:
        lowered = value.lower()
        return any(
            token in lowered
            for token in [
                "secretary",
                "undersecretary",
                "chief of staff",
                "chief operating officer",
                "assistant secretary",
                "chief legal counsel",
                "chief financial officer",
                "communications director",
                "general counsel",
                "commissioner",
            ]
        )
