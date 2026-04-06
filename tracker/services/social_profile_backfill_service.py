from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import Appointment
from tracker.models import Office
from tracker.models import Person
from tracker.services.officials_service import OfficialsService
from tracker.services.social_target_service import SocialTargetService
from tracker.utils.social import discover_social_profiles
from tracker.utils.social import normalize_social_profiles
from tracker.utils.web import absolute_url


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class SocialBackfillResult:
    people_scanned: int = 0
    people_updated: int = 0
    x_profiles_added: int = 0
    social_targets_added: int = 0
    errors: list[str] | None = None


class SocialProfileBackfillService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.officials_service = OfficialsService(session)
        self.social_target_service = SocialTargetService(session)

    def backfill_x_profiles(self, limit: int | None = None, current_federal_only: bool = False) -> SocialBackfillResult:
        people = self._people_to_scan(limit=limit, current_federal_only=current_federal_only)
        result = SocialBackfillResult(people_scanned=len(people), errors=[])

        for person in people:
            try:
                discovered = self._discover_profiles_for_person(person)
            except Exception as exc:
                result.errors.append(f"{person.full_name}: {exc}")
                continue
            normalized = normalize_social_profiles(discovered)
            if not normalized:
                continue

            merged = dict(person.social_profiles or {})
            before_x = merged.get("x")
            merged.update(normalized)
            merged = normalize_social_profiles(merged)
            if merged != (person.social_profiles or {}):
                person.social_profiles = merged
                person.last_seen_at = datetime.utcnow()
                result.people_updated += 1
            if not before_x and merged.get("x"):
                result.x_profiles_added += 1
            result.social_targets_added += self.social_target_service.ensure_valid_social_targets_for_person(
                person.id,
                {"x": merged["x"]} if merged.get("x") else {},
                parser_identity="social_profile_backfill_x_v1",
            )
        return result

    def _people_to_scan(self, limit: int | None = None, current_federal_only: bool = False) -> list[Person]:
        if current_federal_only:
            stmt = (
                select(Person)
                .join(Appointment, Appointment.person_id == Person.id)
                .join(Office, Office.id == Appointment.office_id)
                .where(
                    Appointment.status == "current",
                    Office.level == "federal",
                    Person.canonical_official_url.is_not(None),
                )
                .order_by(Person.last_seen_at.desc(), Person.id.asc())
            )
            people = self.session.execute(stmt).scalars().all()
            deduped: list[Person] = []
            seen_ids: set[int] = set()
            for person in people:
                if person.id in seen_ids:
                    continue
                seen_ids.add(person.id)
                deduped.append(person)
            people = deduped
        else:
            stmt = select(Person).order_by(Person.last_seen_at.desc(), Person.id.asc())
            people = self.session.execute(stmt).scalars().all()
        filtered = [person for person in people if not normalize_social_profiles(person.social_profiles).get("x")]
        filtered.sort(key=self._priority_key)
        if limit:
            filtered = filtered[:limit]
        return filtered

    def _discover_profiles_for_person(self, person: Person) -> dict[str, str]:
        merged: dict[str, str] = {}
        for url in self._candidate_urls(person):
            profiles = self._discover_from_url(url)
            if profiles:
                merged.update(profiles)
                normalized = self._filter_person_level_profiles(person, normalize_social_profiles(merged))
                if normalized.get("x"):
                    return normalized
        return self._filter_person_level_profiles(person, normalize_social_profiles(merged))

    def _candidate_urls(self, person: Person) -> list[str]:
        urls: list[str] = []
        for candidate in [person.canonical_official_url, person.source_url, (person.raw_payload or {}).get("wikipedia_url")]:
            if candidate and candidate not in urls:
                urls.append(candidate)
        return urls

    def _discover_from_url(self, url: str) -> dict[str, str]:
        response = httpx.get(
            url,
            timeout=20.0,
            follow_redirects=True,
            trust_env=False,
            headers=DEFAULT_HEADERS,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        discovered = discover_social_profiles(str(response.url), soup)
        if discovered.get("x"):
            return discovered

        for follow_url in self._discover_follow_urls(str(response.url), soup):
            try:
                follow_response = httpx.get(
                    follow_url,
                    timeout=15.0,
                    follow_redirects=True,
                    trust_env=False,
                    headers=DEFAULT_HEADERS,
                )
                follow_response.raise_for_status()
            except Exception:
                continue
            follow_soup = BeautifulSoup(follow_response.text, "html.parser")
            nested = discover_social_profiles(str(follow_response.url), follow_soup)
            if nested:
                discovered.update(nested)
            if discovered.get("x"):
                break
        return discovered

    def _discover_follow_urls(self, base_url: str, soup: BeautifulSoup) -> list[str]:
        follow_urls: list[str] = []
        keywords = ["contact", "connect", "about", "bio", "office", "media", "press", "meet"]
        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            text = " ".join(anchor.get_text(" ", strip=True).lower().split())
            if not href:
                continue
            absolute = absolute_url(base_url, href)
            absolute_lower = absolute.lower()
            if not any(keyword in text or keyword in absolute_lower for keyword in keywords):
                continue
            if absolute in follow_urls:
                continue
            follow_urls.append(absolute)
            if len(follow_urls) >= 8:
                break
        return follow_urls

    def _priority_key(self, person: Person) -> tuple[int, str, int]:
        official_url = (person.canonical_official_url or "").lower()
        source_url = (person.source_url or "").lower()
        if "house.gov" in official_url:
            return (0, official_url, person.id)
        if "house.gov" in source_url:
            return (1, source_url, person.id)
        if official_url:
            return (2, official_url, person.id)
        if source_url and "wikipedia.org" not in source_url:
            return (3, source_url, person.id)
        return (4, source_url, person.id)

    def _filter_person_level_profiles(self, person: Person, profiles: dict[str, str]) -> dict[str, str]:
        filtered: dict[str, str] = {}
        for platform, url in profiles.items():
            if platform == "x" and not self._is_person_specific_x_profile(person, url):
                continue
            filtered[platform] = url
        return filtered

    def _is_person_specific_x_profile(self, person: Person, url: str) -> bool:
        handle = (urlparse(url).path or "").strip("/").lower()
        if not handle:
            return False
        if handle in {"whitehouse", "potus", "vp", "flotus", "senate", "housegop", "housedems", "congressdotgov"}:
            return False

        others = self.session.execute(select(Person).where(Person.id != person.id, Person.social_profiles.is_not(None))).scalars().all()
        for other in others:
            other_profiles = normalize_social_profiles(other.social_profiles)
            if other_profiles.get("x") == url:
                return False
        return True

