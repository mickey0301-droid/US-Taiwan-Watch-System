from __future__ import annotations

import pandas as pd


COLUMN_LABELS = {
    "zh-TW": {
        "name": "姓名",
        "office": "職位",
        "level": "層級",
        "party": "黨籍",
        "status": "狀態",
        "source_type": "來源類型",
        "review_status": "審核狀態",
        "last_run_status": "最近執行狀態",
        "last_run_at": "最近執行時間",
        "job_name": "工作名稱",
        "started_at": "開始時間",
        "ended_at": "結束時間",
        "records_found": "找到筆數",
        "records_created": "新增筆數",
        "records_updated": "更新筆數",
        "error": "錯誤訊息",
        "relevance_score": "相關分數",
        "event_source_preference": "事件代表來源",
        "source_count": "來源數量",
        "matched_keywords": "符合關鍵字",
        "source_url": "來源網址",
        "source_title": "來源標題",
        "is_primary": "是否主要來源",
        "published_at": "發布時間",
        "title": "標題",
        "role": "角色",
        "start_date": "開始日期",
        "end_date": "結束日期",
        "target_name": "目標名稱",
        "target_type": "目標類型",
        "target_url": "目標網址",
        "parser_identity": "來源標記",
        "is_active": "是否啟用",
        "last_checked_at": "最近檢查時間",
    },
    "en": {
        "name": "Name",
        "office": "Office",
        "level": "Level",
        "party": "Party",
        "status": "Status",
        "source_type": "Source type",
        "review_status": "Review status",
        "last_run_status": "Last run status",
        "last_run_at": "Last run at",
        "job_name": "Job name",
        "started_at": "Started at",
        "ended_at": "Ended at",
        "records_found": "Records found",
        "records_created": "Records created",
        "records_updated": "Records updated",
        "error": "Error",
        "relevance_score": "Relevance score",
        "event_source_preference": "Representative source",
        "source_count": "Source count",
        "matched_keywords": "Matched keywords",
        "source_url": "Source URL",
        "source_title": "Source title",
        "is_primary": "Primary source",
        "published_at": "Published at",
        "title": "Title",
        "role": "Role",
        "start_date": "Start date",
        "end_date": "End date",
        "target_name": "Target name",
        "target_type": "Target type",
        "target_url": "Target URL",
        "parser_identity": "Parser identity",
        "is_active": "Active",
        "last_checked_at": "Last checked at",
    },
}


VALUE_LABELS = {
    "zh-TW": {
        "all": "全部",
        "active": "啟用",
        "paused": "暫停",
        "current": "現任",
        "former": "前任",
        "unknown": "未知",
        "official": "官方",
        "official_api": "官方 API",
        "social": "社群",
        "media": "媒體",
        "cspan": "C-SPAN",
        "wikipedia": "維基百科",
        "secondary": "次級來源",
        "secondary_video": "次級影音來源",
        "pending": "待審核",
        "needs_review": "需複核",
        "confirmed": "已確認",
        "dismissed": "已排除",
        "success": "成功",
        "failed": "失敗",
        "running": "執行中",
        "house": "眾議院",
        "senate": "參議院",
        "federal": "聯邦",
        "state": "州",
        "local": "地方",
        "other": "其他",
        "legislative": "立法",
        "executive": "行政",
        "judicial": "司法",
        "country": "國家",
        "county": "郡縣",
        "city": "城市",
        "true": "是",
        "false": "否",
        "official_website": "官方網站",
        "press_release_page": "新聞稿頁面",
        "rss_feed": "RSS 訂閱",
        "hearing_page": "聽證會頁面",
        "social_page": "社群頁面",
        "cspan_search_target": "C-SPAN 搜尋目標",
        "activity_page": "活動頁面",
        "media_search_target": "媒體搜尋目標",
        "activity_media_target": "活動媒體搜尋目標",
    },
    "en": {
        "all": "All",
        "active": "Active",
        "paused": "Paused",
        "current": "Current",
        "former": "Former",
        "unknown": "Unknown",
        "official": "Official",
        "official_api": "Official API",
        "social": "Social",
        "media": "Media",
        "cspan": "C-SPAN",
        "wikipedia": "Wikipedia",
        "secondary": "Secondary",
        "secondary_video": "Secondary video",
        "pending": "Pending",
        "needs_review": "Needs review",
        "confirmed": "Confirmed",
        "dismissed": "Dismissed",
        "success": "Success",
        "failed": "Failed",
        "running": "Running",
        "house": "House",
        "senate": "Senate",
        "federal": "Federal",
        "state": "State",
        "local": "Local",
        "other": "Other",
        "legislative": "Legislative",
        "executive": "Executive",
        "judicial": "Judicial",
        "country": "Country",
        "county": "County",
        "city": "City",
        "true": "Yes",
        "false": "No",
        "official_website": "Official website",
        "press_release_page": "Press release page",
        "rss_feed": "RSS feed",
        "hearing_page": "Hearing page",
        "social_page": "Social page",
        "cspan_search_target": "C-SPAN search target",
        "activity_page": "Activity page",
        "media_search_target": "Media search target",
        "activity_media_target": "Activity media target",
    },
}


def localize_value(value: object, lang: str) -> object:
    if value is None:
        return value
    text = str(value)
    normalized = text.strip()
    lower = normalized.lower()
    return VALUE_LABELS.get(lang, {}).get(lower, value)


def localize_dataframe(dataframe: pd.DataFrame, lang: str, value_columns: list[str] | None = None) -> pd.DataFrame:
    localized = dataframe.copy()
    for column in value_columns or []:
        if column in localized.columns:
            localized[column] = localized[column].map(lambda value: localize_value(value, lang))
    column_map = COLUMN_LABELS.get(lang, {})
    localized = localized.rename(columns={column: column_map.get(column, column) for column in localized.columns})
    return localized


def source_badge_style(value: object) -> str:
    text = str(value or "")
    if "C-SPAN" in text and ("影片" in text or "Video" in text):
        return "background-color: #dbeafe; color: #1d4ed8; font-weight: 700;"
    if "C-SPAN" in text and ("片段" in text or "Clip" in text):
        return "background-color: #ccfbf1; color: #0f766e; font-weight: 700;"
    if "C-SPAN" in text and ("人物頁" in text or "Person Page" in text):
        return "background-color: #fef3c7; color: #92400e; font-weight: 700;"
    if "C-SPAN" in text and ("搜尋結果" in text or "Search Result" in text):
        return "background-color: #ede9fe; color: #6d28d9; font-weight: 700;"
    return ""


def style_source_columns(dataframe: pd.DataFrame, source_columns: list[str]):
    styler = dataframe.style
    for column in source_columns:
        if column in dataframe.columns:
            styler = styler.map(source_badge_style, subset=[column])
    return styler
