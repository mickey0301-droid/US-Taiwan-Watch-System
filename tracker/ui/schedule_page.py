from __future__ import annotations

from datetime import datetime, time

import pandas as pd
import streamlit as st

from tracker.db import session_scope
from tracker.services.scheduled_collection_service import DEFAULT_EVENT_DOMAINS, DEFAULT_TAIWAN_KEYWORDS, ScheduledCollectionService


PERSON_SCOPE_OPTIONS = [
    ("all_current", "全部現任人物"),
    ("all_federal", "全部聯邦人物"),
    ("federal_officials", "聯邦官員"),
    ("federal_legislators", "聯邦國會議員"),
    ("federal_senators", "聯邦參議員"),
    ("federal_house", "聯邦眾議員"),
    ("state_officials", "州政府官員"),
    ("state_legislators", "州議會議員"),
]


def _parse_csv_lines(text: str) -> list[str]:
    if not text:
        return []
    normalized = text.replace("\n", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _combine_dt(date_value, time_value) -> datetime:
    safe_time = time_value if isinstance(time_value, time) else time(0, 0)
    return datetime.combine(date_value, safe_time)


def _render_search_result(result: dict | None, result_type: str) -> None:
    if not result:
        return
    if result_type == "event":
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("找到幾則", int(result.get("found") or 0))
        c2.metric("新增", int(result.get("created") or 0))
        c3.metric("更新", int(result.get("updated") or 0))
        c4.metric("已去重", int(result.get("skipped_existing") or 0))
    if result_type == "legislation":
        c1, c2, c3 = st.columns(3)
        c1.metric("找到幾則", int(result.get("records_found") or 0))
        c2.metric("新增", int(result.get("records_created") or 0))
        c3.metric("更新", int(result.get("records_updated") or 0))
    st.json(result)
    if result_type == "event":
        items = list(result.get("items") or [])
        if items:
            st.markdown("**事件結果清單**")
            st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)
        else:
            st.caption("本次事件搜尋沒有可顯示的結果明細。")
    if result_type == "legislation":
        items = list(result.get("items") or [])
        if items:
            st.markdown("**法案結果清單**")
            st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)
        else:
            st.caption("本次法案搜尋沒有可顯示的結果明細。")


def _event_now_summary_text() -> str:
    result = st.session_state.get("schedule_event_now_last_result") or {"found": 0, "created": 0, "updated": 0, "skipped_existing": 0}
    return (
        f"找到 {int(result.get('found') or 0)} ｜ 新增 {int(result.get('created') or 0)} ｜ "
        f"更新 {int(result.get('updated') or 0)} ｜ 去重 {int(result.get('skipped_existing') or 0)}"
    )


def _congress_now_summary_text() -> str:
    result = st.session_state.get("schedule_congress_now_last_result") or {"records_found": 0, "records_created": 0, "records_updated": 0}
    return (
        f"找到 {int(result.get('records_found') or 0)} ｜ 新增 {int(result.get('records_created') or 0)} ｜ "
        f"更新 {int(result.get('records_updated') or 0)}"
    )


