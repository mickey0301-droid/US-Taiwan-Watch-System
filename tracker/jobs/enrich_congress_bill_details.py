from __future__ import annotations

from tracker.db import session_scope
from tracker.models import Legislation
from tracker.services.congress_bill_details_service import CongressBillDetailsService


def run_enrich_congress_bill_details(limit: int | None = 50) -> dict:
    with session_scope() as session:
        service = CongressBillDetailsService(session)
        query = (
            session.query(Legislation)
            .filter(Legislation.level == "federal")
            .filter(Legislation.source_url.like("https://www.congress.gov/%"))
            .order_by(Legislation.introduced_date.desc().nullslast(), Legislation.id.desc())
        )
        rows = query.limit(limit).all() if limit else query.all()

        updated = 0
        sponsors_added = 0
        cosponsors_added = 0
        errors: list[str] = []
        for legislation in rows:
            result = service.enrich_legislation(legislation)
            if result.errors:
                errors.append(f"{legislation.bill_number or legislation.id}: {'; '.join(result.errors)}")
                continue
            updated += 1
            sponsors_added += result.sponsors_added
            cosponsors_added += result.cosponsors_added

        return {
            "job_name": "enrich_congress_bill_details",
            "status": "success" if not errors else "partial_success",
            "records_found": len(rows),
            "records_created": 0,
            "records_updated": updated,
            "metadata": {
                "sponsors_added": sponsors_added,
                "cosponsors_added": cosponsors_added,
                "limit": limit,
            },
            "errors": errors[:100],
        }
