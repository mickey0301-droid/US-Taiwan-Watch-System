from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import HistoricalRoster, Office, Person, RosterMembership


PRESIDENTIAL_TERMS = [
    {"ordinal": 41, "president_name": "George H. W. Bush", "label": "Bush (41)", "start_date": date(1989, 1, 20), "end_date": date(1993, 1, 20)},
    {"ordinal": 42, "president_name": "Bill Clinton", "label": "Clinton", "start_date": date(1993, 1, 20), "end_date": date(2001, 1, 20)},
    {"ordinal": 43, "president_name": "George W. Bush", "label": "George W. Bush", "start_date": date(2001, 1, 20), "end_date": date(2009, 1, 20)},
    {"ordinal": 44, "president_name": "Barack Obama", "label": "Obama", "start_date": date(2009, 1, 20), "end_date": date(2017, 1, 20)},
    {"ordinal": 45, "president_name": "Donald Trump", "label": "Trump (45)", "start_date": date(2017, 1, 20), "end_date": date(2021, 1, 20)},
    {"ordinal": 46, "president_name": "Joe Biden", "label": "Biden", "start_date": date(2021, 1, 20), "end_date": date(2025, 1, 20)},
    {"ordinal": 47, "president_name": "Donald Trump", "label": "Trump (47)", "start_date": date(2025, 1, 20), "end_date": None},
]


class RosterService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def seed_default_rosters(self) -> None:
        for congress_number in range(101, 120):
            start_year = 1789 + ((congress_number - 1) * 2)
            self.get_or_create_roster(
                roster_type="congress",
                roster_key=f"congress_{congress_number}",
                label=f"{congress_number}th Congress",
                ordinal_number=congress_number,
                start_date=date(start_year, 1, 3),
                end_date=date(start_year + 2, 1, 3),
                source_url="https://www.congress.gov/",
                source_type="official",
                parser_identity="congress_roster_seed_v1",
            )

        for term in PRESIDENTIAL_TERMS:
            self.get_or_create_roster(
                roster_type="presidential_term",
                roster_key=f"presidential_term_{term['ordinal']}",
                label=term["label"],
                ordinal_number=term["ordinal"],
                president_name=term["president_name"],
                start_date=term["start_date"],
                end_date=term["end_date"],
                source_url="https://www.whitehouse.gov/",
                source_type="official",
                parser_identity="presidential_term_seed_v1",
            )

    def get_or_create_roster(
        self,
        roster_type: str,
        roster_key: str,
        label: str,
        ordinal_number: int | None = None,
        president_name: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        source_url: str | None = None,
        source_type: str | None = None,
        parser_identity: str | None = None,
    ) -> HistoricalRoster:
        stmt = select(HistoricalRoster).where(HistoricalRoster.roster_type == roster_type, HistoricalRoster.roster_key == roster_key)
        roster = self.session.execute(stmt).scalar_one_or_none()
        if roster:
            roster.label = label or roster.label
            roster.ordinal_number = ordinal_number or roster.ordinal_number
            roster.president_name = president_name or roster.president_name
            roster.start_date = start_date or roster.start_date
            roster.end_date = end_date or roster.end_date
            roster.source_url = source_url or roster.source_url
            roster.source_type = source_type or roster.source_type
            roster.parser_identity = parser_identity or roster.parser_identity
            return roster

        roster = HistoricalRoster(
            roster_type=roster_type,
            roster_key=roster_key,
            label=label,
            ordinal_number=ordinal_number,
            president_name=president_name,
            start_date=start_date,
            end_date=end_date,
            source_url=source_url,
            source_type=source_type,
            parser_identity=parser_identity,
        )
        self.session.add(roster)
        self.session.flush()
        return roster

    def get_roster(self, roster_key: str) -> HistoricalRoster | None:
        return self.session.execute(select(HistoricalRoster).where(HistoricalRoster.roster_key == roster_key)).scalar_one_or_none()

    def ensure_membership(
        self,
        roster: HistoricalRoster,
        person: Person,
        office: Office | None,
        jurisdiction_id: int | None,
        role_title: str,
        party: str | None,
        status: str | None,
        source_url: str | None,
        source_type: str | None,
        parser_identity: str | None,
        raw_payload: dict | None = None,
    ) -> bool:
        stmt = select(RosterMembership).where(
            RosterMembership.roster_id == roster.id,
            RosterMembership.person_id == person.id,
            RosterMembership.office_id == (office.id if office else None),
            RosterMembership.role_title == role_title,
        )
        membership = self.session.execute(stmt).scalars().first()
        if membership:
            membership.party = party or membership.party
            membership.status = status or membership.status
            membership.source_url = source_url or membership.source_url
            membership.source_type = source_type or membership.source_type
            membership.parser_identity = parser_identity or membership.parser_identity
            membership.raw_payload = raw_payload or membership.raw_payload
            return False

        self.session.add(
            RosterMembership(
                roster_id=roster.id,
                person_id=person.id,
                office_id=office.id if office else None,
                jurisdiction_id=jurisdiction_id,
                role_title=role_title,
                party=party,
                status=status,
                source_url=source_url,
                source_type=source_type,
                parser_identity=parser_identity,
                raw_payload=raw_payload,
            )
        )
        return True

    def current_congress_roster(self) -> HistoricalRoster:
        current_year = datetime.utcnow().year
        congress_number = ((current_year - 1789) // 2) + 1
        congress_number = max(101, min(119, congress_number))
        self.seed_default_rosters()
        roster = self.get_roster(f"congress_{congress_number}")
        if roster is None:
            raise RuntimeError("Current Congress roster seed not found.")
        return roster

    def presidential_term_roster(self, ordinal: int) -> HistoricalRoster:
        self.seed_default_rosters()
        roster = self.get_roster(f"presidential_term_{ordinal}")
        if roster is None:
            raise RuntimeError(f"Presidential term {ordinal} roster seed not found.")
        return roster

    def congress_rosters_for_years_text(self, years_text: str) -> list[HistoricalRoster]:
        self.seed_default_rosters()
        years = [int(value) for value in re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", years_text or "")]
        if not years:
            return []
        min_year = min(years)
        max_year = max(years)
        stmt = select(HistoricalRoster).where(HistoricalRoster.roster_type == "congress").order_by(HistoricalRoster.ordinal_number.asc())
        rosters = self.session.execute(stmt).scalars().all()
        matches: list[HistoricalRoster] = []
        for roster in rosters:
            if not roster.start_date:
                continue
            roster_start_year = roster.start_date.year
            roster_end_year = (roster.end_date.year if roster.end_date else roster_start_year)
            if roster_end_year >= min_year and roster_start_year <= max_year:
                matches.append(roster)
        return matches
