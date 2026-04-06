from __future__ import annotations

from tracker.db import session_scope
from tracker.models import Legislation
from tracker.services.legislation_service import LegislationService


def run_enrich_congress_bills_sponsors() -> dict:
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
            payload = legislation.raw_payload or {}
            sponsor_name = _extract_sponsor_name(legislation, payload)
            if not sponsor_name:
                continue
            found += 1
            before = len(service.list_sponsors(legislation.id))
            service.ensure_legislation_sponsor(
                legislation.id,
                {
                    "full_name": sponsor_name,
                    "role": "sponsor",
                    "source_url": legislation.source_url,
                    "source_type": legislation.source_type,
                },
                {
                    "source_url": legislation.source_url,
                    "source_type": legislation.source_type,
                    "jurisdiction_name": legislation.jurisdiction_name,
                    "level": legislation.level,
                    "parser_identity": legislation.parser_identity,
                },
            )
            after = len(service.list_sponsors(legislation.id))
            if after > before:
                updated += 1

        return {
            "job_name": "enrich_congress_bills_sponsors",
            "status": "success",
            "records_found": len(rows),
            "records_created": 0,
            "records_updated": updated,
            "metadata": {"sponsor_candidates_found": found},
            "errors": [],
        }


def _extract_sponsor_name(legislation: Legislation, payload: dict) -> str | None:
    sponsor_text = str(payload.get("sponsor_text") or "").strip()
    if sponsor_text:
        sponsor_text = sponsor_text.removeprefix("Rep. ").removeprefix("Sen. ").strip()
        if "[" in sponsor_text:
            sponsor_text = sponsor_text.split("[", 1)[0].strip()
        if sponsor_text:
            return sponsor_text
    return None
