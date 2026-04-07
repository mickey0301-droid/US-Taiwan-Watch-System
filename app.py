from __future__ import annotations

from pathlib import Path

import streamlit as st

from tracker.config import get_settings, use_google_sheet_primary_mode
from tracker.database_setup import ensure_database_ready
from tracker.ui import (
    dashboard,
    jobs_page,
    legislation_page,
    notifications_page,
    officials_page,
    person_page,
    review_page,
    settings_page,
    state_territory_page,
    trackers_page,
)

try:
    from tracker.utils import text as text_utils
except Exception:  # pragma: no cover - keep app bootable on partially deployed environments
    text_utils = None


def _install_streamlit_text_repair() -> None:
    installer = getattr(text_utils, "install_streamlit_text_repair", None)
    if callable(installer):
        installer(st)


def _repair_nested_text(value):
    repairer = getattr(text_utils, "repair_nested_text", None)
    if callable(repairer):
        return repairer(value)
    return value


LABELS = {
    "zh-TW": {
        "app_title": "US Taiwan Watch",
        "dashboard": "首頁",
        "officials": "官員名單",
        "person_detail": "人物",
        "trackers": "追蹤器",
        "review_queue": "事件",
        "state_territory": "州/海外領地",
        "legislation": "立法",
        "jobs_scheduler": "排程與工作",
        "notifications": "通知",
        "settings": "設定",
        "total_officials": "官員總數",
        "total_trackers": "追蹤器總數",
        "recent_statements": "近期聲明",
        "recent_sync_runs": "近期同步",
        "recent_alerts": "近期提醒",
        "federal_official_events": "聯邦官員事件",
        "congress_member_events": "國會議員事件",
        "state_official_events": "州官員事件",
        "state_legislator_events": "州議員事件",
        "search_name": "搜尋姓名",
        "aliases": "別名",
        "office_history": "職務歷程",
        "tracker_status": "追蹤狀態",
        "person_not_found": "找不到此人物。",
        "run_sample_sync": "執行官方名單同步",
        "run_profile_enrichment": "批次補全背景資料",
        "run_portrait_backfill": "補全人物照片",
        "run_tracker_sync": "執行人物追蹤同步",
        "wiki_import": "從維基名單匯入人物",
        "wiki_url": "維基名單網址",
        "office_name": "職位名稱",
        "role_title": "角色名稱",
        "level": "層級",
        "state": "州",
        "department": "部門",
        "branch": "部門",
        "chamber": "議院",
        "jurisdiction_name": "轄區名稱",
        "jurisdiction_type": "轄區類型",
        "appointment_status": "任職狀態",
        "auto_create_trackers": "自動建立 Taiwan 媒體追蹤器",
        "import_list": "匯入名單",
        "status_filter": "狀態",
        "all": "全部",
        "unknown": "未知",
        "no_people_loaded": "這個類別目前還沒有人物資料。",
        "select_person": "選擇人物",
        "person_category": "人物層級",
        "profile_status": "資料狀態",
        "seed_source": "種子來源",
        "primary_source": "目前主要來源",
        "official_page": "官方頁面",
        "date_of_birth": "生日",
        "place_of_birth": "出生地",
        "ethnicity": "族裔",
        "religion": "宗教",
        "education": "學歷",
        "career_history": "過去經歷",
        "field_source": "資料來源",
        "recent_taiwan_statements": "近期台灣相關發言",
        "browse_by_year": "依年份瀏覽發言",
        "recent_media_reports": "近期媒體報導",
        "sources": "來源",
        "representative_source": "代表來源",
        "attached_sources": "附加來源數",
        "keywords": "關鍵字",
        "confirm_related": "確認為台灣相關",
        "needs_review": "保留待審",
        "dismiss": "排除",
        "social_profiles": "社群帳號",
        "no_portrait": "尚無官方肖像，請加入或同步官方網站 target。",
        "no_recent_statements": "目前尚未蒐集到台灣相關發言。",
        "no_historical_statements": "目前尚無歷年台灣相關發言。",
        "last_sync": "最近同步",
        "manual_event_ingest": "手動新增事件",
        "manual_event_url": "事件網址（貼上官方新聞稿 / 活動頁）",
        "manual_event_submit": "抓取並新增事件",
        "manual_event_url_required": "請先輸入事件網址。",
        "manual_event_ingesting": "正在抓取並解析網址...",
        "manual_event_created": "已新增事件：{title}",
        "manual_event_updated": "已更新既有事件：{title}",
        "manual_event_failed": "新增事件失敗",
        "no_people_found_sync_first": "目前還沒有人物資料，請先同步官員名單。",
        "new_tracker": "新增追蹤器",
        "tracker_label": "追蹤器",
        "tracker_name": "追蹤器名稱",
        "tracker_targets": "追蹤目標明細",
        "status": "狀態",
        "include_primary": "包含第一手官方發言",
        "include_media": "包含媒體報導",
        "schedule_note": "排程備註 / cron 預留欄位",
        "targets": "追蹤目標（每行一筆：type|name|url）",
        "job_result": "執行結果",
        "save_tracker": "儲存追蹤器",
        "tracker_saved": "已儲存追蹤器",
        "run_tracker_now": "立即執行此追蹤器",
        "person": "人物",
        "settings_yaml": "設定檔 settings.yaml",
        "keywords_yaml": "關鍵字檔 keywords.yaml",
        "source_registry_yaml": "來源登錄 source_registry.yaml",
        "statement": "事件 / 發言",
        "year": "年份",
    },
    "en": {
        "app_title": "US Taiwan Watch",
        "dashboard": "Home",
        "officials": "Officials",
        "person_detail": "Person Detail",
        "trackers": "Trackers",
        "review_queue": "Events",
        "state_territory": "State / Territory",
        "legislation": "Legislation",
        "jobs_scheduler": "Jobs / Scheduler",
        "notifications": "Notifications",
        "settings": "Settings",
        "total_officials": "Total officials",
        "total_trackers": "Total trackers",
        "recent_statements": "Recent statements",
        "recent_sync_runs": "Recent sync runs",
        "recent_alerts": "Recent alerts",
        "federal_official_events": "Federal official events",
        "congress_member_events": "Congress member events",
        "state_official_events": "State official events",
        "state_legislator_events": "State legislator events",
        "search_name": "Search by name",
        "aliases": "Aliases",
        "office_history": "Office history",
        "tracker_status": "Tracker status",
        "person_not_found": "Person not found.",
        "run_sample_sync": "Run officials sync",
        "run_profile_enrichment": "Run profile enrichment",
        "run_portrait_backfill": "Backfill portraits",
        "run_tracker_sync": "Run tracker sync",
        "wiki_import": "Import people from Wikipedia list",
        "wiki_url": "Wikipedia list URL",
        "office_name": "Office name",
        "role_title": "Role title",
        "level": "Level",
        "state": "State",
        "department": "Department",
        "branch": "Branch",
        "chamber": "Chamber",
        "jurisdiction_name": "Jurisdiction name",
        "jurisdiction_type": "Jurisdiction type",
        "appointment_status": "Appointment status",
        "auto_create_trackers": "Auto-create Taiwan media tracker",
        "import_list": "Import list",
        "status_filter": "Status",
        "all": "All",
        "unknown": "Unknown",
        "no_people_loaded": "No people are loaded for this category yet.",
        "select_person": "Select person",
        "person_category": "Person category",
        "profile_status": "Profile status",
        "seed_source": "Seed source",
        "primary_source": "Current primary source",
        "official_page": "Official page",
        "date_of_birth": "Date of birth",
        "place_of_birth": "Place of birth",
        "ethnicity": "Ethnicity",
        "religion": "Religion",
        "education": "Education",
        "career_history": "Past experience",
        "field_source": "Source",
        "recent_taiwan_statements": "Recent Taiwan-related statements",
        "browse_by_year": "Browse statements by year",
        "recent_media_reports": "Recent media reports",
        "sources": "Sources",
        "representative_source": "Representative source",
        "attached_sources": "Attached sources",
        "keywords": "Keywords",
        "confirm_related": "Confirm Taiwan-related",
        "needs_review": "Needs review",
        "dismiss": "Dismiss",
        "social_profiles": "Social profiles",
        "no_portrait": "No official portrait yet. Add or sync an official website target.",
        "no_recent_statements": "No Taiwan-related statements collected yet.",
        "no_historical_statements": "No historical Taiwan-related statements available yet.",
        "last_sync": "Last sync",
        "manual_event_ingest": "Add Event URL",
        "manual_event_url": "Event URL (official press release or activity page)",
        "manual_event_submit": "Fetch and Add Event",
        "manual_event_url_required": "Please enter an event URL first.",
        "manual_event_ingesting": "Fetching and parsing URL...",
        "manual_event_created": "Event added: {title}",
        "manual_event_updated": "Existing event updated: {title}",
        "manual_event_failed": "Failed to add event",
        "no_people_found_sync_first": "No people found yet. Run officials sync first.",
        "new_tracker": "New tracker",
        "tracker_label": "Tracker",
        "tracker_name": "Tracker name",
        "tracker_targets": "Tracker targets",
        "status": "Status",
        "include_primary": "Include primary-source statements",
        "include_media": "Include media reports",
        "schedule_note": "Schedule note / cron placeholder",
        "targets": "Targets (one per line: type|name|url)",
        "job_result": "Result",
        "save_tracker": "Save tracker",
        "tracker_saved": "Saved tracker",
        "run_tracker_now": "Run this tracker now",
        "person": "Person",
        "settings_yaml": "settings.yaml",
        "keywords_yaml": "keywords.yaml",
        "source_registry_yaml": "source_registry.yaml",
        "statement": "Statement / Event",
        "year": "Year",
    },
}


