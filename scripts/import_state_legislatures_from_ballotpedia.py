from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import text

from tracker.db import session_scope
from tracker.services.officials_service import OfficialsService


BASE_URL = "https://ballotpedia.org/"
USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"
PARSER_IDENTITY = "ballotpedia_state_legislatures_v1"
REQUEST_SLEEP_SECONDS = 0.35

US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
    "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri",
    "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
]

SENATE_SLUG_PATTERNS = [
    "{state}_State_Senate",
    "{state}_Senate",
]

HOUSE_SLUG_PATTERNS = [
    "{state}_House_of_Representatives",
    "{state}_State_House_of_Representatives",
    "{state}_House_of_Delegates",
    "{state}_State_Assembly",
]


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _build_url(slug: str) -> str:
    return urljoin(BASE_URL, quote(slug, safe="_()/-"))


def _fetch_html(url: str, max_attempts: int = 4) -> str:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = httpx.get(
                url,
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://ballotpedia.org/",
                },
                trust_env=False,
            )
            if response.status_code != 200:
                raise RuntimeError(f"Unexpected HTTP status {response.status_code} for {url}")
            if not response.text or len(response.text) < 1000:
                raise RuntimeError(f"Empty/short response body for {url} (len={len(response.text)})")
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts:
                time.sleep(0.8 * attempt)
    assert last_error is not None
    raise last_error


def _extract_district(text: str) -> str | None:
    cleaned = _clean_text(text)
    if not cleaned:
        return None
    match = re.search(r"District\s+([0-9A-Za-z\-]+)", cleaned, flags=re.I)
    if match:
        return match.group(1)
    return cleaned


