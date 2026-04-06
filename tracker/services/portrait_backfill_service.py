from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import Person, Tracker, TrackerTarget
from tracker.services.officials_service import OfficialsService
from tracker.utils.web import absolute_url


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class PortraitBackfillResult:
    people_scanned: int = 0
    portraits_updated: int = 0
    source_counts: dict[str, int] = field(default_factory=lambda: {"official": 0, "social": 0, "wikipedia": 0})
    errors: list[str] = field(default_factory=list)


class PortraitBackfillService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.officials_service = OfficialsService(session)

    def backfill_all(self) -> PortraitBackfillResult:
        result = PortraitBackfillResult()
        people = self.session.execute(select(Person).order_by(Person.id.asc())).scalars().all()
        for person in people:
            result.people_scanned += 1
            try:
                updated_source = self.backfill_person(person)
                if updated_source:
                    result.portraits_updated += 1
                    result.source_counts[updated_source] = result.source_counts.get(updated_source, 0) + 1
            except Exception as exc:
                result.errors.append(f"{person.id}:{person.full_name}: {exc}")
        return result

    def backfill_person(self, person: Person) -> str | None:
        official_urls = self._official_urls(person)
        for url in official_urls:
            portrait_url = self._discover_portrait_from_page(url)
            if portrait_url and self.officials_service.set_best_portrait(person, portrait_url, url, "official"):
                return "official"

        social_urls = self._social_urls(person)
        for url in social_urls:
            portrait_url = self._discover_social_portrait(url)
            if portrait_url and self.officials_service.set_best_portrait(person, portrait_url, url, "social"):
                return "social"

        wikipedia_url = self._wikipedia_url(person)
        if wikipedia_url:
            portrait_url = self._discover_wikipedia_portrait(wikipedia_url)
            if portrait_url and self.officials_service.set_best_portrait(person, portrait_url, wikipedia_url, "wikipedia"):
                return "wikipedia"

        return None

    def _official_urls(self, person: Person) -> list[str]:
        urls: list[str] = []
        for url in [person.canonical_official_url, person.source_url]:
            if url and url not in urls and "wikipedia.org" not in url.lower():
                urls.append(url)

        tracker_ids = self.session.execute(select(Tracker.id).where(Tracker.person_id == person.id)).scalars().all()
        if tracker_ids:
            targets = self.session.execute(
                select(TrackerTarget.target_url).where(
                    TrackerTarget.tracker_id.in_(tracker_ids),
                    TrackerTarget.is_active.is_(True),
                    TrackerTarget.target_type.in_(["official_website", "press_release_page", "hearing_page", "activity_page"]),
                )
            ).scalars().all()
            for target_url in targets:
                if target_url and target_url not in urls:
                    urls.append(target_url)
        return urls

    def _social_urls(self, person: Person) -> list[str]:
        urls: list[str] = []
        for url in (person.social_profiles or {}).values():
            if url and url not in urls:
                urls.append(url)

        tracker_ids = self.session.execute(select(Tracker.id).where(Tracker.person_id == person.id)).scalars().all()
        if tracker_ids:
            targets = self.session.execute(
                select(TrackerTarget.target_url).where(
                    TrackerTarget.tracker_id.in_(tracker_ids),
                    TrackerTarget.is_active.is_(True),
                    TrackerTarget.target_type == "social_page",
                )
            ).scalars().all()
            for target_url in targets:
                if target_url and target_url not in urls:
                    urls.append(target_url)
        return urls

    def _wikipedia_url(self, person: Person) -> str | None:
        raw_payload = person.raw_payload or {}
        return raw_payload.get("wikipedia_url") or (person.source_url if person.source_url and "wikipedia.org" in person.source_url.lower() else None)

    def _fetch_soup(self, url: str) -> BeautifulSoup:
        response = httpx.get(
            url,
            timeout=20.0,
            follow_redirects=True,
            trust_env=False,
            headers=DEFAULT_HEADERS,
        )
        response.raise_for_status()
        return BeautifulSoup(response.text, "lxml")

    def _discover_portrait_from_page(self, url: str) -> str | None:
        try:
            soup = self._fetch_soup(url)
        except Exception:
            return None
        meta = soup.find("meta", attrs={"property": "og:image"}) or soup.find("meta", attrs={"name": "og:image"})
        if meta and meta.get("content"):
            return absolute_url(url, meta["content"].strip())
        selectors = [
            "img[alt*='official' i]",
            "img[alt*='portrait' i]",
            "img[alt*='headshot' i]",
            "img[src*='headshot']",
            "img[src*='portrait']",
            "main img",
            "article img",
        ]
        for selector in selectors:
            image = soup.select_one(selector)
            if image and image.get("src"):
                return absolute_url(url, image["src"].strip())
        return None

    def _discover_social_portrait(self, url: str) -> str | None:
        try:
            soup = self._fetch_soup(url)
        except Exception:
            return None
        for attrs in [{"property": "og:image"}, {"name": "og:image"}, {"name": "twitter:image"}, {"property": "twitter:image"}]:
            meta = soup.find("meta", attrs=attrs)
            if meta and meta.get("content"):
                return absolute_url(url, meta["content"].strip())
        for selector in ["img[alt*='profile' i]", "img[src*='profile']", "img[src*='avatar']", "img"]:
            image = soup.select_one(selector)
            if image and image.get("src"):
                src = image["src"].strip()
                if src:
                    return absolute_url(url, src)
        return None

    def _discover_wikipedia_portrait(self, url: str) -> str | None:
        try:
            soup = self._fetch_soup(url)
        except Exception:
            return None
        meta = soup.find("meta", attrs={"property": "og:image"})
        if meta and meta.get("content"):
            return meta["content"].strip()
        image = soup.select_one(".infobox img")
        if image and image.get("src"):
            return absolute_url(url, image["src"].strip())
        return None
