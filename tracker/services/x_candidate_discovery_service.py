from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs
from urllib.parse import quote_plus
from urllib.parse import unquote
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import Appointment
from tracker.models import Office
from tracker.models import Person
from tracker.utils.names import display_person_name
from tracker.utils.social import normalize_social_profiles


SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

OFFICIAL_KEYWORDS = [
    "senator",
    "representative",
    "congressman",
    "congresswoman",
    "governor",
    "secretary",
    "official",
    "ca40",
    "u.s. senate",
    "u.s. house",
    "member of congress",
]

PARODY_KEYWORDS = [
    "parody",
    "fan account",
    "unofficial",
    "not affiliated",
    "satire",
    "impersonation",
    "fake account",
    "commentary account",
    "backup account",
]

VERIFIED_HINT_KEYWORDS = [
    "verified account",
    "verified",
    "official account",
]


@dataclass
class XCandidateDiscoveryResult:
    people_scanned: int = 0
    records_updated: int = 0
    high_confidence_found: int = 0
    needs_review_found: int = 0
    rejected_found: int = 0
    errors: list[str] | None = None


class XCandidateDiscoveryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def discover_current_legislator_candidates(self, limit: int | None = None) -> XCandidateDiscoveryResult:
        people = self._current_federal_people(limit=limit)
        result = XCandidateDiscoveryResult(people_scanned=len(people), errors=[])
        for person, office in people:
            try:
                candidates = self._search_candidates(person, office)
            except Exception as exc:
                result.errors.append(f"{person.full_name}: {exc}")
                candidates = []
            candidates = self._merge_existing_official_x(person, candidates)
            raw_payload = dict(person.raw_payload or {})
            x_data = dict(raw_payload.get("x_candidate_links") or {})
            x_data["candidates"] = candidates
            x_data["last_scanned_at"] = datetime.utcnow().isoformat()
            x_data["search_status"] = "scanned" if candidates else "no_candidates_found"
            raw_payload["x_candidate_links"] = x_data
            person.raw_payload = raw_payload
            result.records_updated += 1
            result.high_confidence_found += len([c for c in candidates if c["status"] == "high_confidence"])
            result.needs_review_found += len([c for c in candidates if c["status"] == "needs_review"])
            result.rejected_found += len([c for c in candidates if c["status"] == "rejected"])
        return result

    def _current_federal_people(self, limit: int | None = None) -> list[tuple[Person, Office]]:
        stmt = (
            select(Person, Office)
            .join(Appointment, Appointment.person_id == Person.id)
            .join(Office, Office.id == Appointment.office_id)
            .where(
                Appointment.status == "current",
                Office.level == "federal",
                Office.branch.in_(["legislative", "executive"]),
            )
            .order_by(Office.branch.asc(), Office.chamber.asc(), Person.full_name.asc())
        )
        rows = self.session.execute(stmt).all()
        deduped: list[tuple[Person, Office]] = []
        seen_ids: set[int] = set()
        for person, office in rows:
            if person.id in seen_ids:
                continue
            seen_ids.add(person.id)
            deduped.append((person, office))
        if limit:
            deduped = deduped[:limit]
        return deduped

    def _search_candidates(self, person: Person, office: Office) -> list[dict[str, str]]:
        query = self._build_query(person, office)
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        response = httpx.get(url, timeout=20.0, follow_redirects=True, trust_env=False, headers=SEARCH_HEADERS)
        response.raise_for_status()
        if response.status_code == 202:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        candidates: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for result in soup.select(".result")[:8]:
            link = result.select_one("a.result__a")
            if not link:
                continue
            candidate_url = self._unwrap_duckduckgo_url(link.get("href", ""))
            if not candidate_url or candidate_url in seen_urls:
                continue
            seen_urls.add(candidate_url)
            profile_url = self._normalize_profile_url(candidate_url)
            if not profile_url:
                continue
            title = " ".join(link.get_text(" ", strip=True).split())
            snippet_node = result.select_one(".result__snippet")
            snippet = " ".join((snippet_node.get_text(" ", strip=True) if snippet_node else "").split())
            candidate = self._classify_candidate(person, office, profile_url, title, snippet)
            candidates.append(candidate)
            if len(candidates) >= 5:
                break
        return candidates

    def _build_query(self, person: Person, office: Office) -> str:
        display_name = display_person_name(person.full_name, person.given_name, person.family_name)
        role_query = " OR ".join(f'"{term}"' for term in self._office_search_terms(office))
        return f'site:x.com "{display_name}" ({role_query})'

    def _office_search_terms(self, office: Office) -> list[str]:
        if office.branch == "legislative":
            if office.chamber == "senate":
                return ["Senator", "U.S. Senator"]
            return ["Representative", "Congressman", "Congresswoman", "Member of Congress"]

        office_name = office.office_name or ""
        lowered = office_name.lower()
        terms = [office_name] if office_name else []
        if "secretary" in lowered:
            terms.extend(["Secretary", "U.S. official"])
        elif "deputy" in lowered:
            terms.extend(["Deputy Secretary", "U.S. official"])
        elif "director" in lowered:
            terms.extend(["Director", "U.S. official"])
        else:
            terms.append("U.S. official")
        return [term for term in terms if term]

    def _unwrap_duckduckgo_url(self, url: str) -> str | None:
        if not url:
            return None
        if url.startswith("//duckduckgo.com/l/?"):
            parsed = urlparse(f"https:{url}")
            uddg = parse_qs(parsed.query).get("uddg", [])
            if uddg:
                return unquote(uddg[0])
        return url

    def _normalize_profile_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        hostname = (parsed.netloc or "").lower()
        if not (hostname == "x.com" or hostname.endswith(".x.com") or hostname == "twitter.com" or hostname.endswith(".twitter.com")):
            return None
        path_parts = [part for part in parsed.path.split("/") if part]
        if not path_parts:
            return None
        if path_parts[0].lower() in {"search", "home", "i", "settings", "explore", "hashtag", "intent"}:
            return None
        if len(path_parts) > 1 and path_parts[1].lower() == "status":
            return None
        return f"{parsed.scheme or 'https'}://{parsed.netloc}/{path_parts[0]}"

    def _classify_candidate(self, person: Person, office: Office, profile_url: str, title: str, snippet: str) -> dict[str, str]:
        combined = f"{title}\n{snippet}".lower()
        handle = (urlparse(profile_url).path or "").strip("/")
        reasons: list[str] = []
        status = "needs_review"

        if any(keyword in combined for keyword in PARODY_KEYWORDS):
            status = "rejected"
            reasons.append("parody_or_unofficial_keyword")

        name_bits = [bit.lower() for bit in display_person_name(person.full_name, person.given_name, person.family_name).split() if len(bit) >= 3]
        if not any(bit in combined or bit in handle.lower() for bit in name_bits):
            reasons.append("name_mismatch")
            if status != "rejected":
                status = "rejected"

        office_terms = self._classification_terms(office)
        if any(term in combined for term in office_terms):
            reasons.append("office_keyword_match")
        else:
            reasons.append("missing_office_keyword")

        if any(keyword in combined for keyword in OFFICIAL_KEYWORDS):
            reasons.append("official_keyword_match")

        if status != "rejected":
            if "office_keyword_match" in reasons and "official_keyword_match" in reasons:
                status = "high_confidence"
            else:
                status = "needs_review"

        verification_hint = any(keyword in combined for keyword in VERIFIED_HINT_KEYWORDS) or "official_site_match" in reasons

        return {
            "profile_url": profile_url,
            "title": title,
            "snippet": snippet,
            "status": status,
            "handle": handle,
            "reasons": ", ".join(reasons),
            "verification_hint": "true" if verification_hint else "false",
        }

    def _classification_terms(self, office: Office) -> list[str]:
        if office.branch == "legislative":
            if office.chamber == "senate":
                return ["senator", "u.s. senator"]
            return ["representative", "congressman", "congresswoman", "member of congress", "u.s. representative"]

        office_name = (office.office_name or "").lower()
        terms = [office_name] if office_name else []
        if "secretary" in office_name:
            terms.extend(["secretary", "cabinet", "administration official"])
        if "deputy" in office_name:
            terms.extend(["deputy secretary", "administration official"])
        if "director" in office_name:
            terms.extend(["director", "administration official"])
        if not terms:
            terms.extend(["official", "administration official"])
        return terms

    def _merge_existing_official_x(self, person: Person, candidates: list[dict[str, str]]) -> list[dict[str, str]]:
        profiles = normalize_social_profiles(person.social_profiles)
        x_url = profiles.get("x")
        if not x_url:
            return candidates
        existing_urls = {item.get("profile_url") for item in candidates}
        if x_url in existing_urls:
            return candidates
        confirmed = {
            "profile_url": x_url,
            "title": f"{display_person_name(person.full_name, person.given_name, person.family_name)} (official site match)",
            "snippet": "Matched from official website social link.",
            "status": "high_confidence",
            "handle": (urlparse(x_url).path or "").strip("/"),
            "reasons": "official_site_match",
            "verification_hint": "true",
        }
        return [confirmed, *candidates]

