from __future__ import annotations

import argparse

from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import Person
from tracker.services.officials_service import OfficialsService
from tracker.services.profile_enrichment_service import ProfileEnrichmentService


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill one person's background profile.")
    parser.add_argument("person_id", type=int)
    args = parser.parse_args()
    with session_scope() as session:
        person = session.scalar(select(Person).where(Person.id == args.person_id))
        if not person:
            print("Person not found.")
            return
        result = ProfileEnrichmentService(OfficialsService(session)).enrich_person(person)
        print(f"ID: {person.id}")
        print(f"Name: {person.full_name}")
        print(f"Updated fields: {', '.join(result.updated_fields) if result.updated_fields else 'None'}")
        print(f"Sources checked: {', '.join(result.source_urls) if result.source_urls else 'None'}")
        print(f"Errors: {' | '.join(result.errors) if result.errors else 'None'}")


if __name__ == "__main__":
    main()
