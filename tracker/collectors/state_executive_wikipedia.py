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
from tracker.utils.text import compact_whitespace


logger = get_logger(__name__)

STATE_NAMES = {
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
}


class StateExecutiveWikipediaCollector(BaseCollector):
    collector_name = "state_executives_wikipedia"
    source_name = "Wikipedia statewide elected officials"
    parser_identity = "wikipedia_state_executives_v1"

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch(self) -> dict[str, Any]:
        return get_source_registry().get("sources", {}).get("state_executives_wikipedia", {})

    def parse(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not payload.get("source_url"):
            return []
        html = self._fetch_html(payload["source_url"])
        return self._parse_page(payload["source_url"], html)

    def sync(self) -> CollectorRunResult:
        result = CollectorRunResult(job_name=self.collector_name, source_name=self.source_name, started_at=datetime.utcnow())
        with session_scope() as session:
            sync_run = SyncRun(job_name=self.collector_name, job_type="collector", source_name=self.source_name)
            session.add(sync_run)
            session.flush()
            service: OfficialsService | None = None
            seen_keys: set[tuple[int, int, int | None, str]] = set()
            try:
                parsed = self.parse(self.fetch())
                service = OfficialsService(session)
                usa = service.get_or_create_jurisdiction("United States", "country", code="US")
                for record in parsed:
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
                    created_appointment = service.upsert_appointment(person, office, state.id, record["appointment"])
                    if created_appointment:
                        result.records_created += 1
                    seen_keys.add((person.id, office.id, state.id, record["appointment"]["role_title"]))
                result.records_deactivated = service.reconcile_current_appointments(self.parser_identity, seen_keys)
                sync_run.status = "success"
            except Exception as exc:
                logger.exception("State executives wikipedia collector failed.")
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

    def _parse_page(self, page_url: str, html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        records: list[dict[str, Any]] = []
        for heading in soup.find_all(["h2", "h3"]):
            state_name = self._extract_state_name(heading)
            if not state_name:
                continue
            table = heading.find_next("table", class_="wikitable")
            if table is None:
                continue
            records.extend(self._parse_state_table(state_name, page_url, table))
        return records

    def _extract_state_name(self, heading: Tag) -> str | None:
        headline = heading.find(class_="mw-headline")
        text = compact_whitespace((headline or heading).get_text(" ", strip=True))
        return text if text in STATE_NAMES else None

    def _parse_state_table(self, state: str, page_url: str, table: Tag) -> list[dict[str, Any]]:
        rows = table.find_all("tr")
        if not rows:
            return []
        headers = [compact_whitespace(cell.get_text(" ", strip=True)).lower() for cell in rows[0].find_all(["th", "td"])]
        if not any("office" in header for header in headers):
            return []

        office_index = next((index for index, header in enumerate(headers) if "office" in header), 0)
        officer_index = next((index for index, header in enumerate(headers) if any(token in header for token in ["officer", "official", "holder", "name", "member"])), 1)

        records: list[dict[str, Any]] = []
        current_office_label: str | None = None
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= officer_index:
                continue
            office_text = compact_whitespace(cells[office_index].get_text(" ", strip=True))
            if office_text:
                current_office_label = office_text
            office_label = current_office_label
            if not office_label or not self._should_include_office(office_label):
                continue

            anchor = self._select_person_anchor(cells[officer_index:])
            if not anchor:
                continue

            full_name = self._person_name_from_anchor(anchor)
            if not full_name:
                continue

            person_url = urljoin(page_url, anchor["href"])
            page_data = self._fetch_person_page(person_url)
            role_title = self._normalize_office_title(office_label)
            subdepartment_name = self._subdepartment_name(role_title)
            aliases = self._aliases_for_role(full_name, role_title)
            raw_payload = {
                "wikipedia_url": person_url,
                "state": state,
                "office_title": role_title,
                "department_name": state,
                "top_department_name": state,
                "subdepartment_name": subdepartment_name,
                "unit_name": role_title,
            }
            records.append(
                {
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
                        "raw_payload": raw_payload,
                    },
                    "jurisdiction": {"name": state, "type": "state", "code": state},
                    "office": {
                        "office_name": role_title,
                        "level": "state",
                        "branch": "executive",
                        "chamber": None,
                        "source_url": page_url,
                        "source_type": "seed",
                    },
                    "appointment": {
                        "role_title": role_title,
                        "status": "current",
                        "source_url": page_url,
                        "source_type": "seed",
                        "parser_identity": self.parser_identity,
                        "is_current": True,
                        "raw_payload": raw_payload,
                    },
                    "aliases": aliases,
                }
            )
        return records

    def _should_include_office(self, office_label: str) -> bool:
        normalized = compact_whitespace(office_label).lower()
        if normalized in {"governor", "acting governor", "vacant"}:
            return False
        if any(token in normalized for token in ["vacant", "judicial", "supreme court", "chief justice"]):
            return False
        return any(
            token in normalized
            for token in [
                "lieutenant governor",
                "secretary of state",
                "attorney general",
                "treasurer",
                "controller",
                "comptroller",
                "auditor",
                "superintendent",
                "commissioner",
                "public instruction",
                "agriculture",
                "insurance",
                "labor",
                "education",
                "lands",
            ]
        )

    def _normalize_office_title(self, office_label: str) -> str:
        cleaned = compact_whitespace(re.sub(r"\[[^\]]+\]", "", office_label))
        cleaned = re.sub(r"\s+\(.*?\)$", "", cleaned).strip()
        return cleaned

    def _subdepartment_name(self, office_title: str) -> str:
        lowered = office_title.lower()
        if "lieutenant governor" in lowered:
            return "Lieutenant Governor"
        if "secretary of state" in lowered:
            return "Secretary of State"
        if "attorney general" in lowered:
            return "Attorney General"
        if "treasurer" in lowered:
            return "Treasurer"
        if "controller" in lowered or "comptroller" in lowered or "auditor" in lowered:
            return "Fiscal Officers"
        if "education" in lowered or "public instruction" in lowered or "superintendent" in lowered:
            return "Education"
        if "insurance" in lowered:
            return "Insurance"
        if "agriculture" in lowered:
            return "Agriculture"
        if "labor" in lowered:
            return "Labor"
        return "State Executive"

    def _aliases_for_role(self, full_name: str, role_title: str) -> list[str]:
        lowered = role_title.lower()
        aliases = [f"{role_title} {full_name}"]
        if "attorney general" in lowered:
            aliases.append(f"AG {full_name}")
        if "lieutenant governor" in lowered:
            aliases.append(f"Lt. Gov. {full_name}")
        if "secretary of state" in lowered:
            aliases.append(f"Sec. of State {full_name}")
        return aliases

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
        if lowered in {"vacant", "democratic", "republican", "independent"}:
            return False
        if any(token in lowered for token in ["party", "election", "legislature", "government of ", "state of "]):
            return False
        if re.search(r"\d", value):
            return False
        cleaned = re.sub(r"\s+", " ", value).strip(" ,")
        parts = [part for part in re.split(r"[\s,]+", cleaned) if part]
        if len(parts) < 2 or len(parts) > 5:
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
        soup = BeautifulSoup(html, "lxml")
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
