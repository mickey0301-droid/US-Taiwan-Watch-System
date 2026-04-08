from __future__ import annotations

from datetime import datetime
import json

import pandas as pd
import streamlit as st

from tracker.config import use_google_sheet_primary_mode
from tracker.db import session_scope
from tracker.models import Person
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.statements_service import StatementsService
from tracker.ui.display import localize_dataframe, localize_value
from tracker.ui import dashboard
from tracker.ui.source_labels import statement_source_label


def _matched_hits(value: object) -> list[str]:
    payload = value
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except Exception:
            return []
    if not isinstance(payload, dict):
        return []
    hits = payload.get("hits")
    if isinstance(hits, list):
        return [str(item).strip() for item in hits if str(item).strip()]
    return []


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["review_queue"])
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
                    participants.append(
                        {
                            "person_id": person.id,
                            "display_name": person.full_name,
                            "english_name": person.full_name,
                            "chinese_name": "",
                        }
                    )
            sources = service.list_sources_for_statement(selected.id)
            event_payload = {
                "title": selected.title,
                "description": selected.excerpt or selected.title or selected.source_url,
                "event_time": selected.date_published or selected.date_collected,
                "participants": participants,
                "sources": sources,
                "representative_source_url": selected.source_url,
            }
            dashboard._render_event_card(index=1, event=event_payload, lang=lang)

            st.write(f"{labels['attached_sources']}: {service.get_source_count(selected.id)}")
            st.write(f"{labels['keywords']}: {', '.join(_matched_hits(selected.matched_keywords)) or labels['unknown']}")

        rows = [
            {
                "event_time": item.date_published or item.date_collected,
                "title": item.title,
                "review_status": item.review_status,
                "event_source_preference": statement_source_label(item, lang, str(localize_value(item.event_source_preference, lang))),
                "source_count": service.get_source_count(item.id),
                "matched_keywords": ", ".join(_matched_hits(item.matched_keywords)),
            }
            for item in filtered_events
        ]
        summary_df = localize_dataframe(pd.DataFrame(rows), lang, value_columns=["review_status"])
        st.dataframe(summary_df, use_container_width=True)


def _render_google_sheet_fallback(lang: str, labels: dict[str, str]) -> bool:
    events = GoogleSheetReadService().list_events()
    people = GoogleSheetReadService().list_people()
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
        participants = _sheet_participants(selected, people_by_id=people_by_id)
        event_payload = {
            "title": str(selected.get("title") or ""),
            "description": str(selected.get("summary") or selected.get("title") or ""),
            "event_time": selected.get("event_date_date"),
            "participants": participants,
            "sources": selected.get("source_urls") or [],
            "representative_source_url": None,
        }
        dashboard._render_event_card(index=1, event=event_payload, lang=lang)
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


def _sheet_participants(
    event: dict[str, object],
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
                canonical_en = dashboard._sheet_person_english_name(person)
                if canonical_en:
                    english_name = canonical_en
        display_name = zh_name if zh_name else english_name
        participants.append(
            {
                "person_id": person_id,
                "display_name": display_name,
                "english_name": english_name,
                "chinese_name": zh_name,
            }
        )
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