PAGES = {
    "dashboard": dashboard.render,
    "person_detail": person_page.render,
    "state_territory": state_territory_page.render,
    "review_queue": review_page.render,
    "legislation": legislation_page.render,
    "officials": officials_page.render,
    "trackers": trackers_page.render,
    "jobs_scheduler": jobs_page.render,
    "notifications": notifications_page.render,
    "settings": settings_page.render,
}

SIDEBAR_LOGO_PATH = Path(__file__).resolve().parent / "tracker" / "ui" / "assets" / "utw_sidebar_logo.svg"

NAV_PAGE_ORDER = [
    "dashboard",
    "person_detail",
    "state_territory",
    "review_queue",
    "legislation",
    "officials",
    "trackers",
    "jobs_scheduler",
    "notifications",
    "settings",
]

GOOGLE_SHEET_PRIMARY_PAGES = [
    "dashboard",
    "person_detail",
    "state_territory",
    "review_queue",
    "legislation",
    "officials",
]


def _render_sidebar_nav_cards(
    page_options: list[str],
    current_page: str,
    labels: dict[str, object],
) -> str:
    st.sidebar.markdown(
        """
<style>
section[data-testid="stSidebar"] div[data-testid="stButton"] > button {
    min-height: 2.8rem;
    border-radius: 0.6rem;
    text-align: left;
    font-weight: 600;
}
</style>
""",
        unsafe_allow_html=True,
    )
    selected_page = current_page
    st.sidebar.markdown("**導覽 / Navigation**")
    for page in page_options:
        with st.sidebar.container(border=True):
            is_current = page == selected_page
            label = str(labels.get(page, page))
            if is_current:
                st.caption("目前頁面")
            if st.button(
                label,
                key=f"sidebar-nav-card-{page}",
                use_container_width=True,
                type="primary" if is_current else "secondary",
            ):
                selected_page = page
    return selected_page


