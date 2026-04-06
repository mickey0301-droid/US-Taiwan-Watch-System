from __future__ import annotations

from datetime import date, datetime

import streamlit as st
from sqlalchemy import func, select

from tracker.db import session_scope
from tracker.models import NotificationLog, Person, Statement, SyncRun, Tracker
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.statements_service import StatementsService
from tracker.ui.navigation import render_person_links
from tracker.ui.source_labels import source_label


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["dashboard"])
    total_officials = 0
    total_trackers = 0
    total_statements = 0
    total_sync_runs = 0
    total_alerts = 0
    recent_events: list[dict[str, object]] = []

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
                .where(Statement.relevance_score > 0)
                .order_by(Statement.date_published.desc().nullslast(), Statement.date_collected.desc(), Statement.id.desc())
                .limit(3)
            )
            .scalars()
            .all()
        )
        people_by_id = {person.id: person for person in session.execute(select(Person)).scalars().all()}
        for statement in recent_statements:
            participants = []
            for item in statements_service.list_participants_for_statement(statement.id):
                person = people_by_id.get(item.person_id)
                display_name = person.full_name if person and person.full_name else None
                if person and display_name and not any(participant["person_id"] == person.id for participant in participants):
                    participants.append({"person_id": person.id, "display_name": display_name})
            if not participants:
                person = people_by_id.get(statement.person_id) if statement.person_id else None
                participants = [{"person_id": person.id, "display_name": person.full_name}] if person and person.full_name else []
            recent_events.append(
                {
                    "title": statement.title,
                    "description": statement.excerpt or statement.title or statement.source_url,
                    "event_time": statement.date_published or statement.date_collected,
                    "participants": participants,
                    "sources": statements_service.list_sources_for_statement(statement.id),
                    "representative_source_url": statement.source_url,
                }
            )

    if total_officials == 0 and total_statements == 0 and _render_google_sheet_fallback(lang, labels):
        return

    _render_metrics(labels, total_officials, total_trackers, total_statements, total_sync_runs, total_alerts)
    _render_events_section(recent_events, lang)


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
    recent_events = [
        {
            "title": str(item.get("title") or ""),
            "description": str(item.get("summary") or item.get("title") or ""),
            "event_time": item.get("event_date_date"),
            "participants": _participants_from_sheet(item),
            "sources": item.get("source_urls") or [],
            "representative_source_url": None,
        }
        for item in events[:3]
    ]
    _render_events_section(recent_events, lang)
    return True


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


def _render_events_section(recent_events: list[dict[str, object]], lang: str) -> None:
    section_title = "æœ€æ–°ä¸‰å€‹äº‹ä»¶" if lang == "zh-TW" else "Latest three events"
    time_label = "æ™‚é–“" if lang == "zh-TW" else "Time"
    description_label = "äº‹ä»¶æè¿°" if lang == "zh-TW" else "Description"
    participants_label = "åƒèˆ‡äºº" if lang == "zh-TW" else "Participants"
    quoted_sources_label = "å¼•è¿°ä¾†æº" if lang == "zh-TW" else "Quoted sources"
    no_events_label = "ç›®å‰é‚„æ²’æœ‰å¯é¡¯ç¤ºçš„å°ç£ç›¸é—œäº‹ä»¶ã€‚" if lang == "zh-TW" else "No Taiwan-related events are available yet."

    st.subheader(section_title)
    if not recent_events:
        st.info(no_events_label)
        return

    for index, event in enumerate(recent_events, start=1):
        with st.container(border=True):
            st.markdown(f"**{index}. {_translate_event_text(str(event['title']), lang)}**")
            st.markdown(f"`{time_label}`ï¼š{_format_event_time(event.get('event_time'), lang)}")
            st.markdown(f"`{description_label}`ï¼š{_translate_event_text(str(event['description']), lang)}")
            st.markdown(f"`{participants_label}`ï¼š")
            render_person_links(list(event["participants"]), lang, key_prefix=f"dashboard-event-{index}")
            sources = event.get("sources") or []
            formatted_sources = _format_event_sources(sources, lang)
            if formatted_sources:
                st.markdown(f"`{quoted_sources_label}`ï¼š{formatted_sources}")
            elif event.get("representative_source_url"):
                st.markdown(f"`{quoted_sources_label}`ï¼š[link]({event['representative_source_url']})")


def _format_event_time(value: datetime | date | None, lang: str) -> str:
    if value is None:
        return "æœªæä¾›" if lang == "zh-TW" else "Not available"
    return value.strftime("%Y-%m-%d")


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


def _participants_from_sheet(event: dict[str, object]) -> list[dict[str, object]]:
    participant_ids = list(event.get("participant_ids_list") or [])
    participants_en = list(event.get("participants_en_list") or [])
    participants_zh = list(event.get("participants_zh_list") or [])
    participants: list[dict[str, object]] = []
    for index, name in enumerate(participants_en):
        person_id = participant_ids[index] if index < len(participant_ids) else None
        zh_name = participants_zh[index] if index < len(participants_zh) else ""
        display_name = f"{zh_name} {name}".strip() if zh_name else str(name)
        participants.append({"person_id": person_id, "display_name": display_name})
    return participants


def _translate_event_text(text: str | None, lang: str) -> str:
    if not text:
        return "æœªæä¾›" if lang == "zh-TW" else "Not available"
    if lang != "zh-TW":
        return text

    translated = text
    replacements = [
        ("Sens.", "åƒè­°å“¡"),
        ("Sen.", "åƒè­°å“¡"),
        ("Rep.", "çœ¾è­°å“¡"),
        ("Representatives", "çœ¾è­°å“¡"),
        ("Representative", "çœ¾è­°å“¡"),
        ("Senators", "åƒè­°å“¡"),
        ("Senator", "åƒè­°å“¡"),
        ("lead bipartisan", "é ˜éŠœæå‡ºè·¨é»¨æ´¾"),
        ("Lead Bipartisan", "é ˜éŠœæå‡ºè·¨é»¨æ´¾"),
        ("Bipartisan", "è·¨é»¨æ´¾"),
        ("Bill", "æ³•æ¡ˆ"),
        ("Resolution", "æ±ºè­°æ¡ˆ"),
        ("Statement", "è²æ˜Ž"),
        ("Letter", "è¯åå‡½"),
        ("Introduce", "æå‡º"),
        ("Introduced", "æå‡º"),
        ("Commemorating", "ç´€å¿µ"),
        ("Anniversary", "é€±å¹´"),
        ("first presidential elections", "é¦–æ¬¡ç¸½çµ±ç›´é¸"),
        ("First Presidential Elections", "é¦–æ¬¡ç¸½çµ±ç›´é¸"),
        ("drone cooperation", "ç„¡äººæ©Ÿåˆä½œ"),
        ("special defense budget", "ç‰¹åˆ¥åœ‹é˜²é ç®—"),
        ("partnership with the United States", "èˆ‡ç¾Žåœ‹çš„å¤¥ä¼´é—œä¿‚"),
        ("boost defense spending", "æé«˜åœ‹é˜²æ”¯å‡º"),
        ("deter Communist China", "åš‡é˜»ä¸­å…±"),
        ("Taiwan", "å°ç£"),
        ("U.S.", "ç¾Žåœ‹"),
        ("United States", "ç¾Žåœ‹"),
    ]
    for source, target in replacements:
        translated = translated.replace(source, target)
    return translated
