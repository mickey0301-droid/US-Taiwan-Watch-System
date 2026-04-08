from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import Alias, Appointment, Office, Person

DEFAULT_HOUSE_URL = "https://fapa.org/119th-house-taiwan-caucus/"
DEFAULT_SENATE_URL = "https://fapa.org/119th-senate-taiwan-caucus/"
PARSER_IDENTITY = "fapa_taiwan_caucus_v1"


@dataclass
class CaucusPage:
    url: str
    slug: str
    chamber: str
    congress: int
    title: str
    content_html: str


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path.split("/")[0] if path else ""


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'")
    text = re.sub(r"[^A-Za-z\s'\-\.]", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _canonical_name(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^co-chair\s+", "", text, flags=re.I)
    text = re.sub(r"\b(sen\.|senator|rep\.|representative|congressman|congresswoman)\b", "", text, flags=re.I)
    text = re.sub(r"\s+[RD]\s*[-,]\s*[A-Z]{2}(?:-\d+)?$", "", text, flags=re.I)
    text = re.sub(r"\([A-Z]{1,3}-\d+\)$", "", text, flags=re.I).strip()
    text = re.sub(r"\s+", " ", text).strip(" ,;")

    if "," in text:
        left, right = [part.strip() for part in text.split(",", 1)]
        if left and right and len(left.split()) <= 3:
            text = f"{right} {left}".strip()

    return re.sub(r"\s+", " ", text).strip()


def _extract_member_name_from_paragraph(paragraph: BeautifulSoup) -> str | None:
    raw = paragraph.get_text("\n", strip=True)
    if not raw:
        return None
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return None

    first = lines[0]
    if re.search(r"\b(members?)\b", first, flags=re.I):
        return None
    if first.lower() in {"republican", "democrat", "independent"}:
        return None

    if len(lines) >= 2 and re.fullmatch(r"[RD]-[A-Z]{2}(?:-\d+)?", lines[1], flags=re.I):
        candidate = first
    else:
        candidate = first
        candidate = re.sub(r"\s*\([A-Z]{1,3}-\d+\)\s*$", "", candidate, flags=re.I)
        candidate = re.sub(r"\s+[RD]\s*[-,]\s*[A-Z]{2}(?:-\d+)?\s*$", "", candidate, flags=re.I)

    candidate = _canonical_name(candidate)
    if not candidate:
        return None
    if len(candidate.split()) < 2:
        return None
    if re.search(r"\b(republican|democrat|members?)\b", candidate, flags=re.I):
        return None
    return candidate


def _extract_members(content_html: str) -> list[str]:
    soup = BeautifulSoup(content_html, "html.parser")
    names: list[str] = []
    seen: set[str] = set()
    for p in soup.select("p"):
        name = _extract_member_name_from_paragraph(p)
        if not name:
            continue
        key = _normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _extract_history_links(content_html: str, chamber: str) -> list[str]:
    soup = BeautifulSoup(content_html, "html.parser")
    links: list[str] = []
    seen: set[str] = set()
    needle = f"{chamber}-taiwan-caucus"
    for a in soup.select("a[href]"):
        href = str(a.get("href") or "").strip()
        if not href or needle not in href:
            continue
        if href.startswith("/"):
            href = f"https://fapa.org{href}"
        if href in seen:
            continue
        seen.add(href)
        links.append(href)
    return links


def _infer_chamber(slug: str) -> str:
    if "house" in slug:
        return "house"
    if "senate" in slug:
        return "senate"
    raise ValueError(f"Cannot infer chamber from slug: {slug}")


def _infer_congress(slug: str, title: str) -> int:
    for text in [slug, title]:
        match = re.search(r"(\d{3})(?:st|nd|rd|th)", text, flags=re.I)
        if match:
            return int(match.group(1))
    raise ValueError(f"Cannot infer congress from slug/title: {slug} / {title}")


def _fetch_page_from_slug(slug: str) -> CaucusPage:
    api_url = f"https://fapa.org/wp-json/wp/v2/pages?slug={slug}"
    response = httpx.get(api_url, timeout=30.0, follow_redirects=True, trust_env=False)
    response.raise_for_status()
    payload = response.json()
    if not payload:
        raise ValueError(f"No page found for slug: {slug}")
    item = payload[0]
    page_url = str(item.get("link") or f"https://fapa.org/{slug}/")
    title = str((item.get("title") or {}).get("rendered") or slug)
    content_html = str((item.get("content") or {}).get("rendered") or "")
    chamber = _infer_chamber(slug)
    congress = _infer_congress(slug, title)
    return CaucusPage(
        url=page_url,
        slug=slug,
        chamber=chamber,
        congress=congress,
        title=title,
        content_html=content_html,
    )


def _fetch_page(url: str) -> CaucusPage:
    slug = _slug_from_url(url)
    if not slug:
        raise ValueError(f"Invalid URL for FAPA page: {url}")
    return _fetch_page_from_slug(slug)


def _name_variants(name: str) -> set[str]:
    text = _canonical_name(name)
    variants = {_normalize_name(text)}
    parts = [part for part in text.split() if part]
    if len(parts) >= 2:
        variants.add(_normalize_name(f"{parts[-1]}, {' '.join(parts[:-1])}"))
        variants.add(_normalize_name(f"{' '.join(parts[:-1])} {parts[-1]}"))
    return {item for item in variants if item}


def _build_person_index(session, chamber: str) -> dict[str, int]:
    rows = session.execute(
        select(Person.id, Person.full_name)
        .join(Appointment, Appointment.person_id == Person.id)
        .join(Office, Office.id == Appointment.office_id)
        .where(
            Office.level == "federal",
            Office.branch == "legislative",
            Office.chamber == chamber,
        )
    ).all()

    person_ids = sorted({int(person_id) for person_id, _ in rows})
    aliases = session.execute(
        select(Alias.person_id, Alias.alias)
        .where(
            Alias.person_id.in_(person_ids),
            Alias.is_current.is_(True),
        )
    ).all()

    index: dict[str, int] = {}
    for person_id, full_name in rows:
        for variant in _name_variants(str(full_name or "")):
            index.setdefault(variant, int(person_id))
    for person_id, alias_text in aliases:
        for variant in _name_variants(str(alias_text or "")):
            index.setdefault(variant, int(person_id))

    return index


def _append_membership(person: Person, membership: dict[str, object]) -> bool:
    raw_payload = dict(person.raw_payload or {})
    memberships = list(raw_payload.get("taiwan_caucus_memberships") or [])
    key = (
        str(membership.get("group") or ""),
        str(membership.get("chamber") or ""),
        int(membership.get("congress") or 0),
        str(membership.get("source_url") or ""),
    )
    existing_keys = {
        (
            str(item.get("group") or ""),
            str(item.get("chamber") or ""),
            int(item.get("congress") or 0),
            str(item.get("source_url") or ""),
        )
        for item in memberships
        if isinstance(item, dict)
    }
    if key in existing_keys:
        return False

    memberships.append(membership)
    memberships = sorted(
        memberships,
        key=lambda item: (
            str(item.get("chamber") or ""),
            int(item.get("congress") or 0),
            str(item.get("source_url") or ""),
        ),
    )
    raw_payload["taiwan_caucus_memberships"] = memberships
    raw_payload["taiwan_caucus_updated_at"] = datetime.utcnow().isoformat()
    person.raw_payload = raw_payload
    return True


def _target_links(seed_urls: Iterable[str], include_history: bool) -> list[str]:
    queue: list[str] = []
    seen: set[str] = set()
    for url in seed_urls:
        clean = str(url or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        queue.append(clean)

    if not include_history:
        return queue

    expanded = list(queue)
    for url in queue:
        page = _fetch_page(url)
        for history_url in _extract_history_links(page.content_html, page.chamber):
            clean = str(history_url or "").strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            expanded.append(clean)
    return expanded


def run(seed_urls: list[str], include_history: bool, congresses: set[int] | None = None) -> dict[str, object]:
    pages: list[CaucusPage] = []
    for url in _target_links(seed_urls, include_history=include_history):
        try:
            page = _fetch_page(url)
        except Exception:
            continue
        if congresses and page.congress not in congresses:
            continue
        pages.append(page)

    report: dict[str, object] = {
        "pages": [],
        "total_names": 0,
        "matched": 0,
        "updated_people": 0,
        "unmatched_names": [],
    }

    with session_scope() as session:
        index_by_chamber: dict[str, dict[str, int]] = {}
        touched_person_ids: set[int] = set()
        unmatched: set[str] = set()

        for page in pages:
            names = _extract_members(page.content_html)
            report["pages"].append(
                {
                    "url": page.url,
                    "slug": page.slug,
                    "congress": page.congress,
                    "chamber": page.chamber,
                    "names": len(names),
                }
            )
            report["total_names"] = int(report["total_names"]) + len(names)

            if page.chamber not in index_by_chamber:
                index_by_chamber[page.chamber] = _build_person_index(session, page.chamber)

            index = index_by_chamber[page.chamber]
            for name in names:
                person_id = None
                for variant in _name_variants(name):
                    person_id = index.get(variant)
                    if person_id:
                        break
                if not person_id:
                    unmatched.add(name)
                    continue

                person = session.get(Person, int(person_id))
                if not person:
                    unmatched.add(name)
                    continue

                membership = {
                    "group": "Taiwan Caucus",
                    "congress": int(page.congress),
                    "chamber": page.chamber,
                    "source_url": page.url,
                    "source_type": "fapa",
                    "parser_identity": PARSER_IDENTITY,
                    "member_name_on_source": name,
                    "collected_at": datetime.utcnow().isoformat(),
                }
                changed = _append_membership(person, membership)
                report["matched"] = int(report["matched"]) + 1
                if changed:
                    touched_person_ids.add(int(person.id))

        report["updated_people"] = len(touched_person_ids)
        report["unmatched_names"] = sorted(unmatched)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Taiwan Caucus members from FAPA pages into person raw_payload.")
    parser.add_argument("--house-url", default=DEFAULT_HOUSE_URL)
    parser.add_argument("--senate-url", default=DEFAULT_SENATE_URL)
    parser.add_argument("--include-history", action="store_true")
    parser.add_argument("--congresses", default="", help="Comma-separated congress numbers (e.g., 119,118)")
    args = parser.parse_args()

    congresses: set[int] | None = None
    if str(args.congresses or "").strip():
        congresses = {int(item.strip()) for item in str(args.congresses).split(",") if item.strip().isdigit()}

    result = run(
        seed_urls=[args.house_url, args.senate_url],
        include_history=bool(args.include_history),
        congresses=congresses,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

