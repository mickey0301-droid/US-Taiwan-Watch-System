from __future__ import annotations

import re

from tracker.db import session_scope
from tracker.models import LegislationSponsor, Person
from tracker.services.officials_service import OfficialsService
from tracker.utils.names import normalize_person_name, slugify_name, split_person_name


def run_cleanup_malformed_legislation_people() -> dict:
    with session_scope() as session:
        officials = OfficialsService(session)
        rows = (
            session.query(Person)
            .filter(Person.seed_source_type == "legislation")
            .all()
        )
        cleaned = 0
        merged = 0
        deleted = 0

        for person in rows:
            cleaned_name = _clean_legislation_seed_name(person.full_name or "")
            if not cleaned_name or cleaned_name == person.full_name:
                continue

            existing = officials.find_person(cleaned_name)
            if existing and existing.id != person.id:
                sponsor_links = session.query(LegislationSponsor).filter(LegislationSponsor.person_id == person.id).all()
                for link in sponsor_links:
                    duplicate = (
                        session.query(LegislationSponsor)
                        .filter(
                            LegislationSponsor.legislation_id == link.legislation_id,
                            LegislationSponsor.person_id == existing.id,
                            LegislationSponsor.role == link.role,
                        )
                        .first()
                    )
                    if duplicate:
                        session.delete(link)
                    else:
                        link.person_id = existing.id
                if not person.appointments and not person.trackers and not person.statements and not person.statement_participants:
                    session.delete(person)
                    deleted += 1
                merged += 1
                continue

            person.full_name = cleaned_name
            given_name, family_name = split_person_name(cleaned_name)
            person.given_name = given_name
            person.family_name = family_name
            person.official_slug = slugify_name(cleaned_name)
            cleaned += 1

        return {
            "job_name": "cleanup_malformed_legislation_people",
            "status": "success",
            "records_found": len(rows),
            "records_created": 0,
            "records_updated": cleaned + merged,
            "metadata": {
                "cleaned": cleaned,
                "merged": merged,
                "deleted": deleted,
            },
            "errors": [],
        }


def _clean_legislation_seed_name(value: str) -> str:
    cleaned = re.sub(r"\[[^\]]+\]", "", value or "").strip()
    cleaned = re.sub(r"\b(Rep|Sen|Del|Resident Comm)\.?\b", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    return normalize_person_name(cleaned)
