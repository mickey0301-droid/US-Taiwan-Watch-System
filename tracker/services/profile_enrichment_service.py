from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from tracker.models import Person
from tracker.services.officials_service import OfficialsService
from tracker.services.social_target_service import SocialTargetService
from tracker.utils.official_search import build_google_official_bio_search_url, build_google_official_search_url
from tracker.utils.social import discover_social_profiles
from tracker.utils.text import compact_whitespace
from tracker.utils.wikipedia_links import build_wikipedia_search_url


MONTH_DATE_PATTERNS = [
    "%B %d, %Y",
    "%b. %d, %Y",
    "%b %d, %Y",
]


@dataclass
class ProfileEnrichmentResult:
    person_id: int
    full_name: str
    updated_fields: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ProfileEnrichmentService:
    """Fill background profile fields from official pages, Congress.gov, GovTrack, and Wikipedia."""

    def __init__(self, officials_service: OfficialsService) -> None:
        self.officials_service = officials_service
        self.social_target_service = SocialTargetService(officials_service.session)

    def enrich_person(self, person: Person) -> ProfileEnrichmentResult:
        result = ProfileEnrichmentResult(person_id=person.id, full_name=person.full_name)
        merged_profile: dict[str, Any] = {}
        field_sources: dict[str, dict[str, str]] = {}

        raw_profile, raw_sources = self._extract_from_raw_payload(person)
        self._merge_profile(merged_profile, field_sources, raw_profile, raw_sources)

        candidate_urls = self._candidate_urls(person)
        self._store_background_search_urls(person, candidate_urls)
        for source_key in ("congress", "official", "govtrack", "wikipedia"):
            url = candidate_urls.get(source_key)
            if not url:
                continue
            try:
                if source_key == "wikipedia":
                    profile, sources = self._parse_wikipedia_profile(url)
                elif source_key == "govtrack":
                    profile, sources, resolved_url = self._parse_govtrack_profile(url, person.full_name)
                    if resolved_url:
                        candidate_urls["govtrack"] = resolved_url
                        url = resolved_url
                elif source_key == "official":
                    profile, sources = self._parse_generic_official_profile(url)
                else:
                    profile, sources = self._parse_congress_profile(url)
                self._merge_profile(merged_profile, field_sources, profile, sources)
                result.source_urls.append(url)
            except Exception as exc:
                result.errors.append(f"{source_key}: {exc}")

        result.updated_fields = self.officials_service.enrich_person_background(person, merged_profile, field_sources)
        social_profiles = merged_profile.get("social_profiles")
        self.officials_service.enrich_person_profile(person=person, social_profiles=social_profiles)
        if candidate_urls.get("wikipedia") and social_profiles:
            self.social_target_service.ensure_valid_social_targets_for_person(
                person.id,
                social_profiles,
                parser_identity="wikipedia_social_discovery_v1",
            )
        return result

    def _merge_profile(
        self,
        merged_profile: dict[str, Any],
        merged_sources: dict[str, dict[str, str]],
        profile: dict[str, Any],
        sources: dict[str, dict[str, str]],
    ) -> None:
        for field_name, value in profile.items():
            if value in (None, "", []):
                continue
            current = merged_profile.get(field_name)
            if current in (None, "", []):
                merged_profile[field_name] = value
                if field_name in sources:
                    merged_sources[field_name] = sources[field_name]

    def _candidate_urls(self, person: Person) -> dict[str, str]:
        urls: dict[str, str] = {}
        raw_payload = person.raw_payload or {}
        secondary = raw_payload.get("secondary_sources") or {}
        official_search_urls = raw_payload.get("official_search_urls") or {}

        if person.canonical_official_url and "congress.gov" in person.canonical_official_url.lower():
            urls["congress"] = person.canonical_official_url
        elif person.source_url and "congress.gov" in person.source_url.lower():
            urls["congress"] = person.source_url

        if person.canonical_official_url and "wikipedia.org" not in person.canonical_official_url.lower():
            urls["official"] = person.canonical_official_url
        elif person.source_url and self._looks_like_official_profile_url(person.source_url):
            urls["official"] = person.source_url
        elif isinstance(raw_payload.get("official_profile_url"), str):
            urls["official"] = raw_payload["official_profile_url"]
        elif isinstance(raw_payload.get("official_person_url"), str):
            urls["official"] = raw_payload["official_person_url"]

        govtrack_url = secondary.get("govtrack_person_url") or secondary.get("govtrack_search_url")
        if govtrack_url:
            urls["govtrack"] = govtrack_url

        wikipedia_url = raw_payload.get("wikipedia_url")
        if wikipedia_url:
            urls["wikipedia"] = wikipedia_url
        elif person.source_url and "wikipedia.org" in person.source_url.lower():
            urls["wikipedia"] = person.source_url

        if not urls.get("official"):
            department_page = official_search_urls.get("department_page_url") or raw_payload.get("department_page_url")
            if isinstance(department_page, str) and self._looks_like_official_profile_url(department_page):
                urls["official"] = department_page
            whitehouse_page = official_search_urls.get("whitehouse_page_url") or raw_payload.get("whitehouse_page_url")
            if not urls.get("official") and isinstance(whitehouse_page, str) and self._looks_like_official_profile_url(whitehouse_page):
                urls["official"] = whitehouse_page

        return urls

    def _store_background_search_urls(self, person: Person, candidate_urls: dict[str, str]) -> None:
        office_name = self._person_office_name(person)
        raw_payload = dict(person.raw_payload or {})
        official_search_urls = dict(raw_payload.get("official_search_urls") or {})
        background_search_urls = dict(raw_payload.get("background_search_urls") or {})
        background_search_urls.setdefault("google_official_search", build_google_official_search_url(person.full_name, office_name))
        background_search_urls.setdefault("google_official_bio_search", build_google_official_bio_search_url(person.full_name, office_name))
        background_search_urls.setdefault("wikipedia_search", build_wikipedia_search_url(person.full_name, office_name))
        if candidate_urls.get("wikipedia"):
            background_search_urls["wikipedia_page"] = candidate_urls["wikipedia"]
        if candidate_urls.get("official"):
            background_search_urls["official_page"] = candidate_urls["official"]
        if isinstance(official_search_urls.get("whitehouse_search"), str):
            background_search_urls.setdefault("whitehouse_search", official_search_urls["whitehouse_search"])
        if isinstance(official_search_urls.get("department_search"), str):
            background_search_urls.setdefault("department_search", official_search_urls["department_search"])
        raw_payload["background_search_urls"] = background_search_urls
        person.raw_payload = raw_payload

    def _fetch_soup(self, url: str) -> BeautifulSoup:
        response = httpx.get(
            url,
            timeout=20.0,
            follow_redirects=True,
            trust_env=False,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        return BeautifulSoup(response.text, "lxml")

    def _extract_from_raw_payload(self, person: Person) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
        payload = person.raw_payload or {}
        profile: dict[str, Any] = {}
        sources: dict[str, dict[str, str]] = {}
        source_url = person.canonical_official_url or person.source_url or ""
        source_meta = {"source_url": source_url, "source_type": person.source_type or "official_api"}

        birth_value = payload.get("birthDate") or payload.get("birth_date")
        birth_year = payload.get("birthYear") or payload.get("birth_year")
        parsed_birth = self._parse_birth_date(birth_value)
        if not parsed_birth and birth_year:
            try:
                parsed_birth = date(int(birth_year), 1, 1)
            except (TypeError, ValueError):
                parsed_birth = None
        if parsed_birth:
            profile["date_of_birth"] = parsed_birth
            sources["date_of_birth"] = source_meta

        terms = payload.get("terms", {}).get("item", []) if isinstance(payload.get("terms"), dict) else []
        if terms:
            bullets: list[str] = []
            for term in terms[-8:]:
                chamber = term.get("chamber")
                congress = term.get("congress")
                start_year = term.get("startYear")
                end_year = term.get("endYear")
                state = term.get("stateCode") or term.get("state")
                district = term.get("district")
                district_text = f", district {district}" if district else ""
                years = f"{start_year}-{end_year}" if start_year or end_year else ""
                pieces = [piece for piece in [years, chamber, state] if piece]
                line = ", ".join(pieces) + district_text
                if congress:
                    line = f"{line} (Congress {congress})".strip()
                if line.strip():
                    bullets.append(line.strip(", "))
            if bullets:
                profile["career_history"] = "\n".join(f"- {item}" for item in bullets)
                sources["career_history"] = source_meta

        return profile, sources

    def _parse_wikipedia_profile(self, url: str) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
        soup = self._fetch_soup(url)
        profile: dict[str, Any] = {}
        sources: dict[str, dict[str, str]] = {}
        source_meta = {"source_url": url, "source_type": "wikipedia"}

        infobox = soup.select_one(".infobox")
        if infobox:
            born_text = self._extract_infobox_value(infobox, ["Born"])
            birth_date = None
            bday = infobox.select_one(".bday")
            if bday:
                birth_date = self._parse_birth_date(compact_whitespace(bday.get_text(" ", strip=True)))
            if not birth_date and born_text:
                birth_date = self._parse_birth_date(born_text)
            full_name = self._extract_full_name_from_born_text(born_text)
            if full_name:
                profile["full_name_display"] = full_name
                sources["full_name_display"] = source_meta
            if birth_date:
                profile["date_of_birth"] = birth_date
                sources["date_of_birth"] = source_meta
            if born_text:
                place = self._extract_place_of_birth(born_text)
                if place:
                    profile["place_of_birth"] = place
                    sources["place_of_birth"] = source_meta

            education = self._extract_infobox_value(infobox, ["Education", "Alma mater"])
            if education:
                profile["education"] = education
                sources["education"] = source_meta

            experience_bits = []
            for label in ["Previous offices", "Profession", "Occupation"]:
                value = self._extract_infobox_value(infobox, [label])
                if value:
                    if label == "Previous offices":
                        experience_bits.extend(self._split_experience_items(value))
                    else:
                        experience_bits.extend(self._split_experience_items(f"{label}: {value}"))
            if experience_bits:
                profile["career_history"] = self._as_bulleted_text(experience_bits)
                sources["career_history"] = source_meta

        social_profiles = discover_social_profiles(url, soup)
        if social_profiles:
            profile["social_profiles"] = social_profiles

        if "career_history" not in profile:
            lead = self._extract_lead_paragraphs(soup)
            if lead:
                profile["career_history"] = self._as_bulleted_text(self._extract_experience_bullets_from_text(lead))
                sources["career_history"] = source_meta

        return profile, sources

    def _parse_govtrack_profile(
        self,
        url: str,
        person_name: str,
    ) -> tuple[dict[str, Any], dict[str, dict[str, str]], str | None]:
        soup = self._fetch_soup(url)
        resolved_url = url
        if "/congress/members/" not in url:
            candidate = self._resolve_govtrack_member_page(soup, person_name)
            if candidate:
                resolved_url = candidate
                soup = self._fetch_soup(candidate)

        profile: dict[str, Any] = {}
        sources: dict[str, dict[str, str]] = {}
        source_meta = {"source_url": resolved_url, "source_type": "govtrack"}
        page_text = compact_whitespace(soup.get_text(" ", strip=True))

        birth_match = re.search(r"\bBorn\s+([A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4})", page_text)
        if birth_match:
            birth_date = self._parse_birth_date(birth_match.group(1))
            if birth_date:
                profile["date_of_birth"] = birth_date
                sources["date_of_birth"] = source_meta

        lead = self._extract_lead_paragraphs(soup)
        if lead:
            profile["career_history"] = self._as_bulleted_text(self._extract_experience_bullets_from_text(lead))
            sources["career_history"] = source_meta

        education = self._extract_definition_value(soup, ["Education", "Alma mater"])
        if education:
            profile["education"] = education
            sources["education"] = source_meta

        social_profiles = discover_social_profiles(resolved_url, soup)
        if social_profiles:
            profile["social_profiles"] = social_profiles

        return profile, sources, resolved_url

    def _parse_congress_profile(self, url: str) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
        soup = self._fetch_soup(url)
        profile: dict[str, Any] = {}
        sources: dict[str, dict[str, str]] = {}
        source_meta = {"source_url": url, "source_type": "official"}

        birth_value = self._extract_definition_value(soup, ["Born", "Date of Birth", "Birth Date"])
        if birth_value:
            birth_date = self._parse_birth_date(birth_value)
            if birth_date:
                profile["date_of_birth"] = birth_date
                sources["date_of_birth"] = source_meta
            place = self._extract_place_of_birth(birth_value)
            if place:
                profile["place_of_birth"] = place
                sources["place_of_birth"] = source_meta

        for label in ["Place of Birth", "Born in"]:
            value = self._extract_definition_value(soup, [label])
            if value and not profile.get("place_of_birth"):
                profile["place_of_birth"] = value
                sources["place_of_birth"] = source_meta

        education = self._extract_definition_value(soup, ["Education", "Alma mater"])
        if education:
            profile["education"] = education
            sources["education"] = source_meta

        social_profiles = discover_social_profiles(url, soup)
        if social_profiles:
            profile["social_profiles"] = social_profiles

        lead = self._extract_lead_paragraphs(soup)
        if lead:
            profile["career_history"] = self._as_bulleted_text(self._extract_experience_bullets_from_text(lead))
            sources["career_history"] = source_meta

        return profile, sources

    def _parse_generic_official_profile(self, url: str) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
        soup = self._fetch_soup(url)
        profile: dict[str, Any] = {}
        sources: dict[str, dict[str, str]] = {}
        source_meta = {"source_url": url, "source_type": "official"}

        birth_value = self._extract_definition_value(
            soup,
            ["Born", "Date of Birth", "Birth Date", "Birthday"],
        )
        if birth_value:
            birth_date = self._parse_birth_date(birth_value)
            if birth_date:
                profile["date_of_birth"] = birth_date
                sources["date_of_birth"] = source_meta
            place = self._extract_place_of_birth(birth_value)
            if place:
                profile["place_of_birth"] = place
                sources["place_of_birth"] = source_meta

        for label in ["Place of Birth", "Born in", "Hometown", "Home town"]:
            value = self._extract_definition_value(soup, [label])
            if value and not profile.get("place_of_birth"):
                profile["place_of_birth"] = value
                sources["place_of_birth"] = source_meta

        education = self._extract_definition_value(
            soup,
            ["Education", "Alma mater", "Academic Degrees", "Degrees", "Schools attended"],
        )
        if education:
            profile["education"] = education
            sources["education"] = source_meta

        career_parts: list[str] = []
        for label in [
            "Biography",
            "Background",
            "Professional Experience",
            "Experience",
            "Career",
            "Previous Experience",
            "Previous offices",
        ]:
            value = self._extract_definition_value(soup, [label])
            if value:
                career_parts.append(value)
        lead = self._extract_lead_paragraphs(soup)
        if lead:
            career_parts.append(lead)
        if career_parts:
            deduped_parts: list[str] = []
            seen_parts: set[str] = set()
            for part in career_parts:
                normalized_part = compact_whitespace(part)
                if normalized_part and normalized_part not in seen_parts:
                    deduped_parts.append(normalized_part)
                    seen_parts.add(normalized_part)
            if deduped_parts:
                bullets: list[str] = []
                for part in deduped_parts[:2]:
                    bullets.extend(self._extract_experience_bullets_from_text(part))
                profile["career_history"] = self._as_bulleted_text(bullets)
                sources["career_history"] = source_meta

        social_profiles = discover_social_profiles(url, soup)
        if social_profiles:
            profile["social_profiles"] = social_profiles

        return profile, sources

    def _resolve_govtrack_member_page(self, soup: BeautifulSoup, person_name: str) -> str | None:
        best_url = None
        best_score = 0
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if "/congress/members/" not in href:
                continue
            anchor_text = compact_whitespace(anchor.get_text(" ", strip=True))
            if not anchor_text:
                continue
            score = fuzz.token_sort_ratio(anchor_text.lower(), person_name.lower())
            if score > best_score:
                best_score = score
                best_url = urljoin("https://www.govtrack.us/", href)
        return best_url if best_score >= 75 else None

    def _extract_infobox_value(self, infobox: BeautifulSoup, labels: list[str]) -> str | None:
        normalized_labels = {label.lower() for label in labels}
        for row in infobox.select("tr"):
            header = row.find(["th", "td"])
            if not header:
                continue
            label = compact_whitespace(header.get_text(" ", strip=True)).lower()
            if label not in normalized_labels:
                continue
            value_cell = row.find("td")
            if not value_cell:
                continue
            value = compact_whitespace(value_cell.get_text(" ", strip=True))
            if value:
                return value
        return None

    def _extract_definition_value(self, soup: BeautifulSoup, labels: list[str]) -> str | None:
        normalized_labels = {label.lower() for label in labels}
        for dt in soup.find_all("dt"):
            label = compact_whitespace(dt.get_text(" ", strip=True)).lower().rstrip(":")
            if label in normalized_labels:
                dd = dt.find_next_sibling("dd")
                if dd:
                    value = compact_whitespace(dd.get_text(" ", strip=True))
                    if value:
                        return value
        for row in soup.select("tr"):
            header = row.find("th")
            value_cell = row.find("td")
            if not header or not value_cell:
                continue
            label = compact_whitespace(header.get_text(" ", strip=True)).lower().rstrip(":")
            if label in normalized_labels:
                value = compact_whitespace(value_cell.get_text(" ", strip=True))
                if value:
                    return value
        return None

    def _extract_lead_paragraphs(self, soup: BeautifulSoup) -> str | None:
        paragraphs = soup.select("main p, article p, .mw-parser-output > p, .overview p")
        lines: list[str] = []
        for paragraph in paragraphs[:4]:
            text = compact_whitespace(paragraph.get_text(" ", strip=True))
            if len(text) >= 60:
                lines.append(text)
        return "\n\n".join(lines[:2]) if lines else None

    def _split_experience_items(self, value: str) -> list[str]:
        text = compact_whitespace(value)
        if not text:
            return []
        parts = re.split(r"\s*[;•]\s*|\s{2,}", text)
        items = [compact_whitespace(part) for part in parts if compact_whitespace(part)]
        return items or [text]

    def _extract_experience_bullets_from_text(self, text: str) -> list[str]:
        normalized = compact_whitespace(text)
        if not normalized:
            return []
        sentence_parts = re.split(r"(?<=[.;])\s+(?=[A-Z])", normalized)
        bullets: list[str] = []
        for part in sentence_parts:
            cleaned = compact_whitespace(part.strip(" .;"))
            if not cleaned:
                continue
            if re.search(r"\b(19|20)\d{2}\b", cleaned):
                bullets.append(cleaned)
        if bullets:
            return bullets[:6]
        generic_parts = [item for item in self._split_experience_items(normalized) if len(item) >= 20]
        return generic_parts[:6]

    def _as_bulleted_text(self, items: list[str]) -> str:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = compact_whitespace(item).strip("- ")
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(normalized)
        return "\n".join(f"- {item}" for item in cleaned[:6])

    def _parse_birth_date(self, value: str | None) -> date | None:
        if not value:
            return None
        text = compact_whitespace(value)
        iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
        if iso_match:
            try:
                return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
            except ValueError:
                pass
        for pattern in MONTH_DATE_PATTERNS:
            month_match = re.search(r"[A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4}", text)
            if not month_match:
                continue
            try:
                return datetime.strptime(month_match.group(0), pattern).date()
            except ValueError:
                continue
        return None

    def _extract_place_of_birth(self, born_text: str) -> str | None:
        text = compact_whitespace(born_text)
        text = self._remove_full_name_prefix(text)
        text = re.sub(r"\([^)]*\)", "", text).strip()
        text = re.sub(r"\b[A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4}\b", "", text).strip(" ,;")
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", text).strip(" ,;")
        return text or None

    def _extract_full_name_from_born_text(self, born_text: str | None) -> str | None:
        if not born_text:
            return None
        text = compact_whitespace(born_text)
        text = re.sub(r"\([^)]*\)", "", text).strip()
        match = re.match(
            r"^([A-Z][A-Za-z'`.-]+(?:\s+[A-Z][A-Za-z'`.-]+){1,5})\s+(?:born\s+)?(?:(?:[A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4})|\d{4}-\d{2}-\d{2})\b",
            text,
        )
        if not match:
            return None
        return compact_whitespace(match.group(1))

    def _remove_full_name_prefix(self, text: str) -> str:
        full_name = self._extract_full_name_from_born_text(text)
        if not full_name:
            return text
        pattern = rf"^{re.escape(full_name)}\s+"
        return re.sub(pattern, "", text).strip()

    def _looks_like_official_profile_url(self, url: str | None) -> bool:
        if not url:
            return False
        lowered = url.lower()
        if "wikipedia.org" in lowered or "govtrack.us" in lowered or "google.com/search" in lowered:
            return False
        return any(
            marker in lowered
            for marker in [
                ".gov/",
                ".gov",
                "whitehouse.gov",
                "state.gov",
                "treasury.gov",
                "defense.gov",
                "justice.gov",
                "house.gov",
                "senate.gov",
            ]
        )

    def _person_office_name(self, person: Person) -> str | None:
        appointments = list(person.appointments or [])
        current_appointments = [item for item in appointments if item.is_current]
        target = current_appointments[0] if current_appointments else (appointments[0] if appointments else None)
        if not target:
            return None
        if target.raw_payload and isinstance(target.raw_payload, dict):
            office_title = target.raw_payload.get("office_title")
            if office_title:
                return str(office_title)
        if target.role_title:
            return target.role_title
        if target.office and target.office.office_name:
            return target.office.office_name
        return None