def _parse_roster_rows(html: str, chamber: str, state: str, source_url: str) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one(".noarticletext"):
        return {"records": [], "chamber_label": None}

    table = soup.select_one("table#officeholder-table")
    if table is None:
        return {"records": [], "chamber_label": None}

    heading = soup.select_one("h1 .mw-page-title-main")
    chamber_label = _clean_text(heading.get_text(" ", strip=True)) if heading else None

    records: list[dict[str, str | None]] = []
    for row in table.select("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        district_text = _clean_text(cells[0].get_text(" ", strip=True))
        name_text = _clean_text(cells[1].get_text(" ", strip=True))
        party_text = _clean_text(cells[2].get_text(" ", strip=True))
        if not name_text or name_text.lower() == "vacant":
            continue

        person_anchor = cells[1].find("a", href=True)
        person_url = urljoin(BASE_URL, person_anchor["href"]) if person_anchor else source_url

        records.append(
            {
                "state": state,
                "chamber": chamber,
                "district": _extract_district(district_text),
                "full_name": name_text,
                "party": party_text or None,
                "source_url": person_url,
                "chamber_source_url": source_url,
            }
        )

    return {"records": records, "chamber_label": chamber_label}


def _find_best_chamber_page(state: str, chamber: str) -> tuple[str | None, list[dict[str, str | None]], str | None]:
    patterns = SENATE_SLUG_PATTERNS if chamber == "senate" else HOUSE_SLUG_PATTERNS
    best_url: str | None = None
    best_label: str | None = None
    best_records: list[dict[str, str | None]] = []

    for pattern in patterns:
        slug = pattern.format(state=state.replace(" ", "_"))
        url = _build_url(slug)
        try:
            html = _fetch_html(url)
        except Exception:
            continue

        parsed = _parse_roster_rows(html, chamber=chamber, state=state, source_url=url)
        records = parsed["records"]
        if len(records) > len(best_records):
            best_url = url
            best_label = parsed["chamber_label"]  # type: ignore[assignment]
            best_records = records  # type: ignore[assignment]
        time.sleep(REQUEST_SLEEP_SECONDS)

    return best_url, best_records, best_label


def _role_title_from_label(chamber: str, chamber_label: str | None) -> str:
    label = (chamber_label or "").lower()
    if chamber == "senate":
        return "State Senator"
    if "assembly" in label:
        return "State Assemblymember"
    if "delegate" in label:
        return "State Delegate"
    return "State Representative"


def _office_name_from_label(state: str, chamber: str, chamber_label: str | None) -> str:
    if chamber_label:
        return chamber_label
    if chamber == "senate":
        return f"{state} State Senate"
    return f"{state} House of Representatives"


def _collect_records(states: list[str]) -> dict[str, object]:
    all_records: list[dict[str, str | None]] = []
    discovery: list[dict[str, str | int | None]] = []

    for state in states:
        for chamber in ("senate", "house"):
            source_url, chamber_records, chamber_label = _find_best_chamber_page(state, chamber)
            discovery.append(
                {
                    "state": state,
                    "chamber": chamber,
                    "source_url": source_url,
                    "records": len(chamber_records),
                    "chamber_label": chamber_label,
                }
            )
            for item in chamber_records:
                merged = dict(item)
                merged["chamber_label"] = chamber_label
                all_records.append(merged)

    deduped: dict[tuple[str, str, str | None, str], dict[str, str | None]] = {}
    for item in all_records:
        key = (
            str(item["state"]),
            str(item["chamber"]),
            item.get("district"),
            str(item["full_name"]),
        )
        deduped.setdefault(key, item)

    return {"records": list(deduped.values()), "discovery": discovery}


def _upsert(records: list[dict[str, str | None]], dry_run: bool) -> dict[str, object]:
    if dry_run:
        return {
            "records_found": len(records),
            "records_created": 0,
            "records_updated": 0,
            "sample": records[:30],
        }

    created = 0
    updated = 0

    with session_scope() as session:
        service = OfficialsService(session)
        usa = service.get_or_create_jurisdiction("United States", "country", code="US")
        existing_current_keys: set[tuple[str, str, str, str]] = set()
        skipped_existing = 0

        rows = session.execute(
            text(
                """
                SELECT
                  j.name AS state_name,
                  COALESCE(o.chamber, '') AS chamber,
                  COALESCE(p.full_name, '') AS full_name,
                  COALESCE(a.district, '') AS district
                FROM appointments a
                JOIN offices o ON o.id = a.office_id
                JOIN jurisdictions j ON j.id = a.jurisdiction_id
                JOIN persons p ON p.id = a.person_id
                WHERE a.is_current = 1
                  AND o.level = 'state'
                  AND o.branch = 'legislative'
                """
            )
        ).fetchall()
        for row in rows:
            existing_current_keys.add(
                (
                    _clean_text(row.state_name).lower(),
                    _clean_text(row.chamber).lower(),
                    _clean_text(row.full_name).lower(),
                    _clean_text(row.district).lower(),
                )
            )

        for item in records:
            state_name = str(item["state"])
            chamber = str(item["chamber"])
            chamber_label = item.get("chamber_label")
            chamber_source_url = str(item["chamber_source_url"])
            key = (
                _clean_text(state_name).lower(),
                _clean_text(chamber).lower(),
                _clean_text(str(item["full_name"])).lower(),
                _clean_text(str(item.get("district") or "")).lower(),
            )
            if key in existing_current_keys:
                skipped_existing += 1
                continue

            state = service.get_or_create_jurisdiction(state_name, "state", code=state_name, parent_id=usa.id)
            office = service.get_or_create_office(
                office_name=_office_name_from_label(state_name, chamber, chamber_label),
                level="state",
                branch="legislative",
                chamber=chamber,
                jurisdiction_id=state.id,
                source_url=chamber_source_url,
                source_type="media",
            )

            person_payload = {
                "full_name": str(item["full_name"]),
                "source_url": str(item["source_url"]),
                "source_type": "media",
                "profile_status": "seeded",
                "canonical_official_url": str(item["source_url"]),
                "parser_identity": PARSER_IDENTITY,
                "raw_payload": {
                    "source": "ballotpedia",
                    "state": state_name,
                    "chamber": chamber,
                    "district": item.get("district"),
                    "chamber_source_url": chamber_source_url,
                },
            }
            person, is_created = service.upsert_person(person_payload)
            if is_created:
                created += 1
            else:
                updated += 1

            service.upsert_appointment(
                person=person,
                office=office,
                jurisdiction_id=state.id,
                payload={
                    "role_title": _role_title_from_label(chamber, chamber_label),
                    "district": item.get("district"),
                    "party": item.get("party"),
                    "status": "current",
                    "source_url": str(item["source_url"]),
                    "source_type": "media",
                    "parser_identity": PARSER_IDENTITY,
                    "is_current": True,
                    "raw_payload": {
                        "source": "ballotpedia",
                        "state": state_name,
                        "chamber": chamber,
                        "district": item.get("district"),
                        "chamber_source_url": chamber_source_url,
                    },
                },
            )
            existing_current_keys.add(key)

    return {
        "records_found": len(records),
        "records_created": created,
        "records_updated": updated,
        "records_skipped_existing": skipped_existing,
        "sample": records[:30],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import current state legislature members from Ballotpedia.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--states",
        help="Comma-separated states to process (default: all states). Example: 'Florida,Texas,New York'",
    )
    args = parser.parse_args()

    states = US_STATES
    if args.states:
        wanted = {_clean_text(s) for s in args.states.split(",") if _clean_text(s)}
        states = [s for s in US_STATES if s in wanted]

    collected = _collect_records(states)
    records = collected["records"]  # type: ignore[assignment]
    discovery = collected["discovery"]  # type: ignore[assignment]
    outcome = _upsert(records, dry_run=args.dry_run)
    result = {
        "status": "dry_run" if args.dry_run else "success",
        "source_url": "https://ballotpedia.org/State_senates",
        "parser_identity": PARSER_IDENTITY,
        "started_at_utc": datetime.utcnow().isoformat(),
        "chambers_discovered": discovery,
        **outcome,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