def _render_schedule_table(lang: str) -> None:
    with session_scope() as session:
        service = ScheduledCollectionService(session)
        schedules = [
            item
            for item in service.list_schedules()
            if str((item.raw_payload or {}).get("schedule_kind") or "") in {"event_keyword_search_v1", "congress_taiwan_legislation_search_v1"}
        ]
        if not schedules:
            st.caption("目前沒有已建立的排程。" if lang == "zh-TW" else "No schedule yet.")
            return

        rows = []
        for task in schedules:
            payload = task.raw_payload or {}
            one_shot = bool(payload.get("one_shot"))
            mode_text = "單次" if one_shot else "重複"
            rows.append(
                {
                    "ID": task.id,
                    "名稱": task.name,
                    "類型": "事件搜尋" if payload.get("schedule_kind") == "event_keyword_search_v1" else "Congress 法案",
                    "模式": mode_text,
                    "間隔(分)": int(task.interval_minutes or 0),
                    "下次執行": task.next_run_at,
                    "上次執行": task.last_run_at,
                    "狀態": task.last_status or "-",
                    "啟用": bool(task.enabled),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        for task in schedules:
            col1, col2, col3 = st.columns([6, 2, 2])
            col1.caption(f"#{task.id} | {task.name}")
            if col2.button("立即執行", key=f"schedule-page-run-{task.id}", use_container_width=True):
                result = service.run_schedule(task.id) or {}
                st.json(result)
                nested = result.get("result") if isinstance(result, dict) else None
                if isinstance(nested, dict):
                    if "events" in nested:
                        _render_search_result(nested.get("events"), "event")
                    if "legislation" in nested:
                        _render_search_result(nested.get("legislation"), "legislation")
            toggle_label = "停用" if task.enabled else "啟用"
            if col3.button(toggle_label, key=f"schedule-page-toggle-{task.id}", use_container_width=True):
                service.set_enabled(task.id, not task.enabled)
                st.success("已更新排程狀態")


def render(lang: str, labels: dict[str, str]) -> None:
    st.session_state.setdefault("schedule_event_now_last_result", {"found": 0, "created": 0, "updated": 0, "skipped_existing": 0, "items": []})
    st.session_state.setdefault("schedule_congress_now_last_result", {"records_found": 0, "records_created": 0, "records_updated": 0, "items": []})
    st.header(labels.get("schedule", "排程"))
    st.caption("支援：人物事件搜尋（立刻/預約）與 Congress.gov 涉台法案搜尋（立刻/預約）。")
    event_tab, congress_tab, saved_tab = st.tabs(["事件搜尋", "Congress 法案搜尋", "已建立排程"])

    with event_tab:
        st.subheader("立刻搜尋事件")
        with st.form("schedule-event-now-form"):
            scope = st.selectbox("搜尋範圍", options=PERSON_SCOPE_OPTIONS, format_func=lambda item: item[1], key="schedule-event-now-scope")
            st.caption("註：聯邦官員＝行政部門，不含國會議員（參議員/眾議員）。")
            col1, col2 = st.columns(2)
            start_date = col1.date_input("開始日期", value=datetime.utcnow().date(), key="schedule-event-now-start-date")
            end_date = col2.date_input("結束日期", value=datetime.utcnow().date(), key="schedule-event-now-end-date")
            col3, col4 = st.columns(2)
            start_time = col3.time_input("開始時間", value=time(0, 0), key="schedule-event-now-start-time")
            end_time = col4.time_input("結束時間", value=time(23, 59), key="schedule-event-now-end-time")
            keywords_text = st.text_area("台灣關鍵字（逗號或換行分隔）", value=", ".join(DEFAULT_TAIWAN_KEYWORDS), key="schedule-event-now-keywords")
            selected_domains = st.multiselect("預設網域", options=DEFAULT_EVENT_DOMAINS, default=DEFAULT_EVENT_DOMAINS, key="schedule-event-now-domain-default")
            custom_domains_text = st.text_input("新增網域（可多個，逗號分隔）", value="", key="schedule-event-now-domain-custom")
            max_people = st.number_input("人物數上限（0=不限）", min_value=0, max_value=10000, value=0, step=10, key="schedule-event-now-max-people")
            explicit_names_text = st.text_area(
                "指定人物（留空=依範圍搜）",
                value="",
                help="可輸入人名，每行或逗號分隔。填入後會忽略「搜尋範圍」設定，直接搜尋這些人。",
                key="schedule-event-now-explicit-names",
            )
            btn_col, result_col = st.columns([2, 5])
            with btn_col:
                submit_now_event = st.form_submit_button("立刻搜尋")
            with result_col:
                st.markdown(f"**搜尋結果：{_event_now_summary_text()}**")
        if submit_now_event:
            start_at = _combine_dt(start_date, start_time)
            end_at = _combine_dt(end_date, end_time)
            domains = list(dict.fromkeys(selected_domains + _parse_csv_lines(custom_domains_text)))
            with session_scope() as session:
                service = ScheduledCollectionService(session)
                result = service.run_event_keyword_search_now(
                    person_scope=scope[0],
                    start_at=start_at,
                    end_at=end_at,
                    taiwan_keywords=_parse_csv_lines(keywords_text),
                    domains=domains,
                    max_people=(None if int(max_people) <= 0 else int(max_people)),
                    explicit_person_names=_parse_csv_lines(explicit_names_text),
                )
                st.session_state["schedule_event_now_last_result"] = result
            st.rerun()
        if "schedule_event_now_last_result" in st.session_state:
            _render_search_result(st.session_state["schedule_event_now_last_result"], "event")

        st.subheader("預約搜尋事件")
        with st.form("schedule-event-reserve-form"):
            reserve_name = st.text_input("排程名稱", value=f"事件搜尋-{datetime.utcnow().strftime('%Y%m%d-%H%M')}", key="schedule-event-reserve-name")
            reserve_scope = st.selectbox("搜尋範圍", options=PERSON_SCOPE_OPTIONS, format_func=lambda item: item[1], key="schedule-event-reserve-scope")
            st.caption("註：聯邦官員＝行政部門，不含國會議員（參議員/眾議員）。")
            col1, col2 = st.columns(2)
            reserve_start_date = col1.date_input("開始日期", value=datetime.utcnow().date(), key="schedule-event-reserve-start-date")
            reserve_end_date = col2.date_input("結束日期", value=datetime.utcnow().date(), key="schedule-event-reserve-end-date")
            col3, col4 = st.columns(2)
            reserve_start_time = col3.time_input("開始時間", value=time(0, 0), key="schedule-event-reserve-start-time")
            reserve_end_time = col4.time_input("結束時間", value=time(23, 59), key="schedule-event-reserve-end-time")
            reserve_keywords_text = st.text_area("台灣關鍵字（逗號或換行分隔）", value=", ".join(DEFAULT_TAIWAN_KEYWORDS), key="schedule-event-reserve-keywords")
            reserve_domains = st.multiselect("預設網域", options=DEFAULT_EVENT_DOMAINS, default=DEFAULT_EVENT_DOMAINS, key="schedule-event-reserve-domain-default")
            reserve_custom_domains = st.text_input("新增網域（可多個，逗號分隔）", value="", key="schedule-event-reserve-domain-custom")
            reserve_max_people = st.number_input("人物數上限（0=不限）", min_value=0, max_value=10000, value=0, step=10, key="schedule-event-reserve-max-people")
            reserve_mode = st.selectbox("執行模式", options=[("single", "單次"), ("repeat", "每隔一段時間重複")], format_func=lambda item: item[1], key="schedule-event-reserve-mode")
            reserve_interval = st.number_input("重複間隔（分鐘）", min_value=5, max_value=10080, value=1440, step=5, key="schedule-event-reserve-interval")
            col5, col6 = st.columns(2)
            run_date = col5.date_input("預約執行日期", value=datetime.utcnow().date(), key="schedule-event-reserve-run-date")
            run_time = col6.time_input("預約執行時間", value=time(datetime.utcnow().hour, datetime.utcnow().minute), key="schedule-event-reserve-run-time")
            reserve_submit = st.form_submit_button("建立預約排程")
        if reserve_submit:
            start_at = _combine_dt(reserve_start_date, reserve_start_time)
            end_at = _combine_dt(reserve_end_date, reserve_end_time)
            run_at = _combine_dt(run_date, run_time)
            domains = list(dict.fromkeys(reserve_domains + _parse_csv_lines(reserve_custom_domains)))
            with session_scope() as session:
                service = ScheduledCollectionService(session)
                task = service.create_event_keyword_schedule(
                    name=reserve_name,
                    person_scope=reserve_scope[0],
                    start_at=start_at,
                    end_at=end_at,
                    taiwan_keywords=_parse_csv_lines(reserve_keywords_text),
                    domains=domains,
                    run_at=run_at,
                    max_people=(None if int(reserve_max_people) <= 0 else int(reserve_max_people)),
                    one_shot=(reserve_mode[0] == "single"),
                    interval_minutes=int(reserve_interval),
                )
                st.success(f"已建立排程 #{task.id}")

    with congress_tab:
        st.subheader("立刻搜尋 Congress 涉台法案")
        with st.form("schedule-congress-now-form"):
            col1, col2 = st.columns(2)
            start_date = col1.date_input("開始日期", value=datetime.utcnow().date(), key="schedule-congress-now-start-date")
            end_date = col2.date_input("結束日期", value=datetime.utcnow().date(), key="schedule-congress-now-end-date")
            col3, col4 = st.columns(2)
            start_time = col3.time_input("開始時間", value=time(0, 0), key="schedule-congress-now-start-time")
            end_time = col4.time_input("結束時間", value=time(23, 59), key="schedule-congress-now-end-time")
            keywords = st.text_area("台灣關鍵字（逗號或換行分隔）", value=", ".join(DEFAULT_TAIWAN_KEYWORDS), key="schedule-congress-now-keywords")
            btn_col, result_col = st.columns([2, 5])
            with btn_col:
                submit_now = st.form_submit_button("立刻搜尋法案")
            with result_col:
                st.markdown(f"**搜尋結果：{_congress_now_summary_text()}**")
        if submit_now:
            start_at = _combine_dt(start_date, start_time)
            end_at = _combine_dt(end_date, end_time)
            with session_scope() as session:
                service = ScheduledCollectionService(session)
                result = service.run_congress_legislation_search_now(
                    start_at=start_at,
                    end_at=end_at,
                    taiwan_keywords=_parse_csv_lines(keywords),
                )
                st.session_state["schedule_congress_now_last_result"] = result
            st.rerun()
        if "schedule_congress_now_last_result" in st.session_state:
            _render_search_result(st.session_state["schedule_congress_now_last_result"], "legislation")

        st.subheader("預約搜尋 Congress 涉台法案")
        with st.form("schedule-congress-reserve-form"):
            reserve_name = st.text_input("排程名稱", value=f"Congress法案-{datetime.utcnow().strftime('%Y%m%d-%H%M')}", key="schedule-congress-reserve-name")
            col1, col2 = st.columns(2)
            reserve_start_date = col1.date_input("開始日期", value=datetime.utcnow().date(), key="schedule-congress-reserve-start-date")
            reserve_end_date = col2.date_input("結束日期", value=datetime.utcnow().date(), key="schedule-congress-reserve-end-date")
            col3, col4 = st.columns(2)
            reserve_start_time = col3.time_input("開始時間", value=time(0, 0), key="schedule-congress-reserve-start-time")
            reserve_end_time = col4.time_input("結束時間", value=time(23, 59), key="schedule-congress-reserve-end-time")
            reserve_keywords = st.text_area("台灣關鍵字（逗號或換行分隔）", value=", ".join(DEFAULT_TAIWAN_KEYWORDS), key="schedule-congress-reserve-keywords")
            reserve_mode = st.selectbox("執行模式", options=[("single", "單次"), ("repeat", "每隔一段時間重複")], format_func=lambda item: item[1], key="schedule-congress-reserve-mode")
            reserve_interval = st.number_input("重複間隔（分鐘）", min_value=5, max_value=10080, value=1440, step=5, key="schedule-congress-reserve-interval")
            col5, col6 = st.columns(2)
            run_date = col5.date_input("預約執行日期", value=datetime.utcnow().date(), key="schedule-congress-reserve-run-date")
            run_time = col6.time_input("預約執行時間", value=time(datetime.utcnow().hour, datetime.utcnow().minute), key="schedule-congress-reserve-run-time")
            submit_reserve = st.form_submit_button("建立法案預約排程")
        if submit_reserve:
            with session_scope() as session:
                service = ScheduledCollectionService(session)
                task = service.create_congress_legislation_schedule(
                    name=reserve_name,
                    start_at=_combine_dt(reserve_start_date, reserve_start_time),
                    end_at=_combine_dt(reserve_end_date, reserve_end_time),
                    taiwan_keywords=_parse_csv_lines(reserve_keywords),
                    run_at=_combine_dt(run_date, run_time),
                    one_shot=(reserve_mode[0] == "single"),
                    interval_minutes=int(reserve_interval),
                )
                st.success(f"已建立排程 #{task.id}")

    with saved_tab:
        st.subheader("已建立排程")
        _render_schedule_table(lang)
