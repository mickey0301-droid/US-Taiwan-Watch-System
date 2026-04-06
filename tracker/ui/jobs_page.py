from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import SyncRun
from tracker.scheduler import JOB_REGISTRY
from tracker.ui.display import localize_dataframe
from tracker.ui.display import localize_value


JOB_RESULT_KEY_LABELS = {
    "zh-TW": {
        "status": "狀態",
        "job_name": "工作名稱",
        "records_found": "找到筆數",
        "records_updated": "更新筆數",
        "records_created": "新增筆數",
        "people_scanned": "掃描人物數",
        "portraits_updated": "更新照片數",
        "targets_added": "新增目標數",
        "social_targets_added": "新增社群目標數",
        "source_counts": "來源統計",
        "errors": "錯誤",
        "error_count": "錯誤數",
        "metadata": "附加資訊",
        "results": "結果",
        "validation_log": "過濾紀錄",
        "validation_count": "過濾筆數",
        "rejected_name": "被過濾名稱",
        "reason": "過濾原因",
        "category": "規則類型",
        "collection_year": "蒐集年份",
        "limit": "筆數上限",
        "platform": "平台",
    },
    "en": {
        "status": "Status",
        "job_name": "Job name",
        "records_found": "Records found",
        "records_updated": "Records updated",
        "records_created": "Records created",
        "people_scanned": "People scanned",
        "portraits_updated": "Portraits updated",
        "targets_added": "Targets added",
        "social_targets_added": "Social targets added",
        "source_counts": "Source counts",
        "errors": "Errors",
        "error_count": "Error count",
        "metadata": "Metadata",
        "results": "Results",
        "validation_log": "Validation log",
        "validation_count": "Validation count",
        "rejected_name": "Rejected name",
        "reason": "Reason",
        "category": "Rule type",
        "collection_year": "Collection year",
        "limit": "Limit",
        "platform": "Platform",
    },
}


def _localize_job_result(value: object, lang: str) -> object:
    if isinstance(value, dict):
        return {
            JOB_RESULT_KEY_LABELS.get(lang, {}).get(str(key), str(key)): _localize_job_result(item, lang)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_localize_job_result(item, lang) for item in value]
    return localize_value(value, lang)


