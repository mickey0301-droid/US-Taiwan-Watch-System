from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
import re

import streamlit as st
from sqlalchemy import func, select

from tracker.config import use_google_sheet_primary_mode
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
        _render_metrics(labels, 0, 0, 0, 0, 0)
        st.warning(
            "Google Sheet primary mode is enabled, but no sheet data could be loaded."
            if lang != "zh-TW"
            else "目前已啟用 Google Sheet-first 模式，但還是無法載入 Sheet 資料。"
        )
        render_google_sheet_fallback_diagnostic(lang)
        return
    total_officials = 0
    total_trackers = 0
    total_statements = 0
    total_sync_runs = 0
    total_alerts = 0
    recent_events_by_category: dict[str, list[dict[str, object]]] = _empty_event_buckets()
    recent_legislation_by_category: dict[str, list[dict[str, object]]] = _empty_legislation_buckets()

    with session_scope() as session:
        statements_service = StatementsService(session)
        total_officials = session.scalar(select(func.count()).select_from(Person)) or 0
        total_trackers = session.scalar(select(func.count()).select_from(Tracker)) or 0
        total_statements = session.scalar(select(func.count()).select_from(Statement)) or 0
        total_sync_runs = session.scalar(select(func.count()).select_from(SyncRun)) or 0
        total_alerts = session.scalar(select(func.count()).select_from(NotificationLog)) or 0
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

    if total_officials == 0 and total_statements == 0:
        if _render_google_sheet_fallback(lang, labels):
            return
        _render_metrics(labels, total_officials, total_trackers, total_statements, total_sync_runs, total_alerts)
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

    _render_metrics(labels, total_officials, total_trackers, total_statements, total_sync_runs, total_alerts)
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

    _render_metrics(labels, len(people), 0, len(events), len(legislation), 0)
    st.info(
        "Google Sheet fallback mode is active. The cloud app is showing exported data."
        if lang != "zh-TW"
        else "目前使用 Google Sheet fallback 模式，雲端版先顯示已匯出的資料。"
    )
    people_by_id = {int(item.get("person_id") or 0): item for item in people if item.get("person_id")}
    people_category = {pid: _sheet_person_category(item) for pid, item in people_by_id.items()}
    recent_events_by_category = _bucket_recent_events_sheet(events, people_by_id, people_category, lang=lang)
    recent_legislation_by_category = _bucket_recent_legislation_sheet(legislation, lang=lang)
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


def _render_metrics(
    labels: dict[str, str],
    total_officials: int,
    total_trackers: int,
    total_statements: int,
    total_sync_runs: int,
    total_alerts: int,
) -> None:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(labels["total_officials"], total_officials)
    col2.metric(labels["total_trackers"], total_trackers)
    col3.metric(labels["recent_statements"], total_statements)
    col4.metric(labels["recent_sync_runs"], total_sync_runs)
    st.caption(f"{labels['recent_alerts']}: {total_alerts}")


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
        sponsor_people = [item.person for item in row.sponsors if item.person and str(item.role or "").lower() == "sponsor"]
        if not sponsor_people:
            sponsor_people = [item.person for item in row.sponsors if item.person]
        sponsor = sponsor_people[0] if sponsor_people else None
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
            "participants": _participants_from_sheet(item, lang=lang),
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


def _bucket_recent_legislation_sheet(rows: list[dict[str, object]], lang: str) -> dict[str, list[dict[str, object]]]:
    buckets = _empty_legislation_buckets()
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
                "sponsor": _first_sheet_sponsor(item, lang=lang),
            }
        )
    return buckets


