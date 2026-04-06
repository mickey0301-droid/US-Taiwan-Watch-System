from __future__ import annotations

from sqlalchemy import or_, select

from tracker.db import session_scope
from tracker.models import Appointment, Office, Person
from tracker.services.officials_service import OfficialsService
from tracker.services.profile_enrichment_service import ProfileEnrichmentService


def _pending_people_stmt(batch_size: int):
    person_ids = (
        select(Person.id)
        .join(Appointment, Appointment.person_id == Person.id)
        .join(Office, Office.id == Appointment.office_id)
        .where(
            Office.level == "federal",
            Appointment.status == "current",
            or_(
                Person.date_of_birth.is_(None),
                Person.place_of_birth.is_(None),
                Person.education.is_(None),
                Person.career_history.is_(None),
            ),
        )
        .distinct()
        .order_by(Person.updated_at.asc())
        .limit(batch_size)
    )
    return select(Person).where(Person.id.in_(person_ids)).order_by(Person.updated_at.asc())


def run_enrich_current_federal_backgrounds(batch_size: int = 10, max_batches: int = 10) -> dict:
    checked = 0
    updated = 0
    errors: dict[str, list[str]] = {}
    batch_results: list[dict[str, int]] = []

    for batch_number in range(1, max_batches + 1):
        with session_scope() as session:
            people = session.execute(_pending_people_stmt(batch_size)).scalars().unique().all()
            if not people:
                break
            service = ProfileEnrichmentService(OfficialsService(session))
            batch_checked = 0
            batch_updated = 0
            batch_error_count = 0
            for person in people:
                result = service.enrich_person(person)
                checked += 1
                batch_checked += 1
                if result.updated_fields:
                    updated += 1
                    batch_updated += 1
                if result.errors:
                    errors[str(person.id)] = result.errors
                    batch_error_count += 1
            batch_results.append(
                {
                    "batch": batch_number,
                    "people_scanned": batch_checked,
                    "records_updated": batch_updated,
                    "error_count": batch_error_count,
                }
            )
            if batch_checked < batch_size:
                break

    return {
        "status": "failed" if errors else "success",
        "job_name": "enrich_current_federal_backgrounds",
        "people_scanned": checked,
        "records_updated": updated,
        "error_count": len(errors),
        "errors": errors,
        "batch_size": batch_size,
        "max_batches": max_batches,
        "batches": batch_results,
        "scope": "current_federal_people",
    }
