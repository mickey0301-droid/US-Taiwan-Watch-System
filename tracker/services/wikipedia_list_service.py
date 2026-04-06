from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from tracker.models import Tracker
from tracker.services.officials_service import InvalidPersonNameError, OfficialsService
from tracker.services.social_target_service import SocialTargetService
from tracker.services.tracker_service import TrackerService
from tracker.utils.social import discover_social_profiles


@dataclass
class WikipediaImportResult:
    imported_count: int = 0
    tracker_count: int = 0
    skipped_count: int = 0
    names: list[str] | None = None
    validation_log: list[dict[str, str]] | None = None


class WikipediaListService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.officials_service = OfficialsService(session)
        self.tracker_service = TrackerService(session)
        self.social_target_service = SocialTargetService(session)

    def import_list(
        self,
        list_url: str,
        office_name: str,
        role_title: str,
        level: str,
        branch: str | None,
        chamber: str | None,
        jurisdiction_name: str,
        jurisdiction_type: str,
        appointment_status: str,
        auto_create_trackers: bool,
    ) -> WikipediaImportResult:
        response = httpx.get(list_url, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        names_and_urls = self._extract_people(list_url, response.text)
        result = WikipediaImportResult(names=[], validation_log=[])
        usa = self.officials_service.get_or_create_jurisdiction("United States", "country", code="US")
        jurisdiction = self.officials_service.get_or_create_jurisdiction(
            jurisdiction_name,
            jurisdiction_type,
            code=jurisdiction_name,
            parent_id=usa.id if jurisdiction_type != "country" else None,
        )
        office = self.officials_service.get_or_create_office(
            office_name=office_name,
            level=level,
            branch=branch,
            chamber=chamber,
            jurisdiction_id=jurisdiction.id,
            source_url=list_url,
            source_type="wikipedia",
        )

        for full_name, person_url in names_and_urls:
            portrait_url = self._fetch_wikipedia_portrait(person_url)
            social_profiles = self._fetch_wikipedia_social_profiles(person_url)
            try:
                person, created = self.officials_service.upsert_person(
                    {
                        "full_name": full_name,
                        "source_url": person_url,
                        "source_type": "wikipedia",
                        "seed_source_type": "wikipedia",
                        "profile_status": "seeded",
                        "portrait_url": portrait_url,
                        "portrait_source_url": person_url if portrait_url else None,
                        "portrait_source_type": "wikipedia" if portrait_url else None,
                        "social_profiles": social_profiles,
                        "parser_identity": "wikipedia_list_v1",
                        "verification_status": "unverified",
                        "raw_payload": {"list_url": list_url, "wikipedia_url": person_url},
                    }
                )
            except InvalidPersonNameError as exc:
                result.validation_log.append(exc.to_dict())
                result.skipped_count += 1
                continue
            if created:
                result.imported_count += 1
            else:
                result.skipped_count += 1
            result.names.append(full_name)
            self.officials_service.ensure_alias(person.id, full_name, person_url, "wikipedia")
            self.officials_service.upsert_appointment(
                person,
                office,
                jurisdiction.id,
                {
                    "role_title": role_title,
                    "status": appointment_status,
                    "source_url": list_url,
                    "source_type": "wikipedia",
                    "parser_identity": "wikipedia_list_v1",
                    "is_current": appointment_status == "current",
                    "raw_payload": {"list_url": list_url, "person_url": person_url},
                },
            )
            if auto_create_trackers and not person.trackers:
                tracker = self.tracker_service.create_or_update_tracker(
                    tracker_id=None,
                    person_id=person.id,
                    name=f"{full_name} tracker",
                    status="active",
                    include_primary_sources=True,
                    include_media_reports=True,
                    schedule_cron=None,
                    targets=[
                        {
                            "target_type": "media_search_target",
                            "target_name": "Google News Taiwan query",
                            "target_url": self._build_google_news_rss(full_name),
                        }
                    ],
                )
                self.social_target_service.ensure_valid_social_targets_for_tracker(
                    tracker.id,
                    social_profiles,
                    parser_identity="wikipedia_social_discovery_v1",
                )
                result.tracker_count += 1
            elif social_profiles:
                self.social_target_service.ensure_valid_social_targets_for_person(
                    person.id,
                    social_profiles,
                    parser_identity="wikipedia_social_discovery_v1",
                )
        return result

    def _fetch_wikipedia_portrait(self, person_url: str) -> str | None:
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
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        meta = soup.find("meta", attrs={"property": "og:image"})
        if meta and meta.get("content"):
            return meta["content"].strip()
        image = soup.select_one(".infobox img")
        if image and image.get("src"):
            return urljoin(person_url, image["src"].strip())
        return None

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

    def _extract_people(self, base_url: str, html: str) -> list[tuple[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one("#mw-content-text") or soup
        seen: set[str] = set()
        results: list[tuple[str, str]] = []

        for anchor in content.select("table.wikitable a[href], ul li a[href], ol li a[href]"):
            href = anchor.get("href", "").strip()
            name = " ".join(anchor.get_text(" ", strip=True).split())
            if not href or not name:
                continue
            if name.lower() in {"district", "party", "office", "state", "senator", "representative"}:
                continue
            if href.startswith("#") or ":" in href.split("/wiki/")[-1]:
                continue
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            if "wikipedia.org" not in parsed.netloc:
                continue
            if name in seen:
                continue
            if len(name.split()) < 2:
                continue
            seen.add(name)
            results.append((name, absolute))
        return results

    def _build_google_news_rss(self, full_name: str) -> str:
        query = quote(f'"{full_name}" Taiwan')
        return f"https://news.google.com/rss/search?q={query}"