def _validation_columns(lang: str) -> dict[str, str]:
    if lang == "zh-TW":
        return {
            "job_name": "工作名稱",
            "started_at": "開始時間",
            "rejected_name": "被過濾名稱",
            "reason": "過濾原因",
            "category": "規則類型",
        }
    return {
        "job_name": "Job name",
        "started_at": "Started at",
        "rejected_name": "Rejected name",
        "reason": "Reason",
        "category": "Rule type",
    }


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["jobs_scheduler"])

    row1_col1, row1_col2 = st.columns(2)
    row2_col1, row2_col2 = st.columns(2)
    row3_col1, row3_col2 = st.columns(2)
    row4_col1, row4_col2 = st.columns(2)
    row5_col1, row5_col2 = st.columns(2)
    row6_col1, row6_col2 = st.columns(2)
    row7_col1, row7_col2 = st.columns(2)
    row8_col1, row8_col2 = st.columns(2)
    row9_col1, row9_col2 = st.columns(2)

    if row1_col1.button(labels["run_sample_sync"], use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["sync_officials"](), lang))
    if row1_col2.button(labels["run_profile_enrichment"], use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["enrich_profiles"](), lang))

    x_backfill_label = labels.get("run_x_profile_backfill", "補全 X 社群帳號" if lang == "zh-TW" else "Backfill X profiles")
    if row2_col1.button(x_backfill_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["backfill_x_profiles"](), lang))
    if row2_col2.button(labels["run_portrait_backfill"], use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["backfill_portraits"](), lang))

    discovery_label = labels.get(
        "run_official_discovery",
        "批次準備官方資料搜尋" if lang == "zh-TW" else "Prepare official discovery",
    )
    if row3_col1.button(discovery_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["discover_official_sources"](), lang))
    predecessor_label = labels.get(
        "run_wikipedia_predecessors",
        "從現任維基頁擴充前任人物" if lang == "zh-TW" else "Seed predecessors from current Wikipedia pages",
    )
    if row3_col2.button(predecessor_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["seed_wikipedia_predecessors"](), lang))

    roster_label = labels.get(
        "run_historical_roster_seed",
        "建立歷史名單框架" if lang == "zh-TW" else "Seed historical rosters",
    )
    if row4_col1.button(roster_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["seed_historical_rosters"](), lang))
    taiwan_bootstrap_label = labels.get(
        "run_current_taiwan_bootstrap",
        "建立 2026 Taiwan 追蹤器" if lang == "zh-TW" else "Bootstrap 2026 Taiwan trackers",
    )
    if row4_col2.button(taiwan_bootstrap_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["bootstrap_current_taiwan_2026"](), lang))

    x_candidate_label = "建立現任聯邦人物 X 候選搜尋" if lang == "zh-TW" else "Seed current federal X search links"
    if row5_col1.button(x_candidate_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["seed_current_legislator_x_candidates"](), lang))
    x_discovery_label = "解析現任聯邦人物 X 候選結果" if lang == "zh-TW" else "Discover current federal X candidates"
    if row5_col2.button(x_discovery_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["discover_current_legislator_x_candidates"](), lang))
    state_department_label = "同步國務院 Wikipedia 名單" if lang == "zh-TW" else "Sync State Department Wikipedia roster"
    if row6_col1.button(state_department_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["sync_state_department_wikipedia"](), lang))
    federal_department_label = "同步聯邦部門 Wikipedia 名單" if lang == "zh-TW" else "Sync federal department Wikipedia roster"
    if row6_col2.button(federal_department_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["sync_federal_department_wikipedia"](), lang))
    if row7_col1.button("同步媒體工作" if lang == "zh-TW" else "Run media sync", use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["sync_media"](), lang))
    if row7_col2.button("清理工作" if lang == "zh-TW" else "Run cleanup", use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["cleanup"](), lang))

    current_federal_background_label = "補全現任聯邦人物背景資料" if lang == "zh-TW" else "Enrich current federal people backgrounds"
    if row8_col1.button(current_federal_background_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["enrich_current_federal_backgrounds"](), lang))
    taiwan_chinese_sources_label = "建立台灣中文來源追蹤" if lang == "zh-TW" else "Bootstrap Taiwan Chinese source tracking"
    if row8_col2.button(taiwan_chinese_sources_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["bootstrap_taiwan_chinese_sources"](), lang))
    arizona_legislation_label = "同步 Arizona 涉台法案" if lang == "zh-TW" else "Sync Arizona Taiwan legislation"
    if row9_col1.button(arizona_legislation_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["seed_arizona_taiwan_legislation"](), lang))

    congress_detail_label = "è£œå…¨ Congress.gov æ³•æ¡ˆè©³æƒ…" if lang == "zh-TW" else "Enrich Congress.gov bill details"
    if row9_col2.button(congress_detail_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["enrich_congress_bill_details"](), lang))
    cleanup_legislation_people_label = "清理立法髒人名" if lang == "zh-TW" else "Clean malformed legislation people"
    if row7_col2.button(cleanup_legislation_people_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["cleanup_malformed_legislation_people"](), lang))

    with session_scope() as session:
        rows = session.execute(
            select(
                SyncRun.job_name,
                SyncRun.status,
                SyncRun.started_at,
                SyncRun.ended_at,
                SyncRun.records_found,
                SyncRun.records_created,
                SyncRun.records_updated,
                SyncRun.error_message,
                SyncRun.meta,
            )
            .order_by(SyncRun.started_at.desc())
            .limit(100)
        ).all()

    summary_df = pd.DataFrame(
        [
            {
                "job_name": row.job_name,
                "status": row.status,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
                "records_found": row.records_found,
                "records_created": row.records_created,
                "records_updated": row.records_updated,
                "error": row.error_message,
            }
            for row in rows
        ]
    )
    st.dataframe(localize_dataframe(summary_df, lang, value_columns=["status"]), use_container_width=True)

    validation_rows: list[dict[str, object]] = []
    for row in rows:
        meta = row.meta or {}
        for entry in meta.get("validation_log", []):
            validation_rows.append(
                {
                    "job_name": row.job_name,
                    "started_at": row.started_at,
                    "rejected_name": entry.get("rejected_name"),
                    "reason": entry.get("reason"),
                    "category": entry.get("category"),
                }
            )

    if validation_rows:
        st.subheader("最近被過濾的人名" if lang == "zh-TW" else "Recently filtered names")
        validation_df = pd.DataFrame(validation_rows).rename(columns=_validation_columns(lang))
        st.dataframe(validation_df, use_container_width=True)
