from __future__ import annotations

from tracker.db import session_scope
from tracker.models import Person
from tracker.utils.official_search import build_google_federal_executive_search_urls, build_google_official_bio_search_url, build_google_official_search_url
from tracker.utils.wikipedia_links import build_wikipedia_search_url, resolve_wikipedia_url


def run_discover_official_sources() -> dict:
    scanned = 0
    updated = 0
    with session_scope() as session:
        people = session.query(Person).all()
        for person in people:
            scanned += 1
            office_name = None
            if person.appointments:
                current = next((item for item in person.appointments if item.status == "current"), None)
                office_name = current.role_title if current else person.appointments[0].role_title

            raw_payload = dict(person.raw_payload or {})
            existing_urls = raw_payload.get("official_search_urls") or {}
            department_name = None
            if current and current.role_title and ":" in current.role_title:
                department_name = current.role_title.split(":", 1)[0].strip()
            elif raw_payload.get("department_name"):
                department_name = raw_payload.get("department_name")
            if current and current.role_title and (
                raw_payload.get("department_name") or department_name or "secretary" in current.role_title.lower()
            ):
                discovery_urls = build_google_federal_executive_search_urls(person.full_name, office_name, department_name)
            else:
                discovery_urls = {
                    "official_search": build_google_official_search_url(person.full_name, office_name),
                    "official_bio_search": build_google_official_bio_search_url(person.full_name, office_name),
                }
            wikipedia_url = resolve_wikipedia_url(person.source_url, raw_payload)
            wikipedia_links = {
                "wikipedia_page": wikipedia_url,
                "wikipedia_search": build_wikipedia_search_url(person.full_name, office_name),
            }
            existing_wikipedia_links = raw_payload.get("wikipedia_links") or {}
            if existing_urls != discovery_urls or existing_wikipedia_links != wikipedia_links:
                raw_payload["official_search_urls"] = discovery_urls
                raw_payload["wikipedia_links"] = wikipedia_links
                raw_payload["official_discovery_status"] = "search_ready"
                person.raw_payload = raw_payload
                updated += 1

    return {
        "status": "success",
        "job_name": "discover_official_sources",
        "people_scanned": scanned,
        "records_updated": updated,
        "metadata": {"note": "Prepared Google search links for official-source discovery from seed profiles."},
    }
