from __future__ import annotations

from datetime import datetime

import streamlit as st


CHANGELOG_ENTRIES = [
    {
        "updated_at": "2026-04-08 14:20",
        "title_zh": "首頁錯誤修復",
        "title_en": "Dashboard error fix",
        "summary_zh": "修正首頁在部分語系標籤缺漏時會出現 KeyError 的問題。",
        "summary_en": "Fixed a KeyError on the dashboard when some locale label keys are missing.",
    },
    {
        "updated_at": "2026-04-08 14:05",
        "title_zh": "軍職人物頁排序與中文化",
        "title_en": "Military person-page sorting and localization",
        "summary_zh": "軍職部門下拉改為中文顯示，並將部門與人物改為依層級排序。",
        "summary_en": "Localized military department filter labels and sorted departments/people by hierarchy.",
    },
    {
        "updated_at": "2026-04-08 13:50",
        "title_zh": "軍職資料可見性修復",
        "title_en": "Military data visibility fix",
        "summary_zh": "修正人物頁 fallback 條件，確保軍職資料不會因欄位缺漏被全部過濾。",
        "summary_en": "Adjusted person-page fallback filtering so military records are still shown when fields are incomplete.",
    },
]


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels.get("changelog", "更新日誌" if lang == "zh-TW" else "Changelog"))
    st.caption("依時間由新到舊" if lang == "zh-TW" else "Sorted from newest to oldest")

    sorted_entries = sorted(
        CHANGELOG_ENTRIES,
        key=lambda item: datetime.strptime(str(item.get("updated_at") or ""), "%Y-%m-%d %H:%M"),
        reverse=True,
    )
    for entry in sorted_entries:
        title = entry.get("title_zh") if lang == "zh-TW" else entry.get("title_en")
        summary = entry.get("summary_zh") if lang == "zh-TW" else entry.get("summary_en")
        with st.container(border=True):
            st.markdown(f"**{entry.get('updated_at')}**")
            st.markdown(f"**{title}**")
            st.write(summary)
