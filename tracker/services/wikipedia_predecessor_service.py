from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import Appointment, Office, Person
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService
from tracker.utils.official_search import build_google_official_bio_search_url, build_google_official_search_url


PREDECESSOR_LABELS = {
    "preceded by",
    "predecessor",
    "predecessor(s)",
}


@dataclass
class PredecessorSeedResult:
    people_scanned: int = 0
    predecessors_found: int = 0
    records_created: int = 0
    records_updated: int = 0
    errors: list[str] = field(default_factory=list)


class WikipediaPredecessorService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.officials_service = OfficialsService(session)

    def seed_from_current_people(self) -> PredecessorSeedResult:
        result = PredecessorSeedResult()
        rows = self.session.execute(
            select(Person, Appointment, Office)
            .join(Appointment, Appointment.person_id == Person.id)
            .join(Office, Office.id == Appointment.office_id)
            .where(Appointment.status == "current")
        ).all()

        seen_pairs: set[tuple[int, str]] = set()
        for person, appointment, office in rows:
            wikipedia_url = self._get_wikipedia_url(person)
            if not wikipedia_url:
                continue
            result.people_scanned += 1
            try:
                predecessors = self._extract_predecessors(wikipedia_url, appointment.role_title, office.office_name)
            except Exception as exc:
                result.errors.append(f"{person.full_name}: {exc}")
                continue

            for predecessor in predecessors:
                pair_key = (office.id, predecessor["full_name"])
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                result.predecessors_found += 1
                try:
                    predecessor_person, created = self.officials_service.upsert_person(
                        {
                            "full_name": predecessor["full_name"],
                            "source_url": predecessor["wikipedia_url"],
                            "source_type": "wikipedia",
                            "seed_source_type": "wikipedia",
                            "profile_status": "seeded",
                            "portrait_url": predecessor.get("portrait_url"),
                            "portrait_source_url": predecessor["wikipedia_url"] if predecessor.get("portrait_url") else None,
                            "portrait_source_type": "wikipedia" if predecessor.get("portrait_url") else None,
                            "parser_identity": "wikipedia_predecessor_v1",
                            "verification_status": "unverified",
                            "raw_payload": {
                                "wikipedia_url": predecessor["wikipedia_url"],
                                "discovered_from_person_id": person.id,
                                "discovered_from_person_name": person.full_name,
                                "discovered_from_office": appointment.role_title,
                                "official_search_urls": {
                                    "official_search": build_google_official_search_url(predecessor["full_name"], appointment.role_title),
                                    "official_bio_search": build_google_official_bio_search_url(predecessor["full_name"], appointment.role_title),
                                },
                                "official_discovery_status": "search_ready",
                            },
                        }
                    )
                except InvalidPersonNameError as exc:
                    result.errors.append(str(exc))
                    continue

                result.records_created += 1 if created else 0
                result.records_updated += 0 if created else 1
                self.officials_service.ensure_alias(
                    predecessor_person.id,
                    predecessor["full_name"],
                    predecessor["wikipedia_url"],
                    "wikipedia",
                )
                if self.officials_service.upsert_appointment(
                    predecessor_person,
                    office,
                    appointment.jurisdiction_id,
                    {
                        "role_title": appointment.role_title,
                        "district": appointment.district,
                        "party": appointment.party,
                        "status": "former",
                        "source_url": predecessor["wikipedia_url"],
                        "source_type": "wikipedia",
                        "parser_identity": "wikipedia_predecessor_v1",
                        "is_current": False,
                        "raw_payload": {
                            "discovered_from_person_id": person.id,
                            "discovered_from_person_name": person.full_name,
                            "discovered_from_office": appointment.role_title,
                        },
                    },
                ):
                    result.records_created += 1

        return result

    def _get_wikipedia_url(self, person: Person) -> str | None:
        raw_payload = person.raw_payload or {}
        wikipedia_url = raw_payload.get("wikipedia_url")
        if wikipedia_url:
            return wikipedia_url
        if person.source_url and "wikipedia.org" in person.source_url.lower():
            return person.source_url
        return None

    def _extract_predecessors(
        self,
        wikipedia_url: str,
        role_title: str | None = None,
        office_name: str | None = None,
    ) -> list[dict[str, Any]]:
        response = httpx.get(
            wikipedia_url,
            timeout=20.0,
            follow_redirects=True,
            trust_env=False,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        infobox = soup.select_one(".infobox")
        if not infobox:
            return []

        target_block = self._select_best_office_block(infobox, role_title, office_name)
        predecessors: list[dict[str, Any]] = []
        for row in target_block:
            header = row.find("th")
            value_cell = row.find("td")
            if not header or not value_cell:
                continue
            label = " ".join(header.get_text(" ", strip=True).lower().split())
            if label not in PREDECESSOR_LABELS:
                continue
            for anchor in value_cell.find_all("a", href=True):
                name = " ".join(anchor.get_text(" ", strip=True).split())
                href = anchor["href"].strip()
                if not name or not href or ":" in href.split("/wiki/")[-1] or "[" in name or len(name.split()) < 2:
                    continue
                absolute_url = urljoin(wikipedia_url, href)
                portrait_url = self._fetch_wikipedia_portrait(absolute_url)
                predecessors.append(
                    {
                        "full_name": name,
                        "wikipedia_url": absolute_url,
                        "portrait_url": portrait_url,
                    }
                )
        return predecessors

    def _select_best_office_block(
        self,
        infobox: BeautifulSoup,
        role_title: str | None,
        office_name: str | None,
    ) -> list[Any]:
        rows = infobox.select("tr")
        if not rows:
            return []

        blocks: list[tuple[str, list[Any]]] = []
        current_header = ""
        current_rows: list[Any] = []
        for row in rows:
            header_cell = row.find(["th", "td"])
            if not header_cell:
                continue
            header_classes = row.get("class") or []
            row_text = " ".join(header_cell.get_text(" ", strip=True).split())
            is_block_header = any("infobox-header" in item for item in header_classes) or (
                row.find("th") is not None and row.find("td") is None and len(row_text) > 0
            )
            if is_block_header:
                if current_rows:
                    blocks.append((current_header, current_rows))
                current_header = row_text
                current_rows = [row]
            else:
                current_rows.append(row)
        if current_rows:
            blocks.append((current_header, current_rows))

        target_text = " ".join(part for part in [office_name, role_title] if part).strip().lower()
        if not target_text:
            return rows

        best_rows = rows
        best_score = 0
        for header_text, block_rows in blocks:
            score = fuzz.partial_ratio(header_text.lower(), target_text)
            if score > best_score:
                best_score = score
                best_rows = block_rows

        return best_rows if best_score >= 55 else rows

    def _fetch_wikipedia_portrait(self, wikipedia_url: str) -> str | None:
        try:
            response = httpx.get(
                wikipedia_url,
                timeout=15.0,
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
        soup = BeautifulSoup(response.text, "lxml")
        meta = soup.find("meta", attrs={"property": "og:image"})
        if meta and meta.get("content"):
            return meta["content"].strip()
        image = soup.select_one(".infobox img")
        if image and image.get("src"):
            src = image["src"].strip()
            return f"https:{src}" if src.startswith("//") else urljoin(wikipedia_url, src)
        return None
