from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
import re

import streamlit as st
from sqlalchemy import func, select

from tracker.config import get_settings, use_google_sheet_primary_mode
from tracker.db import session_scope
from tracker.models import (
    Alias,
    Appointment,
    Legislation,
    NotificationLog,
    Office,
    Person,
    Statement,
    StatementParticipant,
    SyncRun,
    Tracker,
)
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.statements_service import StatementsService
from tracker.services.ai_assist_service import AIAssistService
from tracker.ui.navigation import person_detail_href
from tracker.ui.source_labels import source_label


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["dashboard"])
    if use_google_sheet_primary_mode():
        if _render_google_sheet_fallback(lang, labels):
            return
        _render_metrics(
            {
                "federal_officials": 0,
                "congress_members": 0,
                "state_officials": 0,
                "state_legislators": 0,
                "federal_legislation": 0,
                "state_legislation": 0,
            },
            lang,
        )
        _render_data_source_status(lang)
        st.warning(
            "Google Sheet primary mode is enabled, but no sheet data could be loaded."
            if lang != "zh-TW"
            else "目前已啟用 Google Sheet-first 模式，但還是無法載入 Sheet 資料。"
        )
        render_google_sheet_fallback_diagnostic(lang)
        return
    dashboard_counts = {
        "federal_officials": 0,
        "congress_members": 0,
        "state_officials": 0,
        "state_legislators": 0,
        "federal_legislation": 0,
        "state_legislation": 0,
    }
    total_statements = 0
    recent_events_by_category: dict[str, list[dict[str, object]]] = _empty_event_buckets()
    recent_legislation_by_category: dict[str, list[dict[str, object]]] = _empty_legislation_buckets()

    with session_scope() as session:
        statements_service = StatementsService(session)
        dashboard_counts = _collect_dashboard_counts_db(session)
        total_statements = session.scalar(select(func.count()).select_from(Statement)) or 0
        recent_statements = (
            session.execute(
                select(Statement)
                # Dashboard should show latest Taiwan-related tracked events for all categories,
                # including federal executive officials, even when relevance scoring is 0.
                .order_by(Statement.date_published.desc().nullslast(), Statement.date_collected.desc(), Statement.id.desc())
                .limit(300)
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
        for participant in participant_rows:
            statement_participants_map.setdefault(participant.statement_id, []).append(participant.person_id)

        related_person_ids: set[int] = set()
        for statement in recent_statements:
            if statement.person_id:
                related_person_ids.add(statement.person_id)
            for person_id in statement_participants_map.get(statement.id, []):
                related_person_ids.add(person_id)
        people_by_id = (
            {
                person.id: person
                for person in session.execute(select(Person).where(Person.id.in_(related_person_ids))).scalars().all()
            }
            if related_person_ids
            else {}
        )
        chinese_alias_map = _build_chinese_alias_map(session, related_person_ids)
        person_category_map = _build_person_category_map(session, related_person_ids)
        recent_events_by_category = _bucket_recent_events_db(
            recent_statements=recent_statements,
            statement_participants_map=statement_participants_map,
            people_by_id=people_by_id,
            chinese_alias_map=chinese_alias_map,
            person_category_map=person_category_map,
            statements_service=statements_service,
            lang=lang,
        )

        legislation_rows = (
            session.execute(
                select(Legislation)
                .where(Legislation.is_taiwan_related.is_(True))
                .order_by(
                    Legislation.last_action_date.desc().nullslast(),
                    Legislation.introduced_date.desc().nullslast(),
                    Legislation.id.desc(),
                )
                .limit(300)
            )
            .scalars()
            .all()
        )
        recent_legislation_by_category = _bucket_recent_legislation_db(legislation_rows, session=session, lang=lang)

    # Cloud DB can lag behind sheet exports; prefer fresher sheet counts when available.
    dashboard_counts = _prefer_sheet_counts_if_newer(dashboard_counts)

    if sum(dashboard_counts.values()) == 0 and total_statements == 0:
        if _render_google_sheet_fallback(lang, labels):
            return
        _render_metrics(dashboard_counts, lang)
        _render_data_source_status(lang)
        st.warning(
            "The current app instance is connected to an empty database, and Google Sheet fallback is not available."
            if lang != "zh-TW"
            else "目前這個 app 讀到的是空資料庫，而且 Google Sheet fallback 也還沒有成功接上，所以首頁會先顯示 0。"
        )
        st.info(
            "Check whether this is the cloud app, and confirm GOOGLE_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_JSON are configured."
            if lang != "zh-TW"
            else "如果這是雲端版，請確認已設定 GOOGLE_SHEET_ID 與 GOOGLE_SERVICE_ACCOUNT_JSON；如果這是本機版，請確認目前 app 指向的是正確的 tracker.db。"
        )
        render_google_sheet_fallback_diagnostic(lang)
        return

    _render_metrics(dashboard_counts, lang)
    _render_data_source_status(lang)
    _render_overview_sections(
        recent_legislation_by_category=recent_legislation_by_category,
        recent_events_by_category=recent_events_by_category,
        lang=lang,
    )


def _render_google_sheet_fallback(lang: str, labels: dict[str, str]) -> bool:
    sheet_service = GoogleSheetReadService()
    people = sheet_service.list_people()
    events = sheet_service.list_events()
    legislation = sheet_service.list_legislation()
    if not (people or events or legislation):
        return False

    _render_metrics(_collect_dashboard_counts_sheet(people, legislation), lang)
    _render_data_source_status(lang, people=people, events=events, legislation=legislation)
    st.info(
        "Google Sheet fallback mode is active. The cloud app is showing exported data."
        if lang != "zh-TW"
        else "目前使用 Google Sheet fallback 模式，雲端版先顯示已匯出的資料。"
    )
    people_by_id = {int(item.get("person_id") or 0): item for item in people if item.get("person_id")}
    people_category = {pid: _sheet_person_category(item) for pid, item in people_by_id.items()}
    recent_events_by_category = _bucket_recent_events_sheet(events, people_by_id, people_category, lang=lang)
    recent_legislation_by_category = _bucket_recent_legislation_sheet(legislation, people=people, lang=lang)
    _render_overview_sections(
        recent_legislation_by_category=recent_legislation_by_category,
        recent_events_by_category=recent_events_by_category,
        lang=lang,
    )
    return True


def render_google_sheet_fallback_diagnostic(lang: str) -> None:
    sheet_service = GoogleSheetReadService()
    sheet_service.list_people()
    error_message = sheet_service.get_last_error()
    if not error_message:
        return
    st.caption(
        f"Google Sheet fallback error: {error_message}"
        if lang != "zh-TW"
        else f"Google Sheet fallback 錯誤：{error_message}"
    )


def _render_data_source_status(
    lang: str,
    people: list[dict[str, object]] | None = None,
    events: list[dict[str, object]] | None = None,
    legislation: list[dict[str, object]] | None = None,
) -> None:
    settings = get_settings()
    db_url = str(settings.database_url or "")
    db_sync_time = None
    try:
        with session_scope() as session:
            db_sync_time = session.scalar(
                select(func.max(SyncRun.ended_at)).where(SyncRun.status == "success")
            )
    except Exception:
        db_sync_time = None

    sheet_error = None
    if people is None or events is None or legislation is None:
        sheet = GoogleSheetReadService()
        people = sheet.list_people()
        events = sheet.list_events()
        legislation = sheet.list_legislation()
        sheet_error = sheet.get_last_error()

    db_sync_text = db_sync_time.strftime("%Y-%m-%d %H:%M:%S") if db_sync_time else ("未提供" if lang == "zh-TW" else "N/A")
    db_text = (
        f"資料庫：{db_url}｜最後成功同步：{db_sync_text}"
        if lang == "zh-TW"
        else f"Database: {db_url} | Last successful sync: {db_sync_text}"
    )
    st.caption(db_text)

    if sheet_error:
        st.caption(
            f"Google Sheet：不可用（{sheet_error}）"
            if lang == "zh-TW"
            else f"Google Sheet: unavailable ({sheet_error})"
        )
        return

    st.caption(
        (
            f"Google Sheet：可用（People {len(people or [])} / Events {len(events or [])} / Legislation {len(legislation or [])}）"
            if lang == "zh-TW"
            else f"Google Sheet: available (People {len(people or [])} / Events {len(events or [])} / Legislation {len(legislation or [])})"
        )
    )


def _render_metrics(counts: dict[str, int], lang: str) -> None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("聯邦官員" if lang == "zh-TW" else "Federal Officials", counts.get("federal_officials", 0))
    col2.metric("國會議員" if lang == "zh-TW" else "Members of Congress", counts.get("congress_members", 0))
    col3.metric("州政府官員" if lang == "zh-TW" else "State Officials", counts.get("state_officials", 0))
    col4.metric("州議員" if lang == "zh-TW" else "State Legislators", counts.get("state_legislators", 0))
    row2_col1, row2_col2 = st.columns(2)
    row2_col1.metric("國會法案" if lang == "zh-TW" else "Congressional Bills", counts.get("federal_legislation", 0))
    row2_col2.metric("州議會法案" if lang == "zh-TW" else "State Legislature Bills", counts.get("state_legislation", 0))


def _collect_dashboard_counts_db(session) -> dict[str, int]:
    def person_count(level: str, branch: str) -> int:
        return (
            session.scalar(
                select(func.count(func.distinct(Appointment.person_id)))
                .join(Office, Office.id == Appointment.office_id)
                .where(
                    Appointment.is_current.is_(True),
                    Office.level == level,
                    Office.branch == branch,
                )
            )
            or 0
        )

    federal_legislation = (
        session.scalar(select(func.count()).select_from(Legislation).where(Legislation.level == "federal", Legislation.is_taiwan_related.is_(True)))
        or 0
    )
    state_legislation = (
        session.scalar(select(func.count()).select_from(Legislation).where(Legislation.level == "state", Legislation.is_taiwan_related.is_(True)))
        or 0
    )
    return {
        "federal_officials": person_count("federal", "executive"),
        "congress_members": person_count("federal", "legislative"),
        "state_officials": person_count("state", "executive"),
        "state_legislators": person_count("state", "legislative"),
        "federal_legislation": federal_legislation,
        "state_legislation": state_legislation,
    }


def _collect_dashboard_counts_sheet(people: list[dict[str, object]], legislation: list[dict[str, object]]) -> dict[str, int]:
    federal_officials = 0
    congress_members = 0
    state_officials = 0
    state_legislators = 0
    for person in people:
        level = str(person.get("level") or "").strip().lower()
        branch = str(person.get("branch") or "").strip().lower()
        if level == "federal" and branch == "executive":
            federal_officials += 1
        elif level == "federal" and branch == "legislative":
            congress_members += 1
        elif level == "state" and branch == "executive":
            state_officials += 1
        elif level == "state" and branch == "legislative":
            state_legislators += 1
    federal_keys: set[str] = set()
    state_keys: set[str] = set()
    for item in legislation:
        category = _infer_sheet_legislation_category(item)
        if category == "federal":
            federal_keys.add(_sheet_legislation_identity_key(item, "federal"))
        else:
            state_keys.add(_sheet_legislation_identity_key(item, "state"))
    federal_legislation = len(federal_keys)
    state_legislation = len(state_keys)
    return {
        "federal_officials": federal_officials,
        "congress_members": congress_members,
        "state_officials": state_officials,
        "state_legislators": state_legislators,
        "federal_legislation": federal_legislation,
        "state_legislation": state_legislation,
    }


def _infer_sheet_legislation_category(item: dict[str, object]) -> str:
    level = str(item.get("level") or "").strip().lower()
    if level in {"federal", "state"}:
        return level
    jurisdiction = str(item.get("jurisdiction") or item.get("jurisdiction_name") or "").strip().lower()
    if jurisdiction in {"united states", "us", "u.s."}:
        return "federal"
    session_year = str(item.get("session_year") or item.get("session") or "").strip()
    if session_year.isdigit() and int(session_year) >= 100:
        return "federal"
    bill_number = str(item.get("bill_number") or "").strip().lower()
    federal_prefixes = ("hr ", "hres", "hjres", "hconres", "s ", "sres", "sjres", "sconres")
    if bill_number.startswith(federal_prefixes):
        return "federal"
    return "state"


def _sheet_legislation_identity_key(item: dict[str, object], category: str) -> str:
    bill_number = str(item.get("bill_number") or "").strip().lower()
    bill_number = re.sub(r"[^a-z0-9]", "", bill_number)
    title = str(item.get("title") or "").strip().lower()
    date_value = item.get("date_date")
    year_text = str(getattr(date_value, "year", "") or "")
    title_bill = _extract_bill_number_from_title_for_sheet(title)
    if category == "federal":
        session_year = str(item.get("session_year") or item.get("session") or "").strip()
        if bill_number:
            return f"fed|{session_year}|{bill_number}"
        if title_bill:
            return f"fed|{session_year}|{title_bill}"
        return f"fed|{session_year}|{title}"
    jurisdiction = str(item.get("jurisdiction") or item.get("jurisdiction_name") or "").strip().lower()
    if bill_number:
        return f"state|{jurisdiction}|{bill_number}|{year_text}"
    if title_bill:
        return f"state|{jurisdiction}|{title_bill}|{year_text}"
    return f"state|{jurisdiction}|{title}|{year_text}"


def _extract_bill_number_from_title_for_sheet(title: str) -> str:
    text = str(title or "").strip().lower()
    if not text:
        return ""
    normalized = re.sub(r"[^a-z0-9]", "", text)
    match = re.search(r"(hr|hres|hjres|hconres|s|sres|sjres|sconres)\d{1,6}", normalized)
    return match.group(0) if match else ""


def _render_overview_sections(
    recent_legislation_by_category: dict[str, list[dict[str, object]]],
    recent_events_by_category: dict[str, list[dict[str, object]]],
    lang: str,
) -> None:
    legislation_title = "法案" if lang == "zh-TW" else "Legislation"
    events_title = "事件" if lang == "zh-TW" else "Events"
    st.subheader(legislation_title)
    legislation_columns = st.columns(2)
    _render_legislation_column(
        legislation_columns[0],
        "國會立法" if lang == "zh-TW" else "Congressional Legislation",
        recent_legislation_by_category.get("federal_legislation", []),
        lang,
    )
    _render_legislation_column(
        legislation_columns[1],
        "州議會立法" if lang == "zh-TW" else "State Legislature Legislation",
        recent_legislation_by_category.get("state_legislation", []),
        lang,
    )

    st.subheader(events_title)
    event_row_1 = st.columns(2)
    _render_event_column(
        event_row_1[0],
        "聯邦官員" if lang == "zh-TW" else "Federal Officials",
        recent_events_by_category.get("federal_officials", []),
        lang,
        "federal-officials",
    )
    _render_event_column(
        event_row_1[1],
        "國會議員" if lang == "zh-TW" else "Members of Congress",
        recent_events_by_category.get("congress_members", []),
        lang,
        "congress-members",
    )
    event_row_2 = st.columns(2)
    _render_event_column(
        event_row_2[0],
        "州政府官員" if lang == "zh-TW" else "State Officials",
        recent_events_by_category.get("state_officials", []),
        lang,
        "state-officials",
    )
    _render_event_column(
        event_row_2[1],
        "州議員" if lang == "zh-TW" else "State Legislators",
        recent_events_by_category.get("state_legislators", []),
        lang,
        "state-legislators",
    )


def _format_event_time(value: datetime | date | None, lang: str) -> str:
    if value is None:
        return "未提供" if lang == "zh-TW" else "Not available"
    return value.strftime("%Y-%m-%d")


def _empty_event_buckets() -> dict[str, list[dict[str, object]]]:
    return {
        "federal_officials": [],
        "congress_members": [],
        "state_officials": [],
        "state_legislators": [],
    }


def _empty_legislation_buckets() -> dict[str, list[dict[str, object]]]:
    return {
        "federal_legislation": [],
        "state_legislation": [],
    }


def _build_person_category_map(session, person_ids: set[int]) -> dict[int, str]:
    if not person_ids:
        return {}
    rows = session.execute(
        select(Appointment.person_id, Office.level, Office.branch)
        .join(Office, Office.id == Appointment.office_id)
        .where(
            Appointment.is_current.is_(True),
            Appointment.person_id.in_(person_ids),
        )
    ).all()
    by_person: dict[int, list[tuple[str | None, str | None]]] = {}
    for person_id, level, branch in rows:
        by_person.setdefault(person_id, []).append((level, branch))

    result: dict[int, str] = {}
    for person_id, offices in by_person.items():
        if any(level == "federal" and branch == "executive" for level, branch in offices):
            result[person_id] = "federal_officials"
        elif any(level == "federal" and branch == "legislative" for level, branch in offices):
            result[person_id] = "congress_members"
        elif any(level == "state" and branch == "executive" for level, branch in offices):
            result[person_id] = "state_officials"
        elif any(level == "state" and branch == "legislative" for level, branch in offices):
            result[person_id] = "state_legislators"
    return result


def _build_chinese_alias_map(session, person_ids: set[int]) -> dict[int, str]:
    if not person_ids:
        return {}
    rows = session.execute(
        select(Alias.person_id, Alias.alias, Alias.id)
        .where(
            Alias.person_id.in_(person_ids),
            Alias.alias_type == "chinese_name",
            Alias.is_current.is_(True),
        )
        .order_by(Alias.id.asc())
    ).all()
    result: dict[int, str] = {}
    for person_id, alias, _alias_id in rows:
        alias_text = str(alias or "").strip()
        if not alias_text or person_id in result:
            continue
        result[person_id] = alias_text
    return result


def _bucket_recent_events_db(
    recent_statements: list[Statement],
    statement_participants_map: dict[int, list[int]],
    people_by_id: dict[int, Person],
    chinese_alias_map: dict[int, str],
    person_category_map: dict[int, str],
    statements_service: StatementsService,
    lang: str,
) -> dict[str, list[dict[str, object]]]:
    buckets = _empty_event_buckets()
    seen_statement_ids = {key: set() for key in buckets.keys()}
    for statement in recent_statements:
        if all(len(items) >= 3 for items in buckets.values()):
            break
        if _is_test_event(statement.title):
            continue
        participant_ids = statement_participants_map.get(statement.id) or ([statement.person_id] if statement.person_id else [])
        categories = {
            person_category_map.get(person_id)
            for person_id in participant_ids
            if person_id and person_category_map.get(person_id)
        }
        if not categories:
            categories = _infer_categories_from_statement(statement, participants=[people_by_id.get(pid) for pid in participant_ids if pid])
        if not categories:
            continue
        participants: list[dict[str, object]] = []
        for person_id in participant_ids:
            person = people_by_id.get(person_id)
            if not person or not person.full_name:
                continue
            if any(item["person_id"] == person_id for item in participants):
                continue
            chinese_name = chinese_alias_map.get(person_id) if lang == "zh-TW" else None
            display_name = chinese_name
            if not display_name:
                display_name = person.full_name
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
            if len(buckets[category]) >= 3:
                continue
            if statement.id in seen_statement_ids[category]:
                continue
            seen_statement_ids[category].add(statement.id)
            buckets[category].append(item)
    return buckets


def _bucket_recent_legislation_db(rows: list[Legislation], session, lang: str) -> dict[str, list[dict[str, object]]]:
    buckets = _empty_legislation_buckets()
    from tracker.ui import legislation_page as legislation_page_ui

    rows = legislation_page_ui._dedupe_db_legislation_rows(rows)
    sponsor_ids: set[int] = set()
    for row in rows:
        sponsor_people = [item.person for item in row.sponsors if item.person and str(item.role or "").lower() == "sponsor"]
        if not sponsor_people:
            sponsor_people = [item.person for item in row.sponsors if item.person]
        if sponsor_people:
            sponsor_ids.add(sponsor_people[0].id)
    chinese_alias_map = _build_chinese_alias_map(session, sponsor_ids) if lang == "zh-TW" else {}
    for row in rows:
        category = None
        if (row.level or "").lower() == "federal":
            category = "federal_legislation"
        elif (row.level or "").lower() == "state":
            category = "state_legislation"
        if not category or len(buckets[category]) >= 3:
            continue
        sponsor_rel = next((item for item in row.sponsors if item.person and str(item.role or "").lower() == "sponsor"), None)
        if sponsor_rel is None:
            sponsor_rel = next((item for item in row.sponsors if item.person), None)
        sponsor = sponsor_rel.person if sponsor_rel else None

        cosponsor_people = [
            item.person
            for item in row.sponsors
            if item.person and str(item.role or "").lower() == "cosponsor"
        ]

        sponsor_name = None
        sponsor_english_name = sponsor.full_name if sponsor and sponsor.full_name else ""
        sponsor_chinese_name = ""
        if sponsor and sponsor.full_name:
            sponsor_name = chinese_alias_map.get(sponsor.id) if lang == "zh-TW" else None
            sponsor_chinese_name = sponsor_name or ""
            if not sponsor_name:
                sponsor_name = sponsor.full_name
        buckets[category].append(
            {
                "title": row.title,
                "summary": row.summary or "",
                "bill_number": row.bill_number,
                "level": row.level,
                "chamber": row.chamber,
                "jurisdiction_name": row.jurisdiction_name,
                "introduced_date": row.introduced_date,
                "date": row.last_action_date or row.introduced_date,
                "source_url": row.source_url,
                "source_urls": legislation_page_ui._collect_db_source_links(row),
                "raw_payload": row.raw_payload or {},
                "sponsor": (
                    {
                        "person_id": sponsor.id,
                        "display_name": sponsor_name,
                        "english_name": sponsor_english_name,
                        "chinese_name": sponsor_chinese_name,
                    }
                    if sponsor and sponsor_name
                    else None
                ),
                "cosponsors": [
                    {
                        "person_id": person.id,
                        "display_name": (chinese_alias_map.get(person.id) if lang == "zh-TW" else person.full_name) or person.full_name,
                        "english_name": person.full_name,
                        "chinese_name": chinese_alias_map.get(person.id, "") if lang == "zh-TW" else "",
                    }
                    for person in cosponsor_people
                ],
            }
        )
    return buckets


def _sheet_person_category(person: dict[str, object]) -> str | None:
    level = str(person.get("level") or "").strip().lower()
    branch = str(person.get("branch") or "").strip().lower()
    if level == "federal" and branch == "executive":
        return "federal_officials"
    if level == "federal" and branch == "legislative":
        return "congress_members"
    if level == "state" and branch == "executive":
        return "state_officials"
    if level == "state" and branch == "legislative":
        return "state_legislators"
    return None


def _bucket_recent_events_sheet(
    events: list[dict[str, object]],
    people_by_id: dict[int, dict[str, object]],
    people_category: dict[int, str | None],
    lang: str,
) -> dict[str, list[dict[str, object]]]:
    buckets = _empty_event_buckets()
    seen_event_ids = {key: set() for key in buckets.keys()}
    for item in events:
        if all(len(entries) >= 3 for entries in buckets.values()):
            break
        if _is_test_event(str(item.get("title") or "")):
            continue
        participant_ids = list(item.get("participant_ids_list") or [])
        categories = {
            people_category.get(int(person_id))
            for person_id in participant_ids
            if people_category.get(int(person_id))
        }
        if not categories:
            categories = _infer_categories_from_sheet_event(item)
        if not categories:
            continue
        event_data = {
            "event_id": item.get("event_id"),
            "title": str(item.get("title") or ""),
            "description": str(item.get("summary") or item.get("title") or ""),
            "event_time": item.get("event_date_date"),
            "participants": _participants_from_sheet(item, lang=lang, people_by_id=people_by_id),
            "sources": item.get("source_urls") or [],
            "representative_source_url": None,
        }
        for category in categories:
            if not category or len(buckets[category]) >= 3:
                continue
            if event_data["event_id"] in seen_event_ids[category]:
                continue
            seen_event_ids[category].add(event_data["event_id"])
            buckets[category].append(event_data)
    return buckets


def _bucket_recent_legislation_sheet(rows: list[dict[str, object]], people: list[dict[str, object]], lang: str) -> dict[str, list[dict[str, object]]]:
    buckets = _empty_legislation_buckets()
    from tracker.ui import legislation_page as legislation_page_ui

    rows = legislation_page_ui._dedupe_sheet_legislation_rows(rows)
    person_lookup = _build_sheet_person_lookup(people)
    for item in rows:
        level = str(item.get("level") or "").strip().lower()
        category = None
        if level == "federal":
            category = "federal_legislation"
        elif level == "state":
            category = "state_legislation"
        if not category:
            jurisdiction = str(item.get("jurisdiction") or item.get("jurisdiction_name") or "").strip().lower()
            category = "federal_legislation" if jurisdiction in {"united states", "us", "u.s."} else "state_legislation"
        if len(buckets[category]) >= 3:
            continue
        buckets[category].append(
            {
                "title": str(item.get("title") or ""),
                "summary": str(item.get("summary") or ""),
                "bill_number": str(item.get("bill_number") or ""),
                "level": level or "",
                "chamber": str(item.get("chamber") or ""),
                "jurisdiction_name": str(item.get("jurisdiction") or item.get("jurisdiction_name") or ""),
                "introduced_date": item.get("date_date"),
                "date": item.get("date_date"),
                "source_url": str(item.get("source_url") or ""),
                "source_urls": legislation_page_ui._collect_sheet_source_links(item),
                "raw_payload": item.get("raw_payload") if isinstance(item.get("raw_payload"), dict) else {},
                "sponsor": _first_sheet_sponsor(item, person_lookup=person_lookup, lang=lang),
                "cosponsors": _remaining_sheet_cosponsors(item, person_lookup=person_lookup, lang=lang),
            }
        )
    return buckets


def _render_legislation_column(column, title: str, entries: list[dict[str, object]], lang: str) -> None:
    chamber_label = "所屬議院" if lang == "zh-TW" else "Chamber"
    sponsor_label = "提案議員" if lang == "zh-TW" else "Sponsor"
    cosponsor_label = "共同提案議員" if lang == "zh-TW" else "Cosponsor"
    introduced_label = "提案時間" if lang == "zh-TW" else "Introduced"
    empty_label = "目前無資料" if lang == "zh-TW" else "No records yet"
    with column:
        st.markdown(f"**{title}**")
        if not entries:
            st.caption(empty_label)
            return
        for index, item in enumerate(entries, start=1):
            with st.container(border=True):
                bill_number = str(item.get("bill_number") or "").strip()
                preferred_title = _select_preferred_legislation_title(
                    title=str(item.get("title") or ""),
                    source_url=str(item.get("source_url") or ""),
                    raw_payload=item.get("raw_payload") if isinstance(item.get("raw_payload"), dict) else {},
                )
                display_title = _format_legislation_title_with_description(
                    title=preferred_title,
                    summary=str(item.get("summary") or ""),
                    lang=lang,
                )
                if _should_prefix_bill_number(bill_number):
                    display_title = f"{bill_number} {display_title}".strip()
                st.markdown(f"**{index}. {display_title}**")
                chamber_text = _format_legislation_chamber(
                    level=str(item.get("level") or ""),
                    chamber=str(item.get("chamber") or ""),
                    jurisdiction_name=str(item.get("jurisdiction_name") or ""),
                    lang=lang,
                )
                st.markdown(f"`{chamber_label}`：{chamber_text}")
                sponsor = item.get("sponsor")
                if isinstance(sponsor, dict) and sponsor.get("display_name"):
                    sponsor_text = _format_people_inline([sponsor], lang)
                else:
                    sponsor_text = "未提供" if lang == "zh-TW" else "Not available"
                st.markdown(f"`{sponsor_label}`：{sponsor_text}")
                cosponsors = item.get("cosponsors") if isinstance(item.get("cosponsors"), list) else []
                cosponsors = _dedupe_people_for_display(cosponsors)
                if not cosponsors:
                    cosponsor_text = "無" if lang == "zh-TW" else "None"
                else:
                    cosponsor_text = _format_people_inline(cosponsors[:3], lang)
                if len(cosponsors) > 3:
                    extra = len(cosponsors) - 3
                    cosponsor_text = f"{cosponsor_text} 等{extra}名" if lang == "zh-TW" else f"{cosponsor_text} and {extra} more"
                st.markdown(f"`{cosponsor_label}`：{cosponsor_text}")
                st.markdown(f"`{introduced_label}`：{_format_event_time(item.get('introduced_date'), lang)}")
                source_urls = [str(link or "").strip() for link in (item.get("source_urls") or []) if str(link or "").strip()]
                if not source_urls and item.get("source_url"):
                    source_urls = [str(item["source_url"]).strip()]
                if source_urls:
                    st.markdown(" | ".join(f"[link]({link})" for link in source_urls[:6]))


def _should_prefix_bill_number(bill_number: str) -> bool:
    text = str(bill_number or "").strip()
    if not text:
        return False
    # Prefix only for real bill identifiers; skip descriptive titles such as
    # "Blue Skies for Taiwan Act of 2026" stored in bill_number field.
    normalized = re.sub(r"[^a-z0-9]", "", text.lower())
    return bool(
        re.match(
            r"^(hr|hres|hjres|hconres|s|sres|sjres|sconres|hb|sb|ab|ac|ajr|scr|hcr|sr|jr)\d+$",
            normalized,
        )
    )


def _first_sheet_sponsor(item: dict[str, object], person_lookup: dict[str, int], lang: str) -> dict[str, object] | None:
    sponsor_ids = list(item.get("sponsor_ids_list") or [])
    sponsor_names = list(item.get("sponsors_en_list") or [])
    sponsor_names_zh = list(item.get("sponsors_zh_list") or [])
    if not sponsor_names:
        return None
    name = str(sponsor_names[0] or "").strip()
    if not name:
        return None
    zh_name = str(sponsor_names_zh[0] or "").strip() if sponsor_names_zh else ""
    display_name = zh_name if (lang == "zh-TW" and zh_name) else name
    person_id = sponsor_ids[0] if sponsor_ids else None
    if person_id is None:
        person_id = _resolve_sheet_person_id(name, zh_name, person_lookup)
    return {
        "person_id": person_id,
        "display_name": display_name,
        "english_name": name,
        "chinese_name": zh_name,
    }


def _remaining_sheet_cosponsors(item: dict[str, object], person_lookup: dict[str, int], lang: str) -> list[dict[str, object]]:
    sponsor_ids = list(item.get("sponsor_ids_list") or [])
    sponsor_names = list(item.get("sponsors_en_list") or [])
    sponsor_names_zh = list(item.get("sponsors_zh_list") or [])
    results: list[dict[str, object]] = []
    for i in range(1, len(sponsor_names)):
        name = str(sponsor_names[i] or "").strip()
        if not name:
            continue
        zh_name = str(sponsor_names_zh[i] or "").strip() if i < len(sponsor_names_zh) else ""
        display_name = zh_name if (lang == "zh-TW" and zh_name) else name
        person_id = sponsor_ids[i] if i < len(sponsor_ids) else None
        if person_id is None:
            person_id = _resolve_sheet_person_id(name, zh_name, person_lookup)
        results.append({
            "person_id": person_id,
            "display_name": display_name,
            "english_name": name,
            "chinese_name": zh_name,
        })
    return results


def _build_sheet_person_lookup(people: list[dict[str, object]]) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for item in people:
        person_id = item.get("person_id")
        if not person_id:
            continue
        for raw_name in (item.get("display_name_en"), item.get("full_name"), item.get("display_name_zh")):
            key = _normalize_sheet_person_name(raw_name)
            if key and key not in lookup:
                lookup[key] = int(person_id)
    return lookup


def _resolve_sheet_person_id(english_name: str, chinese_name: str, person_lookup: dict[str, int]) -> int | None:
    for raw_name in (english_name, chinese_name):
        key = _normalize_sheet_person_name(raw_name)
        if key and key in person_lookup:
            return person_lookup[key]
    return None


def _normalize_sheet_person_name(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[\s\.\,\-\(\)\'\"]+", "", text)


def _format_legislation_chamber(level: str, chamber: str, jurisdiction_name: str, lang: str) -> str:
    normalized_level = str(level or "").strip().lower()
    normalized_chamber = str(chamber or "").strip().lower()
    jurisdiction = str(jurisdiction_name or "").strip()

    chamber_name_zh = "參議院" if normalized_chamber == "senate" else "眾議院" if normalized_chamber == "house" else "議會"
    chamber_name_en = "Senate" if normalized_chamber == "senate" else "House" if normalized_chamber == "house" else "Legislature"
    if normalized_level == "federal":
        return f"聯邦{chamber_name_zh}" if lang == "zh-TW" else f"U.S. {chamber_name_en}"
    if normalized_level == "state":
        if lang == "zh-TW":
            state_zh = _translate_us_state_name_zh(jurisdiction)
            return f"{state_zh or '州'}{chamber_name_zh}"
        return f"{jurisdiction or 'State'} {chamber_name_en}"
    return chamber_name_zh if lang == "zh-TW" else chamber_name_en


def _format_legislation_title_with_description(title: str, summary: str, lang: str) -> str:
    title_text = str(title or "").strip()
    if not title_text:
        return ""
    if lang != "zh-TW":
        return title_text

    summary_text = str(summary or "").strip()
    english_title = _extract_english_legislation_title(title_text)
    quoted_chinese = _extract_quoted_chinese_title(title_text) or _extract_quoted_chinese_title(summary_text)

    chinese_title_raw = _localize_legislation_text(
        bill_number="",
        title=title_text,
        summary=summary_text or title_text,
        latest_action="",
        lang=lang,
    ).strip()
    chinese_title = _clean_legislation_title_text(chinese_title_raw, fallback_title=title_text)
    if not chinese_title:
        chinese_title = _clean_legislation_title_text(_translate_event_text(title_text, lang).strip(), fallback_title=title_text)
    chinese_title = _normalize_chinese_legislation_title(chinese_title, title_text, quoted_chinese)
    if re.match(r"^[A-Za-z0-9]", chinese_title):
        recentered = _extract_mixed_chinese_title_candidate(chinese_title)
        if recentered:
            chinese_title = recentered
    if re.match(r"^[A-Za-z0-9]", chinese_title):
        fallback_zh = _translate_english_legislation_title(english_title)
        if fallback_zh:
            chinese_title = fallback_zh

    # Keep title strictly in "中文標題（English title）" format.
    if english_title:
        return f"{chinese_title}（{english_title}）"
    return chinese_title


def _extract_english_legislation_title(title_text: str) -> str:
    text = str(title_text or "").strip()
    if not text:
        return ""
    text = re.sub(r"《[^》]{1,120}》", " ", text)
    text = re.sub(r"[台臺]灣\s*'s", "Taiwan's", text)
    text = re.sub(r"中國\s*'s", "China's", text)
    text = re.sub(r"[\u4e00-\u9fff]+", " ", text)
    text = re.sub(r"[（(]\s*[\u4e00-\u9fff][^）)]{0,120}[）)]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -:：")
    text = re.sub(r"^(?:H\.?\s*R\.?|S\.?)\s*\d+\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\(\s*([A-Za-z][^)]{6,})\s*\)\s*$", r" (\1)", text).strip()
    # If title became "A (A)" keep one copy.
    dup = re.match(r"^(?P<t>.+?)\s*[（(]\s*(?P=t)\s*[）)]$", text, flags=re.IGNORECASE)
    if dup:
        text = dup.group("t").strip()
    return text


def _extract_quoted_chinese_title(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    match = re.search(r"《([^》]{2,120})》", raw)
    if match:
        return match.group(1).strip()
    return ""


def _normalize_chinese_legislation_title(chinese_title: str, title_text: str, quoted_chinese: str) -> str:
    if quoted_chinese:
        return quoted_chinese

    mixed_candidate = _extract_mixed_chinese_title_candidate(title_text)
    if mixed_candidate:
        return mixed_candidate

    cleaned = str(chinese_title or "").strip()
    cleaned = re.sub(r"[A-Za-z][A-Za-z\s,'\.-]{8,}", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ：:，,。.")
    cjk_chunks = re.findall(r"[\u4e00-\u9fff][\u4e00-\u9fff0-9]{1,}", cleaned)
    if cjk_chunks:
        best = max(cjk_chunks, key=len).strip()
        best = re.sub(r"^年+", "", best)
        if len(best) >= 4:
            if best in {"美國", "聯邦政府"}:
                return _translate_english_legislation_title(_extract_english_legislation_title(title_text)) or "相關法案"
            return best
        if len(best) >= 2 and best not in {"台灣", "中國", "美國", "聯邦"}:
            return best

    english_title = _extract_english_legislation_title(title_text)
    translated_from_english = _translate_english_legislation_title(english_title)
    if translated_from_english:
        return translated_from_english

    return "相關法案"


def _extract_mixed_chinese_title_candidate(title_text: str) -> str:
    text = str(title_text or "").strip()
    if not text:
        return ""
    candidates = re.findall(r"(?:\d{4}年)?[\u4e00-\u9fff]{2,}(?:[\u4e00-\u9fff0-9]{0,30})", text)
    if not candidates:
        return ""
    best = max((c.strip() for c in candidates if c.strip()), key=len, default="")
    best = re.sub(r"^年+", "", best)
    if len(best) < 4:
        return ""
    return best


@lru_cache(maxsize=256)
def _translate_english_legislation_title(english_title: str) -> str:
    source = str(english_title or "").strip()
    if not source:
        return ""

    ai_output = _ai_translate_legislation_title(source)
    if ai_output:
        cleaned = _clean_legislation_title_text(ai_output, fallback_title=source)
        if len(re.findall(r"[\u4e00-\u9fff]", cleaned)) >= 4:
            return cleaned

    return _rule_based_translate_legislation_title(source)


@lru_cache(maxsize=256)
def _ai_translate_legislation_title(english_title: str) -> str | None:
    if not _looks_like_english(english_title):
        return None
    service = AIAssistService()
    if not service.enabled:
        return None
    try:
        result = service.translate_legislation_title(english_title)
    except Exception:
        return None
    return result.strip() if result else None


def _rule_based_translate_legislation_title(english_title: str) -> str:
    text = str(english_title or "").strip()
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text)
    lowered = normalized.lower()
    lowered_no_bill = re.sub(r"^(?:h\.?r\.?|s\.?)\s*\d+\s*", "", lowered).strip()

    specific_rules = [
        (
            r"^to enhance the security, resilience, and protection of critical undersea infrastructure vital to taiwan's national security, economic stability, and defense, particularly in countering gray zone tactics employed by the people's republic of china, and for other purposes\.?$",
            "強化攸關台灣國家安全、經濟穩定與防衛之關鍵海底基礎設施安全、韌性及防護法案",
        ),
        (
            r"^a bill to require the comptroller general of the united states to submit a report on the manner in which delays in arms deliveries to japan, taiwan, and the philippines affect the ability of the department of defense to build and sustain a strong denial defense in the first island chain\.?$",
            "要求美國政府問責署提交報告，評估對日本、台灣與菲律賓軍售延遲如何影響國防部在第一島鏈建立並維持強力拒止防衛能力法案",
        ),
        (
            r"^iowa general assembly friendly taiwan resolution\.?$",
            "支持台灣國際參與及美台稅務協議友好決議",
        ),
        (
            r"^indiana general assembly friendly taiwan resolution\.?$",
            "支持台灣及美台稅務協議友好決議",
        ),
    ]
    for pattern, translated in specific_rules:
        if re.fullmatch(pattern, lowered_no_bill):
            return translated

    replacements = [
        ("and for other purposes", ""),
        ("to enhance", "強化"),
        ("to support", "支持"),
        ("to improve", "提升"),
        ("to promote", "促進"),
        ("security", "安全"),
        ("resilience", "韌性"),
        ("protection", "防護"),
        ("critical undersea infrastructure", "關鍵海底基礎設施"),
        ("taiwan's", "台灣"),
        ("taiwan", "台灣"),
        ("national security", "國家安全"),
        ("economic stability", "經濟穩定"),
        ("defense", "防衛"),
        ("people's republic of china", "中華人民共和國"),
        ("gray zone tactics", "灰色地帶戰術"),
        ("act of", "法案"),
        ("act", "法案"),
        ("resolution", "決議案"),
    ]
    translated = lowered_no_bill
    for source, target in replacements:
        translated = translated.replace(source, target)
    translated = re.sub(r"\s+", " ", translated).strip(" ,.;:")
    translated = re.sub(r"^[a-z0-9\-\.\s]+", "", translated).strip()
    translated = re.sub(r"[A-Za-z][A-Za-z\s,'\.-]{2,}", " ", translated)
    translated = re.sub(r"\s+", " ", translated).strip(" ,.;:")
    if len(re.findall(r"[\u4e00-\u9fff]", translated)) < 4:
        if "taiwan" in lowered:
            return "台灣相關法案中文譯名待補"
        return "法案中文譯名待補"
    if not re.search(r"(法案|決議案)$", translated):
        translated = f"{translated}法案"
    return translated


def _select_preferred_legislation_title(title: str, source_url: str, raw_payload: dict[str, object] | None) -> str:
    title_text = str(title or "").strip()
    if not title_text:
        return ""
    raw_payload = raw_payload or {}
    candidates = [part.strip() for part in re.split(r"\s*[|｜]+\s*", title_text) if part.strip()]
    if not candidates:
        return title_text

    # If any candidate already contains a Chinese legislation title, prefer it.
    chinese_candidates = [candidate for candidate in candidates if _extract_quoted_chinese_title(candidate) or len(re.findall(r"[\u4e00-\u9fff]", candidate)) >= 4]
    if chinese_candidates:
        quoted = [candidate for candidate in chinese_candidates if _extract_quoted_chinese_title(candidate)]
        return quoted[0] if quoted else chinese_candidates[0]

    congress_url = str(raw_payload.get("congress_gov_url") or "").strip().lower()
    source_url_lower = str(source_url or "").strip().lower()
    has_congress_source = "congress.gov" in source_url_lower or "congress.gov" in congress_url
    if has_congress_source:
        # If title was merged from multiple sources, prefer the official Congress style.
        return _pick_official_title_candidate(candidates)
    return candidates[0]


def _pick_official_title_candidate(candidates: list[str]) -> str:
    def score(text: str) -> tuple[int, int]:
        lowered = text.lower()
        english_chars = len(re.findall(r"[a-z]", lowered))
        official_markers = 0
        for marker in (" act", " resolution", "a bill", "to ", "supporting", "commending", "recognizing"):
            if marker in lowered:
                official_markers += 1
        return (official_markers, english_chars)

    return max(candidates, key=score)


def _normalize_compare_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = re.sub(r"[\s\.\,\-\–\—\:\;\'\"\(\)\[\]\{\}]+", "", normalized)
    return normalized


def _clean_legislation_title_text(text: str, fallback_title: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = re.split(r"[：:]", cleaned, maxsplit=1)[0].strip()
    # Drop accidental summary fragment in title translation.
    cleaned = re.split(r"(?:旨在|此法案|本法案|法案旨在)", cleaned, maxsplit=1)[0].strip()
    fallback_norm = _normalize_compare_text(fallback_title)
    candidates = re.findall(r"（([^）]+)）", cleaned)
    for candidate in candidates:
        if _normalize_compare_text(candidate) == fallback_norm:
            cleaned = cleaned.replace(f"（{candidate}）", "").strip()
    cleaned = re.sub(r"\s*\([A-Za-z][^)]{6,}\)\s*$", "", cleaned).strip()
    # Keep normal word boundaries (especially for English title text).
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.strip("。．.")


def _clean_legislation_summary_text(text: str, title_text: str, chinese_title: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"\s+", " ", cleaned)
    for part in [title_text, chinese_title]:
        if not part:
            continue
        cleaned = cleaned.replace(part, "").strip()
    cleaned = re.sub(r"^[：:，,\-–—\s]+", "", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned).strip()
    # Drop duplicated leading title phrase in summary, e.g. "《...》旨在..."
    cleaned = re.sub(r"^《[^》]{2,80}》\s*(旨在|在|係|是)\s*", "", cleaned)
    # Also drop leading duplicated quoted title even without "旨在".
    cleaned = re.sub(r"^《[^》]{2,120}》\s*", "", cleaned)
    cleaned = re.sub(r"^《》\s*", "", cleaned)
    # Remove embedded parenthetical English title duplication in summary body.
    cleaned = re.sub(r"[（(]\s*[A-Za-z][A-Za-z\s,'\.-]{10,}[）)]", "", cleaned).strip()
    cleaned = re.sub(r"[（(]\s*[）)]", "", cleaned).strip()
    if _normalize_compare_text(cleaned) in {"", _normalize_compare_text(title_text), _normalize_compare_text(chinese_title)}:
        return ""
    if len(cleaned) > 60:
        cleaned = cleaned[:60].rstrip() + "…"
    return cleaned


def _translate_us_state_name_zh(state_name: str) -> str:
    mapping = {
        "Alabama": "阿拉巴馬州",
        "Alaska": "阿拉斯加州",
        "Arizona": "亞利桑那州",
        "Arkansas": "阿肯色州",
        "California": "加州",
        "Colorado": "科羅拉多州",
        "Connecticut": "康乃狄克州",
        "Delaware": "德拉瓦州",
        "District of Columbia": "哥倫比亞特區",
        "Florida": "佛羅里達州",
        "Georgia": "喬治亞州",
        "Hawaii": "夏威夷州",
        "Idaho": "愛達荷州",
        "Illinois": "伊利諾州",
        "Indiana": "印第安納州",
        "Iowa": "愛荷華州",
        "Kansas": "堪薩斯州",
        "Kentucky": "肯塔基州",
        "Louisiana": "路易斯安那州",
        "Maine": "緬因州",
        "Maryland": "馬里蘭州",
        "Massachusetts": "麻薩諸塞州",
        "Michigan": "密西根州",
        "Minnesota": "明尼蘇達州",
        "Mississippi": "密西西比州",
        "Missouri": "密蘇里州",
        "Montana": "蒙大拿州",
        "Nebraska": "內布拉斯加州",
        "Nevada": "內華達州",
        "New Hampshire": "新罕布夏州",
        "New Jersey": "新澤西州",
        "New Mexico": "新墨西哥州",
        "New York": "紐約州",
        "North Carolina": "北卡羅來納州",
        "North Dakota": "北達科他州",
        "Ohio": "俄亥俄州",
        "Oklahoma": "奧克拉荷馬州",
        "Oregon": "俄勒岡州",
        "Pennsylvania": "賓夕法尼亞州",
        "Rhode Island": "羅德島州",
        "South Carolina": "南卡羅來納州",
        "South Dakota": "南達科他州",
        "Tennessee": "田納西州",
        "Texas": "德州",
        "Utah": "猶他州",
        "Vermont": "佛蒙特州",
        "Virginia": "維吉尼亞州",
        "Washington": "華盛頓州",
        "West Virginia": "西維吉尼亞州",
        "Wisconsin": "威斯康辛州",
        "Wyoming": "懷俄明州",
        "Guam": "關島",
        "Puerto Rico": "波多黎各",
        "U.S. Virgin Islands": "美屬維京群島",
        "American Samoa": "美屬薩摩亞",
        "Northern Mariana Islands": "北馬里亞納群島",
    }
    raw = str(state_name or "").strip()
    if not raw:
        return ""
    return mapping.get(raw, raw)


def _render_event_column(
    column,
    title: str,
    entries: list[dict[str, object]],
    lang: str,
    key_prefix: str,
) -> None:
    empty_label = "目前無資料" if lang == "zh-TW" else "No records yet"
    with column:
        st.markdown(f"**{title}**")
        if not entries:
            st.caption(empty_label)
            return
        for index, event in enumerate(entries, start=1):
            _render_event_card(index=index, event=event, lang=lang)


def _render_event_card(index: int, event: dict[str, object], lang: str) -> None:
    time_label = "時間" if lang == "zh-TW" else "Time"
    description_label = "事件描述" if lang == "zh-TW" else "Description"
    participants_label = "參與人" if lang == "zh-TW" else "Participants"
    quoted_sources_label = "引述來源" if lang == "zh-TW" else "Quoted sources"

    with st.container(border=True):
        localized_title = _localize_event_text(
            title=str(event.get("title") or ""),
            description=str(event.get("description") or ""),
            lang=lang,
            is_title=True,
        )
        localized_description = _localize_event_text(
            title=str(event.get("title") or ""),
            description=str(event.get("description") or ""),
            lang=lang,
            is_title=False,
        )
        localized_description = _annotate_event_description_names(
            localized_description,
            list(event.get("participants") or []),
            lang,
        )
        st.markdown(f"**{index}. {localized_title}**")
        st.markdown(f"`{time_label}`：{_format_event_time(event.get('event_time'), lang)}")
        st.markdown(f"`{description_label}`：{localized_description}")
        participants = list(event.get("participants") or [])
        participants_text = _format_people_inline(participants, lang)
        st.markdown(f"`{participants_label}`：{participants_text}")
        sources = event.get("sources") or []
        formatted_sources = _format_event_sources(sources, lang)
        if formatted_sources:
            st.markdown(f"`{quoted_sources_label}`：{formatted_sources}")
        elif event.get("representative_source_url"):
            st.markdown(f"`{quoted_sources_label}`：[link]({event['representative_source_url']})")


def _format_people_inline(people: list[dict[str, object]], lang: str) -> str:
    people = _dedupe_people_for_display(people)
    if not people:
        return "未提供" if lang == "zh-TW" else "Not available"
    parts: list[str] = []
    for person in people:
        name = str(person.get("display_name") or "").strip()
        if lang == "zh-TW":
            english_name = str(person.get("english_name") or "").strip()
            chinese_name = str(person.get("chinese_name") or "").strip()
            if chinese_name and english_name:
                name = f"{chinese_name}（{english_name}）"
        name = _normalize_person_display_name(name)
        if not name:
            continue
        person_id = person.get("person_id")
        if person_id:
            parts.append(f"[{name}]({person_detail_href(int(person_id))})")
        else:
            parts.append(name)
    if not parts:
        return "未提供" if lang == "zh-TW" else "Not available"
    return "、".join(parts)


def _dedupe_people_for_display(people: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[str] = set()
    for person in people:
        if not isinstance(person, dict):
            continue
        key = _person_display_dedupe_key(person)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(person)
    return deduped


def _person_display_dedupe_key(person: dict[str, object]) -> str:
    person_id = person.get("person_id")
    if person_id:
        try:
            return f"id:{int(person_id)}"
        except Exception:
            pass
    english_name = str(person.get("english_name") or "").strip()
    chinese_name = str(person.get("chinese_name") or "").strip()
    display_name = str(person.get("display_name") or "").strip()
    if english_name:
        return f"en:{_normalize_person_similarity_key(english_name)}"
    if chinese_name:
        return f"zh:{_normalize_person_name_key(chinese_name)}"
    if display_name:
        return f"display:{_normalize_person_similarity_key(display_name)}"
    return ""


def _normalize_person_similarity_key(name: str) -> str:
    raw = str(name or "").strip().lower()
    if not raw:
        return ""
    words = re.findall(r"[a-z]+", raw)
    if len(words) >= 2:
        first_root = words[0][:3]
        last = words[-1]
        return f"{last}|{first_root}"
    return _normalize_person_name_key(raw)


def _normalize_person_name_key(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[\s\.\,\-\(\)\'\"_]+", "", text)


def _normalize_person_display_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    # Remove accidental separators like "Maria Elvira . Salazar" while
    # keeping initials such as "J. D. Vance".
    text = re.sub(r"\b([A-Za-z]{2,})\s*\.\s*([A-Za-z]{2,})\b", r"\1 \2", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def _prefer_sheet_counts_if_newer(current_counts: dict[str, int]) -> dict[str, int]:
    try:
        sheet = GoogleSheetReadService()
        people = sheet.list_people()
        legislation = sheet.list_legislation()
    except Exception:
        return current_counts

    sheet_counts = _collect_dashboard_counts_sheet(people, legislation)
    merged = dict(current_counts)
    for key, value in sheet_counts.items():
        merged[key] = max(int(merged.get(key, 0) or 0), int(value or 0))
    return merged


def _annotate_event_description_names(description: str, participants: list[dict[str, object]], lang: str) -> str:
    if lang != "zh-TW" or not description:
        return description
    updated = description
    for participant in participants:
        english_name = str(participant.get("english_name") or "").strip()
        chinese_name = str(participant.get("chinese_name") or "").strip()
        if not english_name or not chinese_name:
            continue
        replacement = f"{chinese_name}（{english_name}）"
        if replacement in updated:
            continue
        if len(english_name) < 4:
            continue
        pattern = re.compile(rf"(?<![A-Za-z]){re.escape(english_name)}(?![A-Za-z])", re.IGNORECASE)
        updated = pattern.sub(replacement, updated)
    return updated


def _is_test_event(title: str | None) -> bool:
    lowered = str(title or "").strip().lower()
    return "test shared" in lowered or lowered.startswith("test ")


def _infer_categories_from_statement(statement: Statement, participants: list[Person | None]) -> set[str]:
    categories: set[str] = set()
    title = (statement.title or "").lower()
    description = (statement.excerpt or "").lower()
    combined = f"{title} {description}"
    participant_names = " ".join((person.full_name or "").lower() for person in participants if person)

    if any(keyword in combined or keyword in participant_names for keyword in ["president", "secretary", "white house", "trump", "rubio"]):
        categories.add("federal_officials")
    if any(keyword in combined or keyword in participant_names for keyword in ["sen.", "senator", "rep.", "representative", "congress"]):
        categories.add("congress_members")
    if any(keyword in combined for keyword in ["governor", "state department", "state executive"]):
        categories.add("state_officials")
    if any(keyword in combined for keyword in ["state senate", "state house", "state legislator"]):
        categories.add("state_legislators")
    return categories


def _infer_categories_from_sheet_event(item: dict[str, object]) -> set[str]:
    categories: set[str] = set()
    title = str(item.get("title") or "").lower()
    summary = str(item.get("summary") or "").lower()
    combined = f"{title} {summary}"
    if any(keyword in combined for keyword in ["president", "secretary", "white house", "trump", "rubio"]):
        categories.add("federal_officials")
    if any(keyword in combined for keyword in ["sen.", "senator", "rep.", "representative", "congress"]):
        categories.add("congress_members")
    if any(keyword in combined for keyword in ["governor", "state executive"]):
        categories.add("state_officials")
    if any(keyword in combined for keyword in ["state senate", "state house", "state legislator"]):
        categories.add("state_legislators")
    return categories


def _localize_event_text(title: str, description: str, lang: str, is_title: bool) -> str:
    if lang != "zh-TW":
        return title if is_title else description
    source = title if is_title else description
    ai_output = _ai_translate_dashboard_text(source, title=title, description=description)
    if ai_output:
        return ai_output
    return _translate_event_text(source, lang)


def _localize_legislation_text(
    bill_number: str,
    title: str,
    summary: str,
    latest_action: str,
    lang: str,
) -> str:
    if lang != "zh-TW":
        return title
    ai_output = _ai_summarize_legislation_for_dashboard(
        bill_number=bill_number,
        title=title,
        summary=summary,
        latest_action=latest_action,
    )
    if ai_output:
        return ai_output
    return _translate_event_text(title, lang)


@lru_cache(maxsize=256)
def _ai_translate_dashboard_text(source: str, title: str, description: str) -> str | None:
    if not source or not _looks_like_english(source):
        return None
    service = AIAssistService()
    if not service.enabled:
        return None
    try:
        result = service.summarize_statement(title=title, summary=description)
    except Exception:
        return None
    return result.strip() if result else None


@lru_cache(maxsize=256)
def _ai_summarize_legislation_for_dashboard(
    bill_number: str,
    title: str,
    summary: str,
    latest_action: str,
) -> str | None:
    if not _looks_like_english(title):
        return None
    service = AIAssistService()
    if not service.enabled:
        return None
    try:
        result = service.summarize_legislation(
            bill_number=bill_number,
            title=title,
            summary=summary,
            latest_action=latest_action,
        )
    except Exception:
        return None
    return result.strip() if result else None


def _looks_like_english(text: str) -> bool:
    ascii_letters = re.findall(r"[A-Za-z]", text)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    return len(ascii_letters) >= 6 and len(cjk) == 0


def _format_event_sources(sources: list[object], lang: str) -> str:
    if not sources:
        return ""
    if isinstance(sources[0], str):
        return " | ".join(f"[link]({source})" for source in sources[:5])
    formatted: list[str] = []
    for source in sources[:3]:
        title = source.source_title or source_label(source, lang, source.source_type)
        formatted.append(f"[{title}]({source.source_url})")
    return " | ".join(formatted)


def _participants_from_sheet(
    event: dict[str, object],
    lang: str,
    people_by_id: dict[int, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    participant_ids = list(event.get("participant_ids_list") or [])
    participants_en = list(event.get("participants_en_list") or [])
    participants_zh = list(event.get("participants_zh_list") or [])
    participants: list[dict[str, object]] = []
    for index, name in enumerate(participants_en):
        person_id = participant_ids[index] if index < len(participant_ids) else None
        zh_name = participants_zh[index] if index < len(participants_zh) else ""
        english_name = str(name or "").strip()
        if person_id and people_by_id:
            person = people_by_id.get(int(person_id))
            if person:
                canonical_en = _sheet_person_english_name(person)
                if canonical_en:
                    english_name = canonical_en
        display_name = zh_name if (lang == "zh-TW" and zh_name) else english_name
        participants.append(
            {
                "person_id": person_id,
                "display_name": display_name,
                "english_name": english_name,
                "chinese_name": zh_name or "",
            }
        )
    return participants


def _sheet_person_english_name(person: dict[str, object]) -> str:
    for key in ("display_name_en", "full_name", "display_name"):
        value = str(person.get(key) or "").strip()
        if value:
            return value
    return ""


def _translate_event_text(text: str | None, lang: str) -> str:
    if not text:
        return "未提供" if lang == "zh-TW" else "Not available"
    if lang != "zh-TW":
        return text

    translated = text
    replacements = [
        ("Sens.", "參議員"),
        ("Sen.", "參議員"),
        ("Rep.", "眾議員"),
        ("Representatives", "眾議員"),
        ("Representative", "眾議員"),
        ("Senators", "參議員"),
        ("Senator", "參議員"),
        ("lead bipartisan", "領銜提出跨黨派"),
        ("Lead Bipartisan", "領銜提出跨黨派"),
        ("Bipartisan", "跨黨派"),
        ("Bill", "法案"),
        ("Resolution", "決議案"),
        ("Statement", "聲明"),
        ("Letter", "聯名函"),
        ("Introduce", "提出"),
        ("Introduced", "提出"),
        ("Commemorating", "紀念"),
        ("Anniversary", "週年"),
        ("first presidential elections", "首次總統直選"),
        ("First Presidential Elections", "首次總統直選"),
        ("drone cooperation", "無人機合作"),
        ("special defense budget", "特別國防預算"),
        ("partnership with the United States", "與美國的夥伴關係"),
        ("boost defense spending", "提高國防支出"),
        ("deter Communist China", "嚇阻中共"),
        ("Taiwan", "台灣"),
        ("U.S.", "美國"),
        ("United States", "美國"),
    ]
    for source, target in replacements:
        translated = translated.replace(source, target)
    return translated
