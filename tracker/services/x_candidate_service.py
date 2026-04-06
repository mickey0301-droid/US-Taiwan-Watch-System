from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import Appointment
from tracker.models import Office
from tracker.models import Person
from tracker.utils.official_search import build_x_search_url


@dataclass
class XCandidateSeedResult:
    people_scanned: int = 0
    records_updated: int = 0


class XCandidateService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def seed_current_legislator_x_searches(self) -> XCandidateSeedResult:
        people = self._current_federal_people()
        result = XCandidateSeedResult(people_scanned=len(people))
        for person, office in people:
            raw_payload = dict(person.raw_payload or {})
            x_candidates = dict(raw_payload.get("x_candidate_links") or {})
            x_candidates["google_x_search"] = build_x_search_url(person.full_name, office.office_name)
            x_candidates["search_status"] = "search_ready"
            x_candidates["office_name"] = office.office_name
            raw_payload["x_candidate_links"] = x_candidates
            person.raw_payload = raw_payload
            result.records_updated += 1
        return result

    def _current_federal_people(self) -> list[tuple[Person, Office]]:
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
        return deduped
