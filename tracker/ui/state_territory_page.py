from __future__ import annotations

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracker.config import use_google_sheet_primary_mode
from tracker.db import session_scope
from tracker.models import (
    Appointment,
    Jurisdiction,
    Legislation,
    LegislationSponsor,
    Office,
    Person,
    Statement,
    StatementParticipant,
)
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.statements_service import StatementsService
from tracker.ui import dashboard


def _is_valid_region_option(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if len(text) == 1 and text.isalpha():
        return False
    return True


def render(lang: str, labels: dict[str, str]) -> None:
    title = "州/海外領地" if lang == "zh-TW" else "State / Territory"
    selector_label = "選擇州或海外領地" if lang == "zh-TW" else "Select state or territory"
    st.header(title)

    if use_google_sheet_primary_mode():
        if _render_google_sheet(lang, selector_label):
            return
        st.warning("目前無可用的州/海外領地資料。" if lang == "zh-TW" else "No state/territory data available.")
        return

    with session_scope() as session:
        region_options = _list_regions_db(session)
    if not region_options:
        st.warning("目前無可用的州/海外領地資料。" if lang == "zh-TW" else "No state/territory data available.")
        return

    selected_region = st.selectbox(selector_label, region_options, key="state-territory-select-db")
    _render_database_view(selected_region=selected_region, lang=lang)


def _render_database_view(selected_region: str, lang: str) -> None:
    with session_scope() as session:
        statements_service = StatementsService(session)
        jurisdiction_ids = {
            row[0]
            for row in session.execute(select(Jurisdiction.id).where(Jurisdiction.name == selected_region)).all()
            if row[0]
        }
        person_categories = _build_region_person_categories_db(session, jurisdiction_ids)
        selected_person_ids = set(person_categories.keys())
        chinese_aliases = dashboard._build_chinese_alias_map(session, selected_person_ids)

        recent_statements = (
            session.execute(
                select(Statement)
                .order_by(Statement.date_published.desc().nullslast(), Statement.date_collected.desc(), Statement.id.desc())
                .limit(500)
            )
            .scalars()
            .all()
        )
        statement_ids = [item.id for item in recent_statements]
        participant_rows = (
            session.execute(select(StatementParticipant).where(StatementParticipant.statement_id.in_(statement_ids))).scalars().all()
            if statement_ids
            else []
        )
        statement_participants_map: dict[int, list[int]] = {}
        for row in participant_rows:
            statement_participants_map.setdefault(row.statement_id, []).append(row.person_id)

        people_by_id = (
            {
                person.id: person
                for person in session.execute(select(Person).where(Person.id.in_(selected_person_ids))).scalars().all()
            }
            if selected_person_ids
            else {}
        )

        event_buckets = _empty_event_buckets()
        seen_statement_ids = {key: set() for key in event_buckets}
        for statement in recent_statements:
            if all(len(items) >= 3 for items in event_buckets.values()):
                break
            participant_ids = statement_participants_map.get(statement.id) or ([statement.person_id] if statement.person_id else [])
            categories = set()
            for person_id in participant_ids:
                for category in person_categories.get(person_id, set()):
                    categories.add(category)
            if not categories:
                continue
            participants = []
            for person_id in participant_ids:
                person = people_by_id.get(person_id)
                if not person or not person.full_name:
                    continue
                if any(item["person_id"] == person_id for item in participants):
                    continue
                chinese_name = chinese_aliases.get(person_id) if lang == "zh-TW" else ""
                display_name = chinese_name or person.full_name
                participants.append(
                    {
                        "person_id": person_id,
                        "display_name": display_name,
                        "english_name": person.full_name,
                        "chinese_name": chinese_name,
                    }
                )
            item = {
                "statement_id": statement.id,
                "title": statement.title,
                "description": statement.excerpt or statement.title or statement.source_url,
                "event_time": statement.date_published or statement.date_collected,
                "participants": participants,
                "sources": statements_service.list_sources_for_statement(statement.id),
                "representative_source_url": statement.source_url,
            }
            for category in categories:
                if len(event_buckets[category]) >= 3:
                    continue
                if statement.id in seen_statement_ids[category]:
                    continue
                seen_statement_ids[category].add(statement.id)
                event_buckets[category].append(item)

        federal_person_ids = {
            person_id
            for person_id, categories in person_categories.items()
            if "federal_senators" in categories or "federal_house" in categories
        }
        federal_legislation_rows = (
            session.execute(
                select(Legislation)
                .options(selectinload(Legislation.sponsors).selectinload(LegislationSponsor.person))
                .join(LegislationSponsor, LegislationSponsor.legislation_id == Legislation.id)
                .where(
                    Legislation.is_taiwan_related.is_(True),
                    Legislation.level == "federal",
                    LegislationSponsor.person_id.in_(federal_person_ids if federal_person_ids else {-1}),
                )
                .order_by(Legislation.last_action_date.desc().nullslast(), Legislation.introduced_date.desc().nullslast(), Legislation.id.desc())
                .limit(200)
            )
            .scalars()
            .unique()
            .all()
            if federal_person_ids
            else []
        )
        state_legislation_rows = (
            session.execute(
                select(Legislation)
                .options(selectinload(Legislation.sponsors).selectinload(LegislationSponsor.person))
                .where(
                    Legislation.is_taiwan_related.is_(True),
                    Legislation.level == "state",
                    (
                        (Legislation.jurisdiction_id.in_(jurisdiction_ids))
                        if jurisdiction_ids
                        else (Legislation.jurisdiction_name == selected_region)
                    ),
                )
                .order_by(Legislation.last_action_date.desc().nullslast(), Legislation.introduced_date.desc().nullslast(), Legislation.id.desc())
                .limit(200)
            )
            .scalars()
            .all()
        )

        federal_legislation = dashboard._bucket_recent_legislation_db(federal_legislation_rows, session=session, lang=lang).get("federal_legislation", [])
        state_legislation = dashboard._bucket_recent_legislation_db(state_legislation_rows, session=session, lang=lang).get("state_legislation", [])

    _render_sections(
        lang=lang,
        federal_legislation=federal_legislation,
        state_legislation=state_legislation,
        event_buckets=event_buckets,
    )


def _render_google_sheet(lang: str, selector_label: str) -> bool:
    sheet = GoogleSheetReadService()
    people = sheet.list_people()
    events = sheet.list_events()
    legislation = sheet.list_legislation()
    if not (people or events or legislation):
        return False

    region_options = sorted(
        {
            str(item.get("jurisdiction") or "").strip()
            for item in people
            if _is_valid_region_option(item.get("jurisdiction"))
        }
    )
    if not region_options:
        return False
    selected_region = st.selectbox(selector_label, region_options, key="state-territory-select-sheet")

    people_by_id = {int(item.get("person_id")): item for item in people if item.get("person_id")}
    person_categories = _build_region_person_categories_sheet(people, selected_region)
    federal_person_ids = {
        person_id
        for person_id, categories in person_categories.items()
        if "federal_senators" in categories or "federal_house" in categories
    }

    event_buckets = _empty_event_buckets()
    seen_event_ids = {key: set() for key in event_buckets}
    for item in events:
        if all(len(entries) >= 3 for entries in event_buckets.values()):
            break
        participant_ids = [int(value) for value in list(item.get("participant_ids_list") or []) if value]
        categories = set()
        for person_id in participant_ids:
            for category in person_categories.get(person_id, set()):
                categories.add(category)
        if not categories:
            continue
        event_data = {
            "event_id": item.get("event_id"),
            "title": str(item.get("title") or ""),
            "description": str(item.get("summary") or item.get("title") or ""),
            "event_time": item.get("event_date_date"),
            "participants": dashboard._participants_from_sheet(item, lang=lang, people_by_id=people_by_id),
            "sources": item.get("source_urls") or [],
            "representative_source_url": None,
        }
        for category in categories:
            if len(event_buckets[category]) >= 3:
                continue
            event_id = event_data["event_id"]
            if event_id in seen_event_ids[category]:
                continue
            seen_event_ids[category].add(event_id)
            event_buckets[category].append(event_data)

    federal_legislation_rows = []
    state_legislation_rows = []
    for item in legislation:
        sponsor_ids = [int(value) for value in list(item.get("sponsor_ids_list") or []) if value]
        jurisdiction = str(item.get("jurisdiction") or "").strip()
        scope = str(item.get("scope") or "").strip().lower()
        if sponsor_ids and any(sid in federal_person_ids for sid in sponsor_ids):
            federal_legislation_rows.append(item)
        if jurisdiction == selected_region or scope == "state":
            if jurisdiction == selected_region:
                state_legislation_rows.append(item)

    federal_legislation = _sheet_legislation_entries(federal_legislation_rows, lang=lang, level="federal")
    state_legislation = _sheet_legislation_entries(state_legislation_rows, lang=lang, level="state")

    _render_sections(
        lang=lang,
        federal_legislation=federal_legislation,
        state_legislation=state_legislation,
        event_buckets=event_buckets,
    )
    return True


def _sheet_legislation_entries(rows: list[dict[str, object]], lang: str, level: str) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for item in rows:
        if len(entries) >= 3:
            break
        entries.append(
            {
                "title": str(item.get("title") or ""),
                "summary": str(item.get("summary") or ""),
                "bill_number": str(item.get("bill_number") or ""),
                "level": level,
                "chamber": str(item.get("chamber") or ""),
                "jurisdiction_name": str(item.get("jurisdiction") or ""),
                "introduced_date": item.get("date_date"),
                "date": item.get("date_date"),
                "source_url": str(item.get("official_page") or ""),
                "sponsor": dashboard._first_sheet_sponsor(item, lang=lang),
            }
        )
    return entries


def _build_region_person_categories_db(session, jurisdiction_ids: set[int]) -> dict[int, set[str]]:
    rows = session.execute(
        select(
            Appointment.person_id,
            Appointment.jurisdiction_id,
            Office.jurisdiction_id,
            Office.level,
            Office.branch,
            Office.chamber,
        )
        .join(Office, Office.id == Appointment.office_id)
        .where(Appointment.is_current.is_(True))
    ).all()
    result: dict[int, set[str]] = {}
    for person_id, appointment_jurisdiction_id, office_jurisdiction_id, level, branch, chamber in rows:
        jurisdiction_id = appointment_jurisdiction_id or office_jurisdiction_id
        if not jurisdiction_id or jurisdiction_id not in jurisdiction_ids:
            continue
        category = None
        if level == "federal" and branch == "legislative" and chamber == "senate":
            category = "federal_senators"
        elif level == "federal" and branch == "legislative" and chamber == "house":
            category = "federal_house"
        elif level == "state" and branch == "executive":
            category = "state_officials"
        elif level == "state" and branch == "legislative":
            category = "state_legislators"
        if category:
            result.setdefault(person_id, set()).add(category)
    return result


def _build_region_person_categories_sheet(people: list[dict[str, object]], selected_region: str) -> dict[int, set[str]]:
    result: dict[int, set[str]] = {}
    for person in people:
        person_id = person.get("person_id")
        if not person_id:
            continue
        if str(person.get("jurisdiction") or "").strip() != selected_region:
            continue
        level = str(person.get("level") or "").strip().lower()
        branch = str(person.get("branch") or "").strip().lower()
        office_title = str(person.get("office_title") or "").lower()
        category = None
        if level == "federal" and branch == "legislative":
            if "senator" in office_title or "senate" in office_title:
                category = "federal_senators"
            elif "representative" in office_title or "house" in office_title:
                category = "federal_house"
        elif level == "state" and branch == "executive":
            category = "state_officials"
        elif level == "state" and branch == "legislative":
            category = "state_legislators"
        if category:
            result.setdefault(int(person_id), set()).add(category)
    return result


def _list_regions_db(session) -> list[str]:
    rows = session.execute(
        select(Jurisdiction.name)
        .where(Jurisdiction.type.in_(["state", "territory", "district"]))
        .order_by(Jurisdiction.name.asc())
    ).all()
    return sorted({str(row[0]).strip() for row in rows if _is_valid_region_option(row[0])})


def _empty_event_buckets() -> dict[str, list[dict[str, object]]]:
    return {
        "federal_senators": [],
        "federal_house": [],
        "state_officials": [],
        "state_legislators": [],
    }


def _render_sections(
    lang: str,
    federal_legislation: list[dict[str, object]],
    state_legislation: list[dict[str, object]],
    event_buckets: dict[str, list[dict[str, object]]],
) -> None:
    st.subheader("法案" if lang == "zh-TW" else "Legislation")
    legislation_columns = st.columns(2)
    dashboard._render_legislation_column(
        legislation_columns[0],
        "國會立法" if lang == "zh-TW" else "Congressional Legislation",
        federal_legislation,
        lang,
    )
    dashboard._render_legislation_column(
        legislation_columns[1],
        "州議會立法" if lang == "zh-TW" else "State Legislature Legislation",
        state_legislation,
        lang,
    )

    st.subheader("事件" if lang == "zh-TW" else "Events")
    row1 = st.columns(2)
    dashboard._render_event_column(
        row1[0],
        "聯邦參議員" if lang == "zh-TW" else "U.S. Senators",
        event_buckets.get("federal_senators", []),
        lang,
        "state-territory-federal-senators",
    )
    dashboard._render_event_column(
        row1[1],
        "聯邦眾議員" if lang == "zh-TW" else "U.S. House Members",
        event_buckets.get("federal_house", []),
        lang,
        "state-territory-federal-house",
    )
    row2 = st.columns(2)
    dashboard._render_event_column(
        row2[0],
        "州政府官員" if lang == "zh-TW" else "State Officials",
        event_buckets.get("state_officials", []),
        lang,
        "state-territory-state-officials",
    )
    dashboard._render_event_column(
        row2[1],
        "州議員" if lang == "zh-TW" else "State Legislators",
        event_buckets.get("state_legislators", []),
        lang,
        "state-territory-state-legislators",
    )
