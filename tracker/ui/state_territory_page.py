from __future__ import annotations

import re

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
from tracker.ui.navigation import person_detail_anchor_html, person_detail_href

STATE_EXECUTIVE_ROLE_ZH = {
    "Governor": "州長",
    "Lieutenant Governor": "副州長",
    "Secretary of State": "州務卿",
    "Attorney General": "州檢察長",
    "Treasurer": "州財務長",
    "Comptroller": "主計長",
    "Auditor": "審計長",
    "Superintendent": "教育總監",
    "Insurance Commissioner": "保險專員",
    "Agriculture Commissioner": "農業專員",
}


def _bilingual_text(english: str | None, chinese: str | None) -> str:
    en = str(english or "").strip()
    zh = str(chinese or "").strip()
    if en and zh:
        if en == zh:
            return en
        return f"{zh} / {en}"
    return zh or en


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
        legislature_roster = _build_state_legislature_roster_db(session, jurisdiction_ids)
        executive_roster = _build_state_executive_roster_db(session, jurisdiction_ids)

    _render_sections(
        lang=lang,
        federal_legislation=federal_legislation,
        state_legislation=state_legislation,
        event_buckets=event_buckets,
        legislature_roster=legislature_roster,
        executive_roster=executive_roster,
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
    legislature_roster: dict[str, list[dict[str, str]]],
    executive_roster: list[dict[str, str]],
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

    st.subheader("名單" if lang == "zh-TW" else "Roster")
    roster_cols = st.columns(2)
    _render_state_legislature_roster(roster_cols[0], legislature_roster, lang)
    _render_state_executive_roster(roster_cols[1], executive_roster, lang)


def _build_state_legislature_roster_db(session, jurisdiction_ids: set[int]) -> dict[str, list[dict[str, str]]]:
    if not jurisdiction_ids:
        return {"senate": [], "house": []}
    rows = session.execute(
        select(
            Person.id,
            Appointment.district,
            Person.full_name,
            Appointment.party,
            Office.chamber,
        )
        .join(Office, Office.id == Appointment.office_id)
        .join(Person, Person.id == Appointment.person_id)
        .where(
            Appointment.is_current.is_(True),
            Office.level == "state",
            Office.branch == "legislative",
            Appointment.jurisdiction_id.in_(jurisdiction_ids),
        )
    ).all()

    buckets: dict[str, dict[str, dict[str, str]]] = {"senate": {}, "house": {}}
    for person_id, district, full_name, party, chamber in rows:
        chamber_key = str(chamber or "house").strip().lower()
        if chamber_key not in buckets:
            chamber_key = "house"
        district_text = str(district or "").strip()
        normalized_district = _normalize_district_sort_key(district_text)
        if normalized_district not in buckets[chamber_key]:
            buckets[chamber_key][normalized_district] = {
                "district": district_text or normalized_district,
                "person_id": str(person_id or ""),
                "name": str(full_name or "").strip(),
                "party": str(party or "").strip(),
            }

    result: dict[str, list[dict[str, str]]] = {}
    for chamber_key, by_district in buckets.items():
        result[chamber_key] = _expand_and_sort_district_rows(by_district)
    return result


def _build_state_executive_roster_db(session, jurisdiction_ids: set[int]) -> list[dict[str, str]]:
    if not jurisdiction_ids:
        return []
    rows = session.execute(
        select(
            Person.id,
            Appointment.role_title,
            Office.office_name,
            Person.full_name,
            Appointment.party,
        )
        .join(Office, Office.id == Appointment.office_id)
        .join(Person, Person.id == Appointment.person_id)
        .where(
            Appointment.is_current.is_(True),
            Office.level == "state",
            Office.branch == "executive",
            Appointment.jurisdiction_id.in_(jurisdiction_ids),
        )
    ).all()

    role_order = [
        ("governor", "Governor"),
        ("lieutenant_governor", "Lieutenant Governor"),
        ("secretary_of_state", "Secretary of State"),
        ("attorney_general", "Attorney General"),
        ("treasurer", "Treasurer"),
        ("comptroller", "Comptroller"),
        ("auditor", "Auditor"),
        ("superintendent", "Superintendent"),
        ("insurance_commissioner", "Insurance Commissioner"),
        ("agriculture_commissioner", "Agriculture Commissioner"),
    ]
    role_map: dict[str, dict[str, str]] = {}
    for person_id, role_title, office_name, full_name, party in rows:
        detected = _match_executive_role(str(role_title or ""), str(office_name or ""))
        if not detected or detected in role_map:
            continue
        role_map[detected] = {
            "role": dict(role_order).get(detected, str(role_title or office_name or "").strip()),
            "person_id": str(person_id or ""),
            "name": str(full_name or "").strip(),
            "party": str(party or "").strip(),
        }

    result: list[dict[str, str]] = []
    for key, label in role_order:
        item = role_map.get(key)
        if item:
            result.append(item)
        else:
            result.append({"role": label, "person_id": "", "name": "", "party": ""})
    return result


def _match_executive_role(role_title: str, office_name: str) -> str | None:
    text = f"{role_title} {office_name}".lower()
    if "lieutenant governor" in text:
        return "lieutenant_governor"
    if "governor" in text:
        return "governor"
    if "secretary of state" in text or "secretary of the commonwealth" in text:
        return "secretary_of_state"
    if "attorney general" in text:
        return "attorney_general"
    if "treasurer" in text:
        return "treasurer"
    if "comptroller" in text:
        return "comptroller"
    if "auditor" in text:
        return "auditor"
    if "superintendent" in text:
        return "superintendent"
    if "insurance commissioner" in text:
        return "insurance_commissioner"
    if "agriculture commissioner" in text:
        return "agriculture_commissioner"
    return None


def _normalize_district_sort_key(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    match = re.search(r"district\s*([0-9A-Za-z-]+)", text, flags=re.I)
    if match:
        return match.group(1).strip()
    return text


def _expand_and_sort_district_rows(by_district: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    keys = [k for k in by_district.keys() if k]
    numeric = [int(k) for k in keys if k.isdigit()]
    has_only_numeric = bool(keys) and len(numeric) == len(keys)

    rows: list[dict[str, str]] = []
    if has_only_numeric:
        max_num = max(numeric) if numeric else 0
        for number in range(1, max_num + 1):
            key = str(number)
            item = by_district.get(key)
            if item:
                rows.append(item)
            else:
                rows.append({"district": key, "person_id": "", "name": "", "party": ""})
        return rows

    def _sort_key(item: str) -> tuple[int, object]:
        if item.isdigit():
            return (0, int(item))
        return (1, item.lower())

    for key in sorted(keys, key=_sort_key):
        rows.append(by_district[key])
    return rows


def _name_surname_sort_key(name: str | None) -> tuple[str, str]:
    full = str(name or "").strip()
    if not full:
        return ("", "")
    parts = full.split()
    surname = parts[-1].lower() if parts else ""
    return (surname, full.lower())


def _render_state_legislature_roster(container, legislature_roster: dict[str, list[dict[str, str]]], lang: str) -> None:
    container.markdown("**州議會名單**" if lang == "zh-TW" else "**State Legislature Roster**")
    senate_rows = sorted(
        legislature_roster.get("senate", []),
        key=lambda item: _name_surname_sort_key(item.get("name")),
    )
    house_rows = legislature_roster.get("house", [])

    def _render_chamber(title_zh: str, title_en: str, rows: list[dict[str, str]], position_label_zh: str, position_label_en: str) -> None:
        title = _bilingual_text(title_en, title_zh)
        container.markdown(f"_{title}_")
        if not rows:
            container.caption("目前無資料" if lang == "zh-TW" else "No data yet")
            return
        headers = ("姓名", "部門", "職位") if lang == "zh-TW" else ("Name", "Department", "Position")
        lines = [f"| {headers[0]} | {headers[1]} | {headers[2]} |", "|---|---|---|"]

        def _clean_cell(text: str) -> str:
            return str(text or "").replace("|", "\\|").replace("\n", " ").strip()

        for item in rows:
            district = item.get("district") or ""
            name = item.get("name") or ""
            person_id = str(item.get("person_id") or "").strip()
            if name and person_id:
                member = person_detail_anchor_html(_clean_cell(name), int(person_id))
            else:
                member = _clean_cell(name)
            district_text = district or ("未填選區" if lang == "zh-TW" else "Unspecified district")
            department = title
            position = f"{_bilingual_text(position_label_en, position_label_zh)} (第{district_text}選區 / District {district_text})"
            lines.append(f"| {member} | {_clean_cell(department)} | {_clean_cell(position)} |")
        container.markdown("\n".join(lines), unsafe_allow_html=True)

    _render_chamber(
        "州參議院",
        "State Senate",
        senate_rows,
        "州參議員",
        "State Senator",
    )
    _render_chamber(
        "州眾議院",
        "State House",
        house_rows,
        "州眾議員",
        "State Representative",
    )


def _render_state_executive_roster(container, executive_roster: list[dict[str, str]], lang: str) -> None:
    container.markdown("**州政府名單**" if lang == "zh-TW" else "**State Executive Roster**")
    if not executive_roster:
        container.caption("目前無資料" if lang == "zh-TW" else "No data yet")
        return

    headers = ("姓名", "部門", "職位") if lang == "zh-TW" else ("Name", "Department", "Position")
    lines = [f"| {headers[0]} | {headers[1]} | {headers[2]} |", "|---|---|---|"]

    def _clean_cell(text: str) -> str:
        return str(text or "").replace("|", "\\|").replace("\n", " ").strip()

    for item in executive_roster:
        role = item.get("role") or ""
        name = item.get("name") or ""
        person_id = str(item.get("person_id") or "").strip()
        if name and person_id:
            official = person_detail_anchor_html(_clean_cell(name), int(person_id))
        else:
            official = _clean_cell(name)
        department = _bilingual_text("State Government", "州政府")
        role_bilingual = _bilingual_text(role, STATE_EXECUTIVE_ROLE_ZH.get(role, ""))
        lines.append(f"| {official} | {_clean_cell(department)} | {_clean_cell(role_bilingual)} |")
    container.markdown("\n".join(lines), unsafe_allow_html=True)
