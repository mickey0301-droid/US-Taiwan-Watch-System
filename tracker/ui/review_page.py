from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from tracker.db import session_scope
from tracker.models import Person
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.statements_service import StatementsService
from tracker.ui.display import localize_dataframe, localize_value
from tracker.ui.navigation import render_person_links
from tracker.ui.source_labels import source_label, statement_source_label


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["review_queue"])
    with session_scope() as session:
        service = StatementsService(session)
        events = service.list_review_queue()
        people_by_id = {person.id: person for person in session.query(Person).all()}

        if not events:
            if _render_google_sheet_fallback(lang, labels):
                return
            empty_label = "ç›®å‰æ²’æœ‰å¾…å¯©æ ¸äº‹ä»¶ã€‚" if lang == "zh-TW" else "No events need review right now."
            st.info(empty_label)
            return

        years = sorted(
            {
                (item.date_published or item.date_collected).year
                for item in events
                if (item.date_published or item.date_collected)
            },
            reverse=True,
        )
        year_label = "å¹´ä»½" if lang == "zh-TW" else "Year"
        month_label = "æœˆä»½" if lang == "zh-TW" else "Month"
        event_label = "äº‹ä»¶" if lang == "zh-TW" else "Event"
        time_label = "æ™‚é–“" if lang == "zh-TW" else "Time"
        description_label = "äº‹ä»¶æè¿°" if lang == "zh-TW" else "Description"
        participants_label = "åƒèˆ‡äºº" if lang == "zh-TW" else "Participants"
        quoted_sources_label = "å¼•è¿°ä¾†æº" if lang == "zh-TW" else "Quoted sources"
        no_events_in_month = "é€™å€‹æœˆä»½ç›®å‰æ²’æœ‰å¾…å¯©æ ¸äº‹ä»¶ã€‚" if lang == "zh-TW" else "No review events are available for this month."

        selected_year = st.selectbox(year_label, years)
        month_options = sorted(
            {
                (item.date_published or item.date_collected).month
                for item in events
                if (item.date_published or item.date_collected) and (item.date_published or item.date_collected).year == selected_year
            },
            reverse=True,
        )
        selected_month = st.selectbox(month_label, month_options, format_func=lambda value: f"{value:02d}")

        filtered_events = [
            item
            for item in events
            if (item.date_published or item.date_collected)
            and (item.date_published or item.date_collected).year == selected_year
            and (item.date_published or item.date_collected).month == selected_month
        ]
        if not filtered_events:
            st.info(no_events_in_month)
            return

        event_options = {
            f"{_format_event_time(item.date_published or item.date_collected)} | {item.title[:100]}": item.id
            for item in filtered_events
        }
        selected_event_label = st.selectbox(event_label, list(event_options.keys()))
        selected_event_id = event_options[selected_event_label]
        selected = next(item for item in filtered_events if item.id == selected_event_id)

        participants = []
        for item in service.list_participants_for_statement(selected.id):
            person = people_by_id.get(item.person_id)
            if person and not any(participant["person_id"] == person.id for participant in participants):
                participants.append({"person_id": person.id, "display_name": person.full_name})
        sources = service.list_sources_for_statement(selected.id)

        with st.container(border=True):
            st.markdown(f"**{selected.title}**")
            st.markdown(f"`{time_label}`ï¼š{_format_event_time(selected.date_published or selected.date_collected)}")
            st.markdown(f"`{description_label}`ï¼š{selected.excerpt or selected.title or selected.source_url}")
            st.markdown(f"`{participants_label}`ï¼š")
            render_person_links(participants, lang, key_prefix=f"review-event-{selected.id}")
            if sources:
                formatted_sources = " | ".join(
                    f"[{source.source_title or source_label(source, lang, str(source.source_type or labels['unknown']))}]({source.source_url})"
                    for source in sources[:3]
                )
                st.markdown(f"`{quoted_sources_label}`ï¼š{formatted_sources}")
            st.write(f"{labels['attached_sources']}: {service.get_source_count(selected.id)}")
            st.write(f"{labels['keywords']}: {', '.join((selected.matched_keywords or {}).get('hits', [])) or labels['unknown']}")

        source_rows = [
            {
                "source_type": source_label(source, lang, str(source.source_type or labels["unknown"])),
                "source_url": source.source_url,
                "source_title": source.source_title,
                "is_primary": source.is_primary,
            }
            for source in sources
        ]
        if source_rows:
            st.dataframe(
                localize_dataframe(pd.DataFrame(source_rows), lang, value_columns=["is_primary"]),
                use_container_width=True,
            )

        col1, col2, col3 = st.columns(3)
        if col1.button(labels["confirm_related"]):
            service.update_review_status(selected.id, "confirmed")
            st.success(labels["confirm_related"])
            st.rerun()
        if col2.button(labels["needs_review"]):
            service.update_review_status(selected.id, "needs_review")
            st.success(labels["needs_review"])
            st.rerun()
        if col3.button(labels["dismiss"]):
            service.update_review_status(selected.id, "dismissed")
            st.success(labels["dismiss"])
            st.rerun()

        rows = [
            {
                "event_time": item.date_published or item.date_collected,
                "title": item.title,
                "review_status": item.review_status,
                "event_source_preference": statement_source_label(item, lang, str(localize_value(item.event_source_preference, lang))),
                "source_count": service.get_source_count(item.id),
                "matched_keywords": ", ".join((item.matched_keywords or {}).get("hits", [])),
            }
            for item in filtered_events
        ]
        summary_df = localize_dataframe(pd.DataFrame(rows), lang, value_columns=["review_status"])
        st.dataframe(summary_df, use_container_width=True)