def main() -> None:
    _install_streamlit_text_repair()
    settings = get_settings()
    st.set_page_config(page_title=settings.app_name, layout="wide")
    db_ready = ensure_database_ready()
    if not db_ready.ok:
        st.title("US Taiwan Watch")
        st.error(
            "Cloud database connection failed."
            if settings.default_language != "zh-TW"
            else "雲端主資料庫連線失敗。"
        )
        if db_ready.safe_database_url:
            st.caption(
                f"Database target: {db_ready.safe_database_url}"
                if settings.default_language != "zh-TW"
                else f"目前目標資料庫：{db_ready.safe_database_url}"
            )
        if db_ready.message:
            st.code(db_ready.message)
        st.info(
            "Check TRACKER_DATABASE_URL, database host allowlist, credentials, and sslmode."
            if settings.default_language != "zh-TW"
            else "請檢查 TRACKER_DATABASE_URL、資料庫主機白名單、帳密，以及 sslmode 設定。"
        )
        return

    language = st.session_state.get("ui_language", settings.default_language)
    if language not in settings.supported_languages:
        language = settings.default_language

    labels = _repair_nested_text(LABELS[language])
    if SIDEBAR_LOGO_PATH.exists():
        st.sidebar.image(str(SIDEBAR_LOGO_PATH), use_column_width=True)
    st.sidebar.markdown("## US Taiwan Watch")
    google_sheet_primary = use_google_sheet_primary_mode()
    query_page = st.query_params.get("page")
    if query_page in PAGES and "sidebar_nav_radio" not in st.session_state:
        st.session_state["sidebar_nav_radio"] = str(query_page)
    nav_page_order = GOOGLE_SHEET_PRIMARY_PAGES if google_sheet_primary else NAV_PAGE_ORDER
    page_options = [page for page in nav_page_order if page in PAGES]
    current_page = st.session_state.get("sidebar_nav_radio", "dashboard")
    if current_page not in PAGES or current_page not in page_options:
        current_page = "dashboard"
        st.session_state["sidebar_nav_radio"] = current_page

    page_key = _render_sidebar_nav_cards(page_options, current_page, labels)
    st.session_state["sidebar_nav_radio"] = page_key
    if google_sheet_primary:
        st.sidebar.caption("Google Sheet-first mode")
    if st.query_params.get("page") != page_key:
        st.query_params["page"] = page_key
    if page_key != "person_detail" and "person_id" in st.query_params:
        del st.query_params["person_id"]

    selected_language = st.sidebar.selectbox(
        "語言 / Language",
        settings.supported_languages,
        index=settings.supported_languages.index(language),
        key="ui_language_selector",
    )
    if selected_language != language:
        st.session_state["ui_language"] = selected_language
        st.rerun()

    PAGES[page_key](selected_language, _repair_nested_text(LABELS[selected_language]))


if __name__ == "__main__":
    main()
