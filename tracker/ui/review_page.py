from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from tracker.config import use_google_sheet_primary_mode
from tracker.db import session_scope
from tracker.models import Person
from tracker.services.ai_assist_service import AIAssistService
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.statements_service import StatementsService
from tracker.ui.display import localize_dataframe, localize_value
from tracker.ui import dashboard
from tracker.ui.navigation import render_person_links
from tracker.ui.source_labels import source_label, statement_source_label


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["review_queue"])
    ai_service = AIAssistService()
    if use_google_sheet_primary_mode():
        if _render_google_sheet_fallback(lang, labels):
            return
        st.info("No events need review right now." if lang != "zh-TW" else "目前沒有可顯示的事件資料。")
        return
    with session_scope() as session:
        service = StatementsService(session)
        events = service.list_review_queue()
        people_by_id = {person.id: person for person in session.query(Person).all()}

        if not events:
            if _render_google_sheet_fallback(lang, labels):
                return
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
        category_label = "人物類別" if lang == "zh-TW" else "Person category"
        time_label = "時間" if lang == "zh-TW" else "Time"
        description_label = "事件描述" if lang == "zh-TW" else "Description"
        participants_label = "參與人" if lang == "zh-TW" else "Participants"
        quoted_sources_label = "引述來源" if lang == "zh-TW" else "Quoted sources"
        no_events_in_filter = "此篩選條件下沒有待審核事件。" if lang == "zh-TW" else "No review events match this filter."

        selected_year = st.selectbox(year_label, years)
        month_options = sorted(
            {
                (item.date_published or item.date_collected).month
                for item in events
                if (item.date_published or item.date_collected) and (item.date_published or item.date_collected).year == selected_year
            },
            reverse=True,
        )
        month_options = [0, *month_options]
        selected_month = st.selectbox(
            month_label,
            month_options,
            format_func=lambda value: ("全部" if lang == "zh-TW" else "All") if value == 0 else f"{value:02d}",
        )

        filtered_events = [
            item
            for item in events
            if (item.date_published or item.date_collected)
            and (item.date_published or item.date_collected).year == selected_year
            and (selected_month == 0 or (item.date_published or item.date_collected).month == selected_month)
        ]
        if not filtered_events:
            st.info(no_events_in_filter)
            return

        participant_rows_map = {item.id: service.list_participants_for_statement(item.id) for item in filtered_events}
        participant_ids = {
            row.person_id
            for rows in participant_rows_map.values()
            for row in rows
            if row.person_id
        }
        person_category_map = dashboard._build_person_category_map(session, participant_ids)
        category_options = _person_category_options(lang)
        selected_category = st.selectbox(
            category_label,
            list(category_options.keys()),
            format_func=lambda key: category_options[key],
        )
        filtered_events = [
            item
            for item in filtered_events
            if _event_matches_category(
                participant_rows_map.get(item.id, []),
                person_category_map,
                selected_category,
            )
        ]
        if not filtered_events:
            st.info(no_events_in_filter)
            return

        for selected in filtered_events:
            participants = []
            for participant_row in participant_rows_map.get(selected.id, []):
                person = people_by_id.get(participant_row.person_id)
                if person and not any(participant["person_id"] == person.id for participant in participants):
                    participants.append({"person_id": person.id, "display_name": person.full_name})
            sources = service.list_sources_for_statement(selected.id)
            localized_summary = (
                ai_service.summarize_statement(selected.title, selected.excerpt or selected.full_text or selected.raw_text or "")
                if lang == "zh-TW"
                else None
            )

            with st.container(border=True):
                st.markdown(f"**{selected.title}**")
                st.markdown(f"`{time_label}`：{_format_event_time(selected.date_published or selected.date_collected)}")
                st.markdown(f"`{description_label}`：{localized_summary or selected.excerpt or selected.title or selected.source_url}")
                if localized_summary:
                    st.caption("AI 中文摘要" if lang == "zh-TW" else "AI summary")
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

                col1, col2, col3 = st.columns(3)
                if col1.button(labels["confirm_related"], key=f"confirm-{selected.id}"):
                    service.update_review_status(selected.id, "confirmed")
                    st.success(labels["confirm_related"])
                    st.rerun()
                if col2.button(labels["needs_review"], key=f"needs-{selected.id}"):
                    service.update_review_status(selected.id, "needs_review")
                    st.success(labels["needs_review"])
                    st.rerun()
                if col3.button(labels["dismiss"], key=f"dismiss-{selected.id}"):
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
    people = GoogleSheetReadService().list_people()
    ai_service = AIAssistService()
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

    year_label = "年份" if lang == "zh-TW" else "Year"
    month_label = "月份" if lang == "zh-TW" else "Month"
    category_label = "人物類別" if lang == "zh-TW" else "Person category"
    time_label = "時間" if lang == "zh-TW" else "Time"
    description_label = "事件描述" if lang == "zh-TW" else "Description"
    participants_label = "參與人" if lang == "zh-TW" else "Participants"
    quoted_sources_label = "引述來源" if lang == "zh-TW" else "Quoted sources"

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
    month_options = [0, *month_options]
    selected_month = st.selectbox(
        month_label,
        month_options,
        format_func=lambda value: ("全部" if lang == "zh-TW" else "All") if value == 0 else f"{value:02d}",
        key="sheet-review-month",
    )
    filtered_events = [
        item
        for item in events
        if (item.get("year_int") or (item.get("event_date_date").year if item.get("event_date_date") else None)) == selected_year
        and (
            selected_month == 0
            or (item.get("month_int") or (item.get("event_date_date").month if item.get("event_date_date") else None)) == selected_month
        )
    ]
    if not filtered_events:
        return True

    people_by_id = {int(item.get("person_id")): item for item in people if item.get("person_id")}
    people_category = {pid: dashboard._sheet_person_category(item) for pid, item in people_by_id.items()}
    category_options = _person_category_options(lang)
    selected_category = st.selectbox(
        category_label,
        list(category_options.keys()),
        format_func=lambda key: category_options[key],
        key="sheet-review-category",
    )
    filtered_events = [
        item
        for item in filtered_events
        if _sheet_event_matches_category(item, people_category, selected_category)
    ]
    if not filtered_events:
        st.info("此篩選條件下沒有待審核事件。" if lang == "zh-TW" else "No review events match this filter.")
        return True

    for selected in filtered_events:
        participants = _sheet_participants(selected)
        with st.container(border=True):
            st.markdown(f"**{selected.get('title') or ''}**")
            event_date = selected.get("event_date_date")
            st.markdown(f"`{time_label}`：{event_date.strftime('%Y-%m-%d') if event_date else 'N/A'}")
            localized_summary = (
                ai_service.summarize_statement(str(selected.get("title") or ""), str(selected.get("summary") or ""))
                if lang == "zh-TW"
                else None
            )
            st.markdown(f"`{description_label}`：{localized_summary or selected.get('summary') or selected.get('title') or ''}")
            if localized_summary:
                st.caption("AI 中文摘要" if lang == "zh-TW" else "AI summary")
            st.markdown(f"`{participants_label}`：")
            render_person_links(participants, lang, key_prefix=f"sheet-review-event-{selected.get('event_id')}")
            if selected.get("source_urls"):
                st.markdown(f"`{quoted_sources_label}`：" + " | ".join(f"[link]({url})" for url in selected["source_urls"][:5]))
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


def _person_category_options(lang: str) -> dict[str, str]:
    if lang == "zh-TW":
        return {
            "all": "全部",
            "federal_officials": "聯邦官員",
            "congress_members": "國會議員",
            "state_officials": "州政府官員",
            "state_legislators": "州議員",
        }
    return {
        "all": "All",
        "federal_officials": "Federal officials",
        "congress_members": "Congress members",
        "state_officials": "State officials",
        "state_legislators": "State legislators",
    }


def _event_matches_category(participants, person_category_map: dict[int, str], selected_category: str) -> bool:
    if selected_category == "all":
        return True
    categories = {
        person_category_map.get(item.person_id)
        for item in participants
        if getattr(item, "person_id", None) and person_category_map.get(item.person_id)
    }
    return selected_category in categories


def _sheet_event_matches_category(event: dict[str, object], people_category: dict[int, str | None], selected_category: str) -> bool:
    if selected_category == "all":
        return True
    categories = {
        people_category.get(int(person_id))
        for person_id in list(event.get("participant_ids_list") or [])
        if person_id is not None and people_category.get(int(person_id))
    }
    return selected_category in categories


def _format_event_time(value: datetime | None) -> str:
    if value is None:
        return "N/A"
    return value.strftime("%Y-%m-%d")
