from __future__ import annotations

from datetime import datetime

import streamlit as st
from sqlalchemy import or_, select

from tracker.db import session_scope
from tracker.models import SyncRun


JOB_NAME_ZH: dict[str, str] = {
    "federal_subcabinet": "聯邦部會名單同步",
    "federal_department_main_wikipedia": "聯邦部會首長同步",
    "sync_federal_house_wikipedia": "聯邦眾議員同步",
    "sync_federal_senators_wikipedia": "聯邦參議員同步",
    "sync_state_department_wikipedia": "國務院名單同步",
    "sync_federal_military_official_pages": "聯邦軍職名單同步",
    "sync_combatant_command_official_pages": "作戰司令部名單同步",
}


def _localize_job_name(job_name: str, source_name: str | None) -> str:
    if not job_name:
        return source_name or "系統同步"
    return JOB_NAME_ZH.get(job_name, source_name or job_name)


def _status_text(status: str, lang: str) -> str:
    status = (status or "").lower()
    if lang == "zh-TW":
        return {
            "success": "成功",
            "failed": "失敗",
            "partial_failure": "部分失敗",
            "running": "執行中",
        }.get(status, status or "未知")
    return {
        "success": "Success",
        "failed": "Failed",
        "partial_failure": "Partial failure",
        "running": "Running",
    }.get(status, status or "Unknown")


def _fmt_time(ts: datetime | None) -> str:
    if not ts:
        return "-"
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels.get("changelog", "更新日誌" if lang == "zh-TW" else "Changelog"))
    st.caption("系統偵測到有資料變動時會自動寫入，依時間由新到舊。"
               if lang == "zh-TW"
               else "Auto-generated when data changes are detected, sorted newest to oldest.")

    with session_scope() as session:
        runs = (
            session.execute(
                select(SyncRun)
                .where(
                    or_(
                        SyncRun.records_created > 0,
                        SyncRun.records_updated > 0,
                        SyncRun.records_deactivated > 0,
                    )
                )
                .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
                .limit(200)
            )
            .scalars()
            .all()
        )

    if not runs:
        st.info("目前還沒有偵測到變動紀錄。" if lang == "zh-TW" else "No change logs yet.")
        return

    for run in runs:
        updated_at = run.ended_at or run.started_at
        job_name = _localize_job_name(run.job_name, run.source_name)
        if lang == "zh-TW":
            title = f"{job_name}：偵測到資料變動"
            summary = (
                f"新增 {int(run.records_created or 0)} 筆、"
                f"更新 {int(run.records_updated or 0)} 筆、"
                f"停用 {int(run.records_deactivated or 0)} 筆。"
            )
            status_text = _status_text(run.status, lang)
            status_line = f"狀態：{status_text}"
        else:
            title = f"{run.job_name}: Data changes detected"
            summary = (
                f"Created {int(run.records_created or 0)}, "
                f"updated {int(run.records_updated or 0)}, "
                f"deactivated {int(run.records_deactivated or 0)}."
            )
            status_line = f"Status: {_status_text(run.status, lang)}"

        with st.container(border=True):
            st.markdown(f"**{_fmt_time(updated_at)}**")
            st.markdown(f"**{title}**")
            st.write(summary)
            st.caption(status_line)
