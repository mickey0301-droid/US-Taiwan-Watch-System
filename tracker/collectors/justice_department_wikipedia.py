from __future__ import annotations

from datetime import datetime
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


GENERIC_NON_PERSON_PREFIXES = (
    "assistant attorney general",
    "deputy attorney general",
    "associate attorney general",
    "office of ",
    "department of ",
    "united states ",
    "division of ",
)


JUSTICE_ORG_LINKS = [
    {"title": "Deputy Attorney General", "url": "https://en.wikipedia.org/wiki/United_States_Deputy_Attorney_General", "subdepartment": "Office of the Deputy Attorney General"},
    {"title": "Associate Attorney General", "url": "https://en.wikipedia.org/wiki/United_States_Associate_Attorney_General", "subdepartment": "Office of the Associate Attorney General"},
    # TODO: Re-enable Assistant Attorney General once we switch to per-division current office pages.
    # The umbrella Wikipedia page mixes historical officeholder tables and is currently too noisy.
]


class JusticeDepartmentWikipediaCollector(BaseCollector):
    collector_name = "justice_department_wikipedia"
    source_name = "Wikipedia Justice Department organization"
    source_url = "https://en.wikipedia.org/wiki/United_States_Department_of_Justice"
    parser_identity = "wikipedia_justice_department_v1"
    department_name = "Department of Justice"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> list[dict[str, str]]:
        return JUSTICE_ORG_LINKS

    def parse(self, payload: list[dict[str, str]]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in payload:
            if item["title"] == "Assistant Attorney General":
                parsed.extend(self._parse_assistant_attorneys_general(item, seen))
                continue
            record = self._parse_single_position(item)
            if not record:
                continue
            key = (record["person"]["full_name"], record["office"]["office_name"])
            if key in seen:
                continue
            seen.add(key)
            parsed.append(record)
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
                logger.exception("%s failed.", self.collector_name)
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

    def _parse_single_position(self, item: dict[str, str]) -> dict[str, Any] | None:
        page_data = self._fetch_person_from_infobox(item["url"])
        if not page_data or not page_data.get("full_name"):
            return None
        office_title = page_data.get("role_title") or item["title"]
        return self._build_record(item["url"], item["subdepartment"], item["title"], office_title, page_data)

    def _parse_assistant_attorneys_general(self, item: dict[str, str], seen: set[tuple[str, str]]) -> list[dict[str, Any]]:
        try:
            response = httpx.get(
                item["url"],
                timeout=25.0,
                follow_redirects=True,
                trust_env=False,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            response.raise_for_status()
        except Exception:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        parsed: list[dict[str, Any]] = []
        for header in soup.select("#mw-content-text .mw-parser-output h3"):
            header_text = " ".join(header.get_text(" ", strip=True).split()).replace("[edit]", "").strip()
            if not header_text:
                continue
            if header_text.lower() in {"assistant attorney general", "assistant attorneys general"}:
                continue
            next_table = header.find_next("table")
            if not next_table:
                continue
            header_row = [th.get_text(" ", strip=True).lower() for th in next_table.select("tr th")]
            if not any("name" in cell for cell in header_row):
                continue
            for row in next_table.select("tr")[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                row_texts = [" ".join(cell.get_text(" ", strip=True).split()) for cell in cells]
                if not self._looks_like_current_row(header_row, row_texts):
                    continue
                person_anchor = self._pick_person_anchor(cells[0], item["url"])
                if not person_anchor:
                    continue
                full_name = " ".join(person_anchor.get_text(" ", strip=True).split())
                if not full_name:
                    continue
                person_url = urljoin(item["url"], person_anchor["href"].strip())
                page_data = self._fetch_person_page(person_url)
                page_data["full_name"] = full_name
                page_data["person_url"] = person_url
                office_title = f"Assistant Attorney General for {header_text}"
                record = self._build_record(item["url"], item["subdepartment"], header_text, office_title, page_data)
                key = (record["person"]["full_name"], record["office"]["office_name"])
                if key in seen:
                    continue
                seen.add(key)
                parsed.append(record)
        return parsed

    def _looks_like_current_row(self, headers: list[str], row_texts: list[str]) -> bool:
        normalized_headers = [header.lower() for header in headers]
        if any("left office" in header for header in normalized_headers):
            for index, header in enumerate(normalized_headers):
                if "left office" in header:
                    value = row_texts[index] if index < len(row_texts) else ""
                    return value in {"", "â€”", "-", "Incumbent", "incumbent"}
        if any("term ended" in header for header in normalized_headers):
            for index, header in enumerate(normalized_headers):
                if "term ended" in header:
                    value = row_texts[index] if index < len(row_texts) else ""
                    return value in {"", "â€”", "-", "Incumbent", "incumbent"}
        if any("years of service" in header for header in normalized_headers):
            for index, header in enumerate(normalized_headers):
                if "years of service" in header:
                    value = row_texts[index] if index < len(row_texts) else ""
                    return "present" in value.lower() or "incumbent" in value.lower()
        return False

    def _pick_person_anchor(self, cell: Tag, base_url: str) -> Tag | None:
        candidates: list[Tag] = []
        for anchor in cell.find_all("a", href=True):
            href = anchor.get("href", "")
            if "/wiki/" not in href or ":" in href.split("/wiki/")[-1]:
                continue
            label = " ".join(anchor.get_text(" ", strip=True).split())
            if not self._looks_like_person_name(label):
                continue
            candidates.append(anchor)
        return candidates[0] if candidates else None

    def _looks_like_person_name(self, value: str) -> bool:
        normalized = " ".join(value.split()).strip()
        if not normalized or len(normalized) < 5:
            return False
        if "," in normalized:
            return False
        lower_value = normalized.lower()
        if lower_value.startswith(GENERIC_NON_PERSON_PREFIXES):
            return False
        parts = normalized.replace(".", " ").split()
        capitalized_parts = [part for part in parts if part and part[0].isupper()]
        if len(parts) < 2 or len(capitalized_parts) < 2:
            return False
        if any(char.isdigit() for char in normalized):
            return False
        return True

    def _build_record(
        self,
        source_page: str,
        subdepartment_name: str,
        unit_name: str,
        office_title: str,
        page_data: dict[str, Any],
    ) -> dict[str, Any]:
        person_url = page_data.get("person_url") or source_page
        office_name = f"{self.department_name}: {office_title}"
        return {
            "person": {
                "full_name": page_data["full_name"],
                "source_url": person_url,
                "source_type": "wikipedia",
                "seed_source_type": "wikipedia",
                "profile_status": "seeded",
                "portrait_url": page_data.get("portrait_url"),
                "portrait_source_url": person_url if page_data.get("portrait_url") else None,
                "portrait_source_type": "wikipedia" if page_data.get("portrait_url") else None,
                "social_profiles": page_data.get("social_profiles") or {},
                "parser_identity": self.parser_identity,
                "verification_status": "unverified",
                "raw_payload": {
                    "wikipedia_url": person_url,
                    "source_page": source_page,
                    "top_department_name": self.department_name,
                    "subdepartment_name": subdepartment_name,
                    "department_name": self.department_name,
                    "unit_name": unit_name,
                    "office_title": office_title,
                },
            },
            "jurisdiction": {"name": "United States", "type": "country", "code": "US"},
            "office": {
                "office_name": office_name,
                "level": "federal",
                "branch": "executive",
                "chamber": None,
                "source_url": source_page,
                "source_type": "wikipedia",
            },
            "appointment": {
                "role_title": office_name,
                "status": "current",
                "source_url": source_page,
                "source_type": "wikipedia",
                "parser_identity": self.parser_identity,
                "is_current": True,
                "raw_payload": {
                    "top_department_name": self.department_name,
                    "subdepartment_name": subdepartment_name,
                    "department_name": self.department_name,
                    "unit_name": unit_name,
                    "office_title": office_title,
                    "wikipedia_person_url": person_url,
                },
            },
            "aliases": [page_data["full_name"]],
        }

    def _fetch_person_from_infobox(self, url: str) -> dict[str, Any] | None:
        try:
            response = httpx.get(
                url,
                timeout=25.0,
                follow_redirects=True,
                trust_env=False,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            response.raise_for_status()
        except Exception:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        infobox = soup.select_one("table.infobox")
        if not infobox:
            return None

        person_anchor = None
        role_title = None
        for row in infobox.select("tr"):
            header_cell = row.find("th")
            value_cell = row.find("td")
            if not header_cell or not value_cell:
                continue
            header = " ".join(header_cell.get_text(" ", strip=True).split()).lower()
            if header not in {"incumbent", "agency executive", "deputy"}:
                continue
            for anchor in value_cell.find_all("a", href=True):
                href = anchor.get("href", "")
                if "/wiki/" in href and ":" not in href.split("/wiki/")[-1]:
                    person_anchor = anchor
                    break
            if person_anchor:
                role_title = " ".join(value_cell.get_text(" ", strip=True).split()).replace(person_anchor.get_text(" ", strip=True), "", 1).strip(" ,;â€“-")
                break

        if not person_anchor:
            return None

        person_url = urljoin(url, person_anchor["href"].strip())
        page_data = self._fetch_person_page(person_url)
        page_data["full_name"] = " ".join(person_anchor.get_text(" ", strip=True).split())
        page_data["person_url"] = person_url
        page_data["role_title"] = role_title
        return page_data

    def _fetch_person_page(self, person_url: str) -> dict[str, Any]:
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
        portrait_url = None
        infobox = soup.select_one("table.infobox")
        if infobox:
            image = infobox.select_one("img")
            if image and image.get("src"):
                portrait_url = urljoin(person_url, image["src"].strip())
        return {
            "portrait_url": portrait_url,
            "social_profiles": discover_social_profiles(person_url, soup),
        }