def _render_legislation_column(column, title: str, entries: list[dict[str, object]], lang: str) -> None:
    chamber_label = "所屬議院" if lang == "zh-TW" else "Chamber"
    sponsor_label = "提案議員" if lang == "zh-TW" else "Sponsor"
    introduced_label = "提案時間" if lang == "zh-TW" else "Introduced"
    date_label = "日期" if lang == "zh-TW" else "Date"
    empty_label = "目前無資料" if lang == "zh-TW" else "No records yet"
    with column:
        st.markdown(f"**{title}**")
        if not entries:
            st.caption(empty_label)
            return
        for index, item in enumerate(entries, start=1):
            with st.container(border=True):
                bill_number = str(item.get("bill_number") or "").strip()
                display_title = _format_legislation_title_with_description(
                    title=str(item.get("title") or ""),
                    summary=str(item.get("summary") or ""),
                    lang=lang,
                )
                if bill_number:
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
                st.markdown(f"`{introduced_label}`：{_format_event_time(item.get('introduced_date'), lang)}")
                st.caption(f"{date_label}: {_format_event_time(item.get('date'), lang)}")
                if item.get("source_url"):
                    st.markdown(f"[link]({item['source_url']})")


def _first_sheet_sponsor(item: dict[str, object], lang: str) -> dict[str, object] | None:
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
    return {
        "person_id": person_id,
        "display_name": display_name,
        "english_name": name,
        "chinese_name": zh_name,
    }


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

    chinese_title_raw = _localize_legislation_text(
        bill_number="",
        title=title_text,
        summary=title_text,
        latest_action="",
        lang=lang,
    ).strip()
    chinese_title = _clean_legislation_title_text(chinese_title_raw, fallback_title=title_text)
    if not chinese_title:
        chinese_title = _clean_legislation_title_text(_translate_event_text(title_text, lang).strip(), fallback_title=title_text)

    if _looks_like_english(title_text):
        headline = f"{chinese_title}（{title_text}）"
    else:
        headline = chinese_title

    summary_text = str(summary or "").strip()
    if not summary_text or _normalize_compare_text(summary_text) == _normalize_compare_text(title_text):
        return headline
    summary_zh_raw = _localize_event_text(title=title_text, description=summary_text, lang=lang, is_title=False).strip()
    summary_zh = _clean_legislation_summary_text(summary_zh_raw, title_text=title_text, chinese_title=chinese_title)
    if not summary_zh:
        return headline
    return f"{headline}：{summary_zh}"


def _normalize_compare_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = re.sub(r"[\s\.\,\-\–\—\:\;\'\"\(\)\[\]\{\}]+", "", normalized)
    return normalized


def _clean_legislation_title_text(text: str, fallback_title: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = re.split(r"[：:]", cleaned, maxsplit=1)[0].strip()
    fallback_norm = _normalize_compare_text(fallback_title)
    candidates = re.findall(r"（([^）]+)）", cleaned)
    for candidate in candidates:
        if _normalize_compare_text(candidate) == fallback_norm:
            cleaned = cleaned.replace(f"（{candidate}）", "").strip()
    cleaned = re.sub(r"\s+", "", cleaned)
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
    time_label = "時間" if lang == "zh-TW" else "Time"
    description_label = "事件描述" if lang == "zh-TW" else "Description"
    participants_label = "參與人" if lang == "zh-TW" else "Participants"
    quoted_sources_label = "引述來源" if lang == "zh-TW" else "Quoted sources"
    empty_label = "目前無資料" if lang == "zh-TW" else "No records yet"
    with column:
        st.markdown(f"**{title}**")
        if not entries:
            st.caption(empty_label)
            return
        for index, event in enumerate(entries, start=1):
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


def _participants_from_sheet(event: dict[str, object], lang: str) -> list[dict[str, object]]:
    participant_ids = list(event.get("participant_ids_list") or [])
    participants_en = list(event.get("participants_en_list") or [])
    participants_zh = list(event.get("participants_zh_list") or [])
    participants: list[dict[str, object]] = []
    for index, name in enumerate(participants_en):
        person_id = participant_ids[index] if index < len(participant_ids) else None
        zh_name = participants_zh[index] if index < len(participants_zh) else ""
        english_name = str(name or "").strip()
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
