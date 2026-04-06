from __future__ import annotations

from datetime import datetime

import streamlit as st
from sqlalchemy import func, select

from tracker.db import session_scope
from tracker.models import NotificationLog, Person, Statement, SyncRun, Tracker
from tracker.services.statements_service import StatementsService
from tracker.ui.navigation import render_person_links
from tracker.ui.source_labels import source_label


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["dashboard"])
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
        recent_events = []
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

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(labels["total_officials"], total_officials)
    col2.metric(labels["total_trackers"], total_trackers)
    col3.metric(labels["recent_statements"], total_statements)
    col4.metric(labels["recent_sync_runs"], total_sync_runs)
    st.caption(f"{labels['recent_alerts']}: {total_alerts}")

    section_title = "最新三個事件" if lang == "zh-TW" else "Latest three events"
    time_label = "時間" if lang == "zh-TW" else "Time"
    description_label = "事件描述" if lang == "zh-TW" else "Description"
    participants_label = "參與人" if lang == "zh-TW" else "Participants"
    quoted_sources_label = "引述來源" if lang == "zh-TW" else "Quoted sources"
    no_events_label = "目前還沒有可顯示的台灣相關事件。" if lang == "zh-TW" else "No Taiwan-related events are available yet."

    st.subheader(section_title)
    if not recent_events:
        st.info(no_events_label)
        return

    for index, event in enumerate(recent_events, start=1):
        with st.container(border=True):
            st.markdown(f"**{index}. {_translate_event_text(event['title'], lang)}**")
            st.markdown(f"`{time_label}`：{_format_event_time(event['event_time'], lang)}")
            st.markdown(f"`{description_label}`：{_translate_event_text(event['description'], lang)}")
            st.markdown(f"`{participants_label}`：")
            render_person_links(event["participants"], lang, key_prefix=f"dashboard-event-{index}")
            formatted_sources = _format_event_sources(event["sources"], lang)
            if formatted_sources:
                st.markdown(f"`{quoted_sources_label}`：{formatted_sources}")
            elif event["representative_source_url"]:
                st.markdown(f"`{quoted_sources_label}`：[link]({event['representative_source_url']})")


def _format_event_time(value: datetime | None, lang: str) -> str:
    if value is None:
        return "未提供" if lang == "zh-TW" else "Not available"
    return value.strftime("%Y-%m-%d")


def _format_event_sources(sources: list[object], lang: str) -> str:
    if not sources:
        return ""
    formatted: list[str] = []
    for source in sources[:3]:
        title = source.source_title or source_label(source, lang, source.source_type)
        formatted.append(f"[{title}]({source.source_url})")
    return " | ".join(formatted)


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