def _render_google_sheet_fallback(lang: str, labels: dict[str, str]) -> bool:
    events = GoogleSheetReadService().list_events()
    if not events:
        return False

    st.info(
        "Google Sheet fallback mode is active. Review actions are disabled in the cloud app."
        if lang != "zh-TW"
        else "目前使用 Google Sheet fallback 模式，雲端版暫不提供審核寫回。"
    )
    years = sorted(
        {
            item.get("year_int") or (item.get("event_date_date").year if item.get("event_date_date") else None)
            for item in events
            if item.get("year_int") or item.get("event_date_date")
        },
        reverse=True,
    )
    years = [item for item in years if item is not None]
    if not years:
        return False

    year_label = "å¹´ä»½" if lang == "zh-TW" else "Year"
    month_label = "æœˆä»½" if lang == "zh-TW" else "Month"
    event_label = "äº‹ä»¶" if lang == "zh-TW" else "Event"
    time_label = "æ™‚é–“" if lang == "zh-TW" else "Time"
    description_label = "äº‹ä»¶æè¿°" if lang == "zh-TW" else "Description"
    participants_label = "åƒèˆ‡äºº" if lang == "zh-TW" else "Participants"
    quoted_sources_label = "å¼•è¿°ä¾†æº" if lang == "zh-TW" else "Quoted sources"

    selected_year = st.selectbox(year_label, years, key="sheet-review-year")
    month_options = sorted(
        {
            item.get("month_int") or (item.get("event_date_date").month if item.get("event_date_date") else None)
            for item in events
            if (item.get("year_int") or (item.get("event_date_date").year if item.get("event_date_date") else None)) == selected_year
        },
        reverse=True,
    )
    month_options = [item for item in month_options if item is not None]
    selected_month = st.selectbox(month_label, month_options, format_func=lambda value: f"{value:02d}", key="sheet-review-month")
    filtered_events = [
        item
        for item in events
        if (item.get("year_int") or (item.get("event_date_date").year if item.get("event_date_date") else None)) == selected_year
        and (item.get("month_int") or (item.get("event_date_date").month if item.get("event_date_date") else None)) == selected_month
    ]
    if not filtered_events:
        return True

    options = {
        f"{_format_event_time(datetime.combine(item['event_date_date'], datetime.min.time()) if item.get('event_date_date') else None)} | {str(item.get('title') or '')[:100]}": item
        for item in filtered_events
    }
    selected_event_label = st.selectbox(event_label, list(options.keys()), key="sheet-review-event")
    selected = options[selected_event_label]
    participants = _sheet_participants(selected)

    with st.container(border=True):
        st.markdown(f"**{selected.get('title') or ''}**")
        event_date = selected.get("event_date_date")
        st.markdown(f"`{time_label}`ï¼š{event_date.strftime('%Y-%m-%d') if event_date else 'N/A'}")
        st.markdown(f"`{description_label}`ï¼š{selected.get('summary') or selected.get('title') or ''}")
        st.markdown(f"`{participants_label}`ï¼š")
        render_person_links(participants, lang, key_prefix=f"sheet-review-event-{selected.get('event_id')}")
        if selected.get("source_urls"):
            st.markdown(f"`{quoted_sources_label}`ï¼š" + " | ".join(f"[link]({url})" for url in selected["source_urls"][:5]))
        st.write(f"{labels['attached_sources']}: {selected.get('source_count_int') or 0}")
        st.write(f"{labels['keywords']}: {selected.get('taiwan_keywords') or labels['unknown']}")

    summary_df = localize_dataframe(
        pd.DataFrame(
            [
                {
                    "event_time": item.get("event_date_date"),
                    "title": item.get("title"),
                    "review_status": item.get("review_status"),
                    "event_source_preference": item.get("primary_source_type"),
                    "source_count": item.get("source_count_int"),
                    "matched_keywords": item.get("taiwan_keywords"),
                }
                for item in filtered_events
            ]
        ),
        lang,
        value_columns=["review_status"],
    )
    st.dataframe(summary_df, use_container_width=True)
    return True


def _sheet_participants(event: dict[str, object]) -> list[dict[str, object]]:
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


def _format_event_time(value: datetime | None) -> str:
    if value is None:
        return "N/A"
    return value.strftime("%Y-%m-%d")
