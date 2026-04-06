from __future__ import annotations

import pandas as pd
import streamlit as st

from tracker.db import session_scope
from tracker.services.tracker_service import TrackerService
from tracker.services.tracker_sync_service import TrackerSyncService
from tracker.ui.display import localize_dataframe, localize_value
from tracker.utils.names import display_person_name


TARGET_TEMPLATES = {
    "zh-TW": """official_website|官方網站|https://example.gov
press_release_page|新聞稿頁面|https://example.gov/news
rss_feed|RSS 訂閱|https://example.gov/rss.xml
hearing_page|聽證會頁面|https://example.gov/hearings
social_page|社群帳號或貼文頁|https://x.com/example
cspan_search_target|C-SPAN 搜尋頁或人物頁|https://www.c-span.org/search/?searchtype=Videos&sponsorid[]=1
activity_page|涉台活動頁面|https://example.gov/events
media_search_target|媒體追蹤|https://news.google.com/rss/search?q=Taiwan+Official+Name
activity_media_target|涉台活動媒體搜尋|https://news.google.com/rss/search?q=Taiwan+Official+Name+visit""",
    "en": """official_website|Official website|https://example.gov
press_release_page|Press releases|https://example.gov/news
rss_feed|RSS feed|https://example.gov/rss.xml
hearing_page|Hearings|https://example.gov/hearings
social_page|Social profile or post page|https://x.com/example
cspan_search_target|C-SPAN search or person page|https://www.c-span.org/search/?searchtype=Videos&sponsorid[]=1
activity_page|Taiwan-related activity page|https://example.gov/events
media_search_target|Media tracking|https://news.google.com/rss/search?q=Taiwan+Official+Name
activity_media_target|Taiwan activity media search|https://news.google.com/rss/search?q=Taiwan+Official+Name+visit""",
}


def _parse_targets(raw: str) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3:
            continue
        targets.append({"target_type": parts[0], "target_name": parts[1], "target_url": parts[2]})
    return targets


def _serialize_targets(targets: list) -> str:
    return "\n".join(f"{item.target_type}|{item.target_name or ''}|{item.target_url}" for item in targets if item.is_active)


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["trackers"])
    with session_scope() as session:
        service = TrackerService(session)
        trackers = service.list_trackers()
        people = service.list_people()
        if not people:
            st.warning(labels["no_people_found_sync_first"])
            st.dataframe(
                localize_dataframe(pd.DataFrame([{"name": item.name, "status": item.status} for item in trackers]), lang, value_columns=["status"]),
                use_container_width=True,
            )
            return

        tracker_options = {labels["new_tracker"]: None}
        tracker_options.update({item.name: item.id for item in trackers})
        selected_label = st.selectbox(labels["tracker_label"], list(tracker_options.keys()))
        selected_id = tracker_options[selected_label]
        selected_tracker = service.get_tracker(selected_id) if selected_id else None

        person_options = {
            display_person_name(person.full_name, person.given_name, person.family_name): person.id
            for person in people
        }
        default_person_index = 0
        if selected_tracker and person_options:
            keys = list(person_options.values())
            default_person_index = keys.index(selected_tracker.person_id) if selected_tracker.person_id in keys else 0

        with st.form("tracker_form"):
            person_label = st.selectbox(labels["person"], list(person_options.keys()), index=default_person_index if person_options else None)
            name = st.text_input(labels["tracker_name"], value=selected_tracker.name if selected_tracker else "")
            status_options = ["active", "paused"]
            status = st.selectbox(
                labels["status"],
                status_options,
                index=0 if not selected_tracker or selected_tracker.status == "active" else 1,
                format_func=lambda value: str(localize_value(value, lang)),
            )
            include_primary = st.checkbox(labels["include_primary"], value=selected_tracker.include_primary_sources if selected_tracker else True)
            include_media = st.checkbox(labels["include_media"], value=selected_tracker.include_media_reports if selected_tracker else True)
            schedule_cron = st.text_input(labels["schedule_note"], value=selected_tracker.schedule_cron or "" if selected_tracker else "")
            targets_text = st.text_area(
                labels["targets"],
                value=_serialize_targets(selected_tracker.targets) if selected_tracker else TARGET_TEMPLATES.get(lang, TARGET_TEMPLATES["en"]),
                height=180,
            )
            submitted = st.form_submit_button(labels["save_tracker"])

        if submitted and person_options:
            tracker = service.create_or_update_tracker(
                tracker_id=selected_id,
                person_id=person_options[person_label],
                name=name or f"{person_label} tracker",
                status=status,
                include_primary_sources=include_primary,
                include_media_reports=include_media,
                schedule_cron=schedule_cron or None,
                targets=_parse_targets(targets_text),
            )
            st.success(f"{labels['tracker_saved']} {tracker.name}")

        if selected_tracker and st.button(labels["run_tracker_now"]):
            result = TrackerSyncService(session).sync_tracker(selected_tracker)
            st.json(result.__dict__)

        if selected_tracker:
            targets_df = pd.DataFrame(
                [
                    {
                        "target_name": item.target_name,
                        "target_type": item.target_type,
                        "target_url": item.target_url,
                        "parser_identity": item.parser_identity or "",
                        "is_active": item.is_active,
                        "last_checked_at": item.last_checked_at,
                    }
                    for item in selected_tracker.targets
                ]
            )
            if not targets_df.empty:
                st.subheader(labels["tracker_targets"])
                st.dataframe(
                    localize_dataframe(
                        targets_df,
                        lang,
                        value_columns=["target_type", "is_active"],
                    ),
                    use_container_width=True,
                )

        data = pd.DataFrame(
            [{"name": item.name, "status": item.status, "last_run_at": item.last_run_at, "last_run_status": item.last_run_status} for item in service.list_trackers()]
        )
        st.dataframe(localize_dataframe(data, lang, value_columns=["status", "last_run_status"]), use_container_width=True)
