from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from tracker.db import session_scope
from tracker.models import Person
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
            empty_label = "目前沒有待審核事件。" if lang == "zh-TW" else "No events need review right now."
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
        year_label = "年份" if lang == "zh-TW" else "Year"
        month_label = "月份" if lang == "zh-TW" else "Month"
        event_label = "事件" if lang == "zh-TW" else "Event"
        time_label = "時間" if lang == "zh-TW" else "Time"
        description_label = "事件描述" if lang == "zh-TW" else "Description"
        participants_label = "參與人" if lang == "zh-TW" else "Participants"
        quoted_sources_label = "引述來源" if lang == "zh-TW" else "Quoted sources"
        no_events_in_month = "這個月份目前沒有待審核事件。" if lang == "zh-TW" else "No review events are available for this month."

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
            st.markdown(f"`{time_label}`：{_format_event_time(selected.date_published or selected.date_collected)}")
            st.markdown(f"`{description_label}`：{selected.excerpt or selected.title or selected.source_url}")
            st.markdown(f"`{participants_label}`：")
            render_person_links(participants, lang, key_prefix=f"review-event-{selected.id}")
            if sources:
                formatted_sources = " | ".join(
                    f"[{source.source_title or source_label(source, lang, str(source.source_type or labels['unknown']))}]({source.source_url})"
                    for source in sources[:3]
                )
                st.markdown(f"`{quoted_sources_label}`：{formatted_sources}")
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


def _format_event_time(value: datetime | None) -> str:
    if value is None:
        return "N/A"
    return value.strftime("%Y-%m-%d")
