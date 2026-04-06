from __future__ import annotations

from tracker.models import StatementSource
from tracker.utils.source_types import source_bucket_label


SOURCE_TYPE_LABELS = {
    "zh-TW": {
        "cspan_video": "C-SPAN影片",
        "cspan_clip": "C-SPAN片段",
        "cspan_person_page": "C-SPAN人物頁",
        "cspan_search_result": "C-SPAN搜尋結果",
    },
    "en": {
        "cspan_video": "C-SPAN Video",
        "cspan_clip": "C-SPAN Clip",
        "cspan_person_page": "C-SPAN Person Page",
        "cspan_search_result": "C-SPAN Search Result",
    },
}


def source_label(source: StatementSource, lang: str, fallback: str) -> str:
    if source.source_type != "cspan":
        return source_bucket_label(source.source_type, source.source_url, lang)
    raw_payload = source.raw_payload or {}
    page_type = raw_payload.get("page_type")
    if not page_type:
        return source_bucket_label(source.source_type, source.source_url, lang)
    return SOURCE_TYPE_LABELS.get(lang, {}).get(page_type, fallback)


def statement_source_label(statement, lang: str, fallback: str) -> str:
    source_type = getattr(statement, "event_source_preference", None) or getattr(statement, "source_type", None)
    if source_type != "cspan":
        return source_bucket_label(source_type, getattr(statement, "source_url", None), lang)
    raw_payload = getattr(statement, "raw_payload", {}) or {}
    page_type = raw_payload.get("page_type")
    if not page_type:
        return source_bucket_label(source_type, getattr(statement, "source_url", None), lang)
    return SOURCE_TYPE_LABELS.get(lang, {}).get(page_type, fallback)
