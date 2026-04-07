from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import Appointment, Office, Person, Tracker
from tracker.services.tracker_service import TrackerService
from tracker.utils.web import build_cspan_search_url
from tracker.utils.web import build_google_news_rss_url


@dataclass
class BootstrapResult:
    people_scanned: int = 0
    trackers_created: int = 0
    trackers_updated: int = 0
    targets_added: int = 0


class CurrentTaiwanCollectionService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.tracker_service = TrackerService(session)

    def bootstrap_current_federal_trackers(self, year: int | None = None, limit: int | None = None) -> BootstrapResult:
        target_year = year or datetime.utcnow().year
        people = self._list_current_federal_people(limit=limit)
        result = BootstrapResult(people_scanned=len(people))

        for person, office in people:
            existing = self.session.execute(
                select(Tracker).where(
                    Tracker.person_id == person.id,
                    Tracker.name == f"{target_year} Taiwan monitor",
                )
            ).scalar_one_or_none()
            targets = self._build_targets(person, office, target_year)
            tracker = self.tracker_service.create_or_update_tracker(
                tracker_id=existing.id if existing else None,
                person_id=person.id,
                name=f"{target_year} Taiwan monitor",
                status="active",
                include_primary_sources=True,
                include_media_reports=True,
                schedule_cron=None,
                targets=targets,
            )
            if existing:
                result.trackers_updated += 1
            else:
                result.trackers_created += 1
            result.targets_added += len(targets)
        return result

    def bootstrap_existing_people_trackers_for_years(
        self,
        years: list[int],
        limit: int | None = None,
    ) -> BootstrapResult:
        normalized_years = sorted({year for year in years if year >= 2000})
        if not normalized_years:
            return BootstrapResult()

        people = self._list_existing_public_people(limit=limit)
        result = BootstrapResult(people_scanned=len(people))
        for person, office in people:
            for year in normalized_years:
                existing = self.session.execute(
                    select(Tracker).where(
                        Tracker.person_id == person.id,
                        Tracker.name == f"{year} Taiwan monitor",
                    )
                ).scalar_one_or_none()
                targets = self._build_targets(person, office, year, news_query_variants=3)
                tracker = self.tracker_service.create_or_update_tracker(
                    tracker_id=existing.id if existing else None,
                    person_id=person.id,
                    name=f"{year} Taiwan monitor",
                    status="active",
                    include_primary_sources=True,
                    include_media_reports=True,
                    schedule_cron=None,
                    targets=targets,
                )
                if existing:
                    result.trackers_updated += 1
                else:
                    result.trackers_created += 1
                result.targets_added += len(targets)
        return result

    def _list_current_federal_people(self, limit: int | None = None) -> list[tuple[Person, Office]]:
        stmt = (
            select(Person, Office)
            .join(Appointment, Appointment.person_id == Person.id)
            .join(Office, Office.id == Appointment.office_id)
            .where(
                Appointment.status == "current",
                Office.level == "federal",
                Office.branch.in_(["legislative", "executive"]),
            )
            .order_by(Office.branch.asc(), Office.chamber.asc().nullslast(), Person.full_name.asc())
        )
        rows = self.session.execute(stmt).all()
        prioritized = sorted(rows, key=lambda row: self._office_priority(row[1]))
        deduped: list[tuple[Person, Office]] = []
        seen_people: set[int] = set()
        for person, office in prioritized:
            if person.id in seen_people:
                continue
            seen_people.add(person.id)
            deduped.append((person, office))
        if limit:
            deduped = deduped[:limit]
        return deduped

    def _list_existing_public_people(self, limit: int | None = None) -> list[tuple[Person, Office]]:
        stmt = (
            select(Person, Office)
            .join(Appointment, Appointment.person_id == Person.id)
            .join(Office, Office.id == Appointment.office_id)
            .where(
                Appointment.status == "current",
                Office.level.in_(["federal", "state"]),
                Office.branch.in_(["legislative", "executive"]),
            )
            .order_by(Office.level.asc(), Office.branch.asc(), Office.chamber.asc().nullslast(), Person.full_name.asc())
        )
        rows = self.session.execute(stmt).all()
        prioritized = sorted(rows, key=lambda row: self._office_priority(row[1]))
        deduped: list[tuple[Person, Office]] = []
        seen_people: set[int] = set()
        for person, office in prioritized:
            if person.id in seen_people:
                continue
            seen_people.add(person.id)
            deduped.append((person, office))
        if limit:
            deduped = deduped[:limit]
        return deduped

    def _build_targets(self, person: Person, office: Office, year: int, news_query_variants: int = 1) -> list[dict[str, str]]:
        targets: list[dict[str, str]] = []
        official_url = self._preferred_official_url(person)
        if official_url:
            targets.append(
                {
                    "target_type": "official_website",
                    "target_name": "Official website",
                    "target_url": official_url,
                    "parser_identity": f"current_taiwan_official_y{year}_v1",
                }
            )

        for platform, url in (person.social_profiles or {}).items():
            if not url:
                continue
            targets.append(
                {
                    "target_type": "social_page",
                    "target_name": f"{platform.title()}",
                    "target_url": url,
                    "parser_identity": f"current_taiwan_social_y{year}_v1",
                }
            )

        news_queries = self._build_news_queries(person.full_name, office, year, variants=max(1, news_query_variants))
        for index, news_query in enumerate(news_queries, start=1):
            targets.append(
                {
                    "target_type": "rss_feed",
                    "target_name": f"{year} Taiwan media RSS {index}",
                    "target_url": build_google_news_rss_url(news_query),
                    "parser_identity": f"google_news_taiwan_y{year}_v2_q{index}",
                }
            )

        if office.branch == "legislative":
            cspan_query = f"\"{person.full_name}\" Taiwan"
            targets.append(
                {
                    "target_type": "cspan_search_target",
                    "target_name": f"{year} C-SPAN Taiwan",
                    "target_url": build_cspan_search_url(cspan_query),
                    "parser_identity": f"cspan_taiwan_y{year}_v1",
                }
            )
        return targets

    def _preferred_official_url(self, person: Person) -> str | None:
        for candidate in [person.canonical_official_url, person.source_url]:
            if candidate and "wikipedia.org" not in candidate.lower():
                return candidate
        return None

    def _build_news_queries(self, full_name: str, office: Office, year: int, variants: int = 1) -> list[str]:
        office_hint = office.office_name
        if office.chamber == "senate":
            office_hint = "U.S. Senator"
        elif office.chamber == "house":
            office_hint = "U.S. Representative"
        elif office.branch == "executive":
            office_hint = office.office_name
        base = f"after:{year}-01-01 before:{year + 1}-01-01"
        queries = [
            f"\"{full_name}\" Taiwan {office_hint} {base}",
            f"\"{full_name}\" (Taiwan OR Taipei) {office_hint} {base}",
            f"\"{full_name}\" (Taiwan OR \"Republic of China\") {base}",
        ]
        return queries[:variants]

    def _office_priority(self, office: Office) -> tuple[int, int, str]:
        if office.branch == "legislative" and office.chamber == "senate":
            return (0, 0, office.office_name)
        if office.branch == "legislative" and office.chamber == "house":
            return (0, 1, office.office_name)
        if office.branch == "executive":
            return (1, 0, office.office_name)
        return (2, 0, office.office_name)
