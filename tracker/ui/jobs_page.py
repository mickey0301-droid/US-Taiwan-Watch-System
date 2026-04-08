from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import SyncRun
from tracker.services.manual_url_import_service import ManualUrlImportService
from tracker.services.dedupe_cleanup_service import DedupeCleanupService
from tracker.services.scheduled_collection_service import ScheduledCollectionService
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
    row10_col1, row10_col2 = st.columns(2)

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
    import_sheet_label = "匯入 Google Sheet 資料" if lang == "zh-TW" else "Import Google Sheet data"
    if row10_col1.button(import_sheet_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["import_google_sheet_data"](), lang))
    export_sheet_label = "同步本機資料到 Google Sheet" if lang == "zh-TW" else "Export local data to Google Sheet"
    if row10_col2.button(export_sheet_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["export_google_sheet_data"](), lang))
    cleanup_legislation_people_label = "清理立法髒人名" if lang == "zh-TW" else "Clean malformed legislation people"
    if row7_col2.button(cleanup_legislation_people_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["cleanup_malformed_legislation_people"](), lang))
    dedupe_records_label = "清理重複連結資料" if lang == "zh-TW" else "Dedupe records by URL"
    if row9_col2.button(dedupe_records_label, use_container_width=True):
        st.subheader(labels["job_result"])
        st.json(_localize_job_result(JOB_REGISTRY["dedupe_records_by_url"](), lang))

    st.divider()
    st.subheader("手動網址批次匯入" if lang == "zh-TW" else "Manual URL Batch Import")

    with st.form("manual_people_import_form"):
        st.markdown("**1. 人物匯入（可批次）**" if lang == "zh-TW" else "**1. People import (batch)**")
        people_urls = st.text_area(
            "人物網址（每行一筆）" if lang == "zh-TW" else "Person URLs (one per line)",
            height=120,
            key="manual_people_urls",
        )
        people_type = st.selectbox(
            "人物類型" if lang == "zh-TW" else "Person type",
            options=[
                ("auto", "自動判斷（AI+規則）" if lang == "zh-TW" else "Auto classify (AI + rules)"),
                ("federal_official", "聯邦官員" if lang == "zh-TW" else "Federal official"),
                ("federal_senator", "聯邦參議員" if lang == "zh-TW" else "Federal senator"),
                ("federal_house", "聯邦眾議員" if lang == "zh-TW" else "Federal house member"),
                ("state_official", "州政府官員" if lang == "zh-TW" else "State official"),
                ("state_legislator", "州議員" if lang == "zh-TW" else "State legislator"),
            ],
            format_func=lambda item: item[1],
            key="manual_people_type",
        )
        state_name = st.text_input("州名（州層級必填）" if lang == "zh-TW" else "State name (required for state scopes)", key="manual_people_state")
        chamber_hint = st.selectbox(
            "州議院提示（選填）" if lang == "zh-TW" else "State chamber hint (optional)",
            options=["", "senate", "house"],
            key="manual_people_chamber",
        )
        submit_people = st.form_submit_button("批次新增人物" if lang == "zh-TW" else "Batch add people")
    if submit_people:
        with session_scope() as session:
            service = ManualUrlImportService(session)
            result = service.import_people_from_urls(
                raw_urls=people_urls,
                person_type=people_type[0],
                state_name=state_name,
                chamber_hint=chamber_hint,
            )
            dedupe = DedupeCleanupService(session).cleanup_all()
            st.json(
                {
                    "created": result.created,
                    "updated": result.updated,
                    "failed": result.failed,
                    "dedupe": dedupe,
                    "items": result.items[:50] if result.items else [],
                }
            )

    with st.form("manual_events_import_form"):
        st.markdown("**2. 事件匯入（系統自動分類）**" if lang == "zh-TW" else "**2. Event import (auto-classified)**")
        event_urls = st.text_area(
            "事件網址（每行一筆）" if lang == "zh-TW" else "Event URLs (one per line)",
            height=120,
            key="manual_event_urls_batch",
        )
        submit_events = st.form_submit_button("批次新增事件" if lang == "zh-TW" else "Batch add events")
    if submit_events:
        with session_scope() as session:
            service = ManualUrlImportService(session)
            result = service.import_events_from_urls(raw_urls=event_urls)
            dedupe = DedupeCleanupService(session).cleanup_all()
            st.json(
                {
                    "created": result.created,
                    "updated": result.updated,
                    "failed": result.failed,
                    "dedupe": dedupe,
                    "items": result.items[:50] if result.items else [],
                }
            )

    with st.form("manual_legislation_import_form"):
        st.markdown("**3. 法案匯入（系統自動分類）**" if lang == "zh-TW" else "**3. Legislation import (auto-classified)**")
        legislation_urls = st.text_area(
            "法案網址（每行一筆）" if lang == "zh-TW" else "Legislation URLs (one per line)",
            height=120,
            key="manual_legislation_urls_batch",
        )
        submit_legislation = st.form_submit_button("批次新增法案" if lang == "zh-TW" else "Batch add legislation")
    if submit_legislation:
        with session_scope() as session:
            service = ManualUrlImportService(session)
            result = service.import_legislation_from_urls(raw_urls=legislation_urls)
            dedupe = DedupeCleanupService(session).cleanup_all()
            st.json(
                {
                    "created": result.created,
                    "updated": result.updated,
                    "failed": result.failed,
                    "dedupe": dedupe,
                    "items": result.items[:50] if result.items else [],
                }
            )

    dedupe_label = "執行全庫去重（人物/事件/法案）" if lang == "zh-TW" else "Run full dedupe (people/events/legislation)"
    if st.button(dedupe_label, use_container_width=True):
        with session_scope() as session:
            result = DedupeCleanupService(session).cleanup_all()
            st.json(result)

    st.divider()
    st.subheader("自動排程搜尋更新" if lang == "zh-TW" else "Scheduled auto update")
    with st.form("create_collection_schedule_form"):
        schedule_name = st.text_input("排程名稱" if lang == "zh-TW" else "Schedule name", value=f"auto-{datetime.utcnow().strftime('%Y%m%d-%H%M')}")
        entity_scope = st.selectbox(
            "更新範圍" if lang == "zh-TW" else "Entity scope",
            options=[
                ("all", "人物 + 事件 + 法案" if lang == "zh-TW" else "People + events + legislation"),
                ("people", "只更新人物" if lang == "zh-TW" else "People only"),
                ("events", "只更新事件" if lang == "zh-TW" else "Events only"),
                ("legislation", "只更新法案" if lang == "zh-TW" else "Legislation only"),
            ],
            format_func=lambda item: item[1],
        )
        person_scope = st.selectbox(
            "人物範圍（事件搜尋用）" if lang == "zh-TW" else "Person scope (for event search)",
            options=[
                ("all_federal", "全部聯邦人物" if lang == "zh-TW" else "All federal people"),
                ("federal_officials", "聯邦官員" if lang == "zh-TW" else "Federal officials"),
                ("federal_senators", "聯邦參議員" if lang == "zh-TW" else "Federal senators"),
                ("federal_house", "聯邦眾議員" if lang == "zh-TW" else "Federal house members"),
                ("all_current", "全部現任人物" if lang == "zh-TW" else "All current people"),
            ],
            format_func=lambda item: item[1],
        )
        default_year = datetime.utcnow().year
        schedule_year = st.number_input("年份" if lang == "zh-TW" else "Year", min_value=2000, max_value=2100, value=default_year, step=1)
        schedule_months = st.multiselect(
            "月份（可多選）" if lang == "zh-TW" else "Months (multiple)",
            options=list(range(1, 13)),
            default=[datetime.utcnow().month],
            format_func=lambda item: f"{item:02d}",
        )
        interval_minutes = st.number_input("執行間隔（分鐘）" if lang == "zh-TW" else "Run interval (minutes)", min_value=5, max_value=10080, value=1440, step=5)
        max_people = st.number_input("事件搜尋人數上限（0=不限）" if lang == "zh-TW" else "Event person limit (0 = unlimited)", min_value=0, max_value=5000, value=100, step=10)
        create_schedule = st.form_submit_button("新增排程" if lang == "zh-TW" else "Create schedule")
    if create_schedule:
        with session_scope() as session:
            service = ScheduledCollectionService(session)
            task = service.create_schedule(
                name=schedule_name,
                entity_scope=entity_scope[0],
                person_scope=person_scope[0],
                year=int(schedule_year),
                months=[int(item) for item in schedule_months] or [datetime.utcnow().month],
                interval_minutes=int(interval_minutes),
                max_people=(int(max_people) if int(max_people) > 0 else None),
            )
            st.success(("已新增排程" if lang == "zh-TW" else "Schedule created") + f" #{task.id}")

    with session_scope() as session:
        schedule_service = ScheduledCollectionService(session)
        schedules = schedule_service.list_schedules()
        if schedules:
            st.markdown("**已建立排程**" if lang == "zh-TW" else "**Saved schedules**")
            for task in schedules:
                col_a, col_b, col_c = st.columns([5, 2, 2])
                months_text = task.months_csv or "-"
                col_a.write(
                    f"#{task.id} | {task.name} | {task.entity_scope} | {task.person_scope} | "
                    f"{task.year or '-'}-{months_text} | every {task.interval_minutes}m | "
                    f"enabled={task.enabled} | last={task.last_status or '-'}"
                )
                if col_b.button("立即執行" if lang == "zh-TW" else "Run now", key=f"schedule-run-{task.id}", use_container_width=True):
                    result = schedule_service.run_schedule(task.id)
                    st.json(result)
                toggle_label = "停用" if task.enabled else "啟用"
                if col_c.button(toggle_label if lang == "zh-TW" else ("Disable" if task.enabled else "Enable"), key=f"schedule-toggle-{task.id}", use_container_width=True):
                    schedule_service.set_enabled(task.id, not task.enabled)
                    st.success("已更新排程狀態" if lang == "zh-TW" else "Schedule status updated")

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
