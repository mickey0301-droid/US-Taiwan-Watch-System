from __future__ import annotations

from tracker.db import session_scope
from tracker.models import Legislation
from tracker.services.legislation_service import LegislationService
from tracker.utils.congress_bills import congress_bill_url, congress_from_url


def run_enrich_congress_bills_official_links() -> dict:
    with session_scope() as session:
        service = LegislationService(session)
        rows = (
            session.query(Legislation)
            .filter(Legislation.parser_identity == "congress_bills_excel_v1")
            .all()
        )
        updated = 0
        found = 0
        for legislation in rows:
            payload = dict(legislation.raw_payload or {})
            existing_url = payload.get("congress_gov_url")
            congress = (
                payload.get("congress")
                or congress_from_url(existing_url)
                or congress_from_url(legislation.source_url)
            )
            official_url = existing_url or congress_bill_url(congress, legislation.bill_number)
            if not official_url:
                continue
            found += 1
            payload["congress_gov_url"] = official_url
            if congress is not None:
                payload["congress"] = int(congress)
            payload["source_priority"] = "official"
            legislation.raw_payload = payload
            legislation.source_url = official_url
            legislation.source_type = "official"
            service.ensure_legislation_source(
                legislation.id,
                {
                    "source_url": official_url,
                    "source_type": "official",
                    "source_title": f"Congress.gov | {legislation.bill_number or legislation.title}",
                    "parser_identity": "congress_bill_url_enrichment_v1",
                    "raw_payload": {"derived_from": "congress_bills_excel_v1"},
                },
            )
            updated += 1
        return {
            "job_name": "enrich_congress_bills_official_links",
            "status": "success",
            "records_found": len(rows),
            "records_created": 0,
            "records_updated": updated,
            "metadata": {"official_links_added": found},
            "errors": [],
        }
