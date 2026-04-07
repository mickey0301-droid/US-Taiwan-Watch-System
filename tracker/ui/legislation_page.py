from __future__ import annotations

from typing import Iterable

import streamlit as st

from tracker.config import use_google_sheet_primary_mode
from tracker.db import session_scope
from tracker.models import Legislation, Person
from tracker.services.ai_assist_service import AIAssistService
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.legislation_service import LegislationService
from tracker.ui.navigation import render_person_links
from tracker.utils.congress_bills import congress_bill_url
from tracker.utils.source_types import source_bucket_label, source_priority_key


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["legislation"])
    ai_service = AIAssistService()
    if use_google_sheet_primary_mode():
        if _render_google_sheet_fallback(lang):
            return
        st.info("No legislation is available yet." if lang != "zh-TW" else "目前沒有可顯示的法案資料。")
        return
    with session_scope() as session:
        service = LegislationService(session)
        people_by_id = {person.id: person for person in session.query(Person).all()}
        all_rows = session.query(Legislation).order_by(
            Legislation.introduced_date.desc().nullslast(),
            Legislation.last_action_date.desc().nullslast(),
            Legislation.id.desc(),
        ).all()
        if not all_rows:
            if _render_google_sheet_fallback(lang):
                return
            st.info("目前還沒有立法資料。" if lang == "zh-TW" else "No legislation is available yet.")
            return

        scope_label = "法案範圍" if lang == "zh-TW" else "Scope"
        year_label = "年份" if lang == "zh-TW" else "Year"
        month_label = "月份" if lang == "zh-TW" else "Month"
        bill_label = "法案" if lang == "zh-TW" else "Legislation"
        time_label = "時間" if lang == "zh-TW" else "Time"
        description_label = "法案摘要" if lang == "zh-TW" else "Summary"
        sponsors_label = "提案人" if lang == "zh-TW" else "Sponsors"
        sources_label = "來源" if lang == "zh-TW" else "Sources"
        status_label = "進度" if lang == "zh-TW" else "Status"
        official_link_label = "Congress.gov"
        topic_label = "其他相關主題" if lang == "zh-TW" else "Additional topics"
        latest_action_label = "最新動作" if lang == "zh-TW" else "Latest action"
        committees_label = "委員會" if lang == "zh-TW" else "Committees"
        cosponsors_label = "聯署人數" if lang == "zh-TW" else "Cosponsors"
        text_link_label = "法案全文" if lang == "zh-TW" else "Bill text"

        scopes = _scope_options(lang)
        selected_scope = st.selectbox(scope_label, list(scopes.keys()), format_func=lambda key: scopes[key])

        scoped_rows = [row for row in all_rows if _match_scope(row, selected_scope)]
        years = _list_years(scoped_rows)
        if not years:
            st.info("目前沒有符合條件的法案。" if lang == "zh-TW" else "No legislation matches this filter.")
            return

        selected_year = st.selectbox(year_label, years)
        months = _list_months(scoped_rows, selected_year)
        selected_month = st.selectbox(month_label, months, format_func=lambda value: f"{value:02d}")

        legislation_rows = _rows_for_year_month(scoped_rows, selected_year, selected_month)
        if not legislation_rows:
            st.info("這個月份目前沒有立法資料。" if lang == "zh-TW" else "No legislation is available for this month.")
            return

        options = {
            f"{_format_date(item.introduced_date or item.last_action_date)} | {item.bill_number or item.title[:80]}": item.id
            for item in legislation_rows
        }
        selected_label = st.selectbox(bill_label, list(options.keys()))
        selected_id = options[selected_label]
        selected = next(item for item in legislation_rows if item.id == selected_id)

        sponsors = []
        for sponsor in service.list_sponsors(selected.id):
            person = people_by_id.get(sponsor.person_id)
            if person:
                sponsors.append({"person_id": person.id, "display_name": person.full_name})

        sources = sorted(
            service.list_sources(selected.id),
            key=lambda source: (
                source_priority_key(source.source_type, source.source_url),
                -(source.collected_at.timestamp() if source.collected_at else 0),
                source.id,
            ),
        )
        raw_payload = selected.raw_payload or {}
        official_link = raw_payload.get("congress_gov_url") or congress_bill_url(
            raw_payload.get("congress"),
            selected.bill_number,
        )
        additional_topics = sorted((raw_payload.get("additional_topics") or {}).keys())
        latest_action = raw_payload.get("latest_action_text")
        committees = raw_payload.get("committee_assignments") or []
        if not isinstance(committees, list):
            committees = [str(committees)]
        cosponsor_count = raw_payload.get("cosponsor_count")
        text_page_url = raw_payload.get("text_page_url")
        localized_summary = (
            ai_service.summarize_legislation(
                str(selected.bill_number or ""),
                str(selected.title or ""),
                str(selected.summary or ""),
                str(latest_action or ""),
            )
            if lang == "zh-TW"
            else None
        )

        with st.container(border=True):
            heading = selected.title
            if selected.bill_number:
                heading = f"{selected.bill_number} | {heading}"
            st.markdown(f"**{heading}**")
            st.markdown(f"`{time_label}`：{_format_date(selected.introduced_date or selected.last_action_date)}")
            st.markdown(f"`{description_label}`：{localized_summary or selected.summary or selected.title}")
            if localized_summary:
                st.caption("AI 中文摘要" if lang == "zh-TW" else "AI summary")
            st.markdown(f"`{status_label}`：{selected.status_text or ('未知' if lang == 'zh-TW' else 'Unknown')}")
            if official_link:
                st.markdown(f"`{official_link_label}`：[Congress.gov]({official_link})")
            if text_page_url:
                st.markdown(f"`{text_link_label}`：[{text_link_label}]({text_page_url})")
            if latest_action:
                st.markdown(f"`{latest_action_label}`：{latest_action}")
            if committees:
                st.markdown(f"`{committees_label}`：{' | '.join(item for item in committees if item)}")
            if cosponsor_count not in (None, ""):
                st.markdown(f"`{cosponsors_label}`：{cosponsor_count}")
            if additional_topics:
                st.markdown(f"`{topic_label}`：{', '.join(additional_topics)}")

            st.markdown(f"`{sponsors_label}`：")
            if sponsors:
                render_person_links(sponsors, lang, key_prefix=f"legislation-{selected.id}")
            else:
                st.write("目前未附提案人。" if lang == "zh-TW" else "No sponsors attached yet.")

            if sources:
                formatted = " | ".join(
                    f"[{source_bucket_label(source.source_type, source.source_url, lang)}]({source.source_url})"
                    for source in sources[:5]
                )
                st.markdown(f"`{sources_label}`：{formatted}")


def _render_google_sheet_fallback(lang: str) -> bool:
    rows = GoogleSheetReadService().list_legislation()
    ai_service = AIAssistService()
    if not rows:
        return False

    st.info(
        "Google Sheet fallback mode is active. The cloud app is showing exported legislation data."
        if lang != "zh-TW"
        else "目前使用 Google Sheet fallback 模式，雲端版先顯示已匯出的立法資料。"
    )
    scope_label = "法案範圍" if lang == "zh-TW" else "Scope"
    year_label = "年份" if lang == "zh-TW" else "Year"
    month_label = "月份" if lang == "zh-TW" else "Month"
    bill_label = "法案" if lang == "zh-TW" else "Legislation"
    time_label = "時間" if lang == "zh-TW" else "Time"
    description_label = "法案摘要" if lang == "zh-TW" else "Summary"
    sponsors_label = "提案人" if lang == "zh-TW" else "Sponsors"
    status_label = "進度" if lang == "zh-TW" else "Status"
    latest_action_label = "最新動作" if lang == "zh-TW" else "Latest action"
    committees_label = "委員會" if lang == "zh-TW" else "Committees"
    cosponsors_label = "聯署人數" if lang == "zh-TW" else "Cosponsors"

    scopes = {"all": "全部法案" if lang == "zh-TW" else "All legislation"}
    for row in rows:
        scope = str(row.get("scope") or "").strip()
        if scope and scope not in scopes:
            scopes[scope] = scope
    selected_scope = st.selectbox(scope_label, list(scopes.keys()), format_func=lambda key: scopes[key], key="sheet-legislation-scope")
    scoped_rows = [row for row in rows if selected_scope == "all" or str(row.get("scope") or "").strip() == selected_scope]
    years = sorted({row.get("date_date").year for row in scoped_rows if row.get("date_date")}, reverse=True)
    if not years:
        return True
    selected_year = st.selectbox(year_label, years, key="sheet-legislation-year")
    months = sorted({row.get("date_date").month for row in scoped_rows if row.get("date_date") and row["date_date"].year == selected_year}, reverse=True)
    selected_month = st.selectbox(month_label, months, format_func=lambda value: f"{value:02d}", key="sheet-legislation-month")
    filtered_rows = [row for row in scoped_rows if row.get("date_date") and row["date_date"].year == selected_year and row["date_date"].month == selected_month]
    if not filtered_rows:
        return True
    options = {
        f"{row['date_date'].strftime('%Y-%m-%d')} | {row.get('bill_number') or str(row.get('title') or '')[:80]}": row
        for row in filtered_rows
    }
    selected_row = st.selectbox(bill_label, list(options.keys()), key="sheet-legislation-select")
    selected = options[selected_row]
    sponsors = _sheet_sponsors(selected)

    with st.container(border=True):
        heading = selected.get("title") or ""
        if selected.get("bill_number"):
            heading = f"{selected['bill_number']} | {heading}"
        localized_summary = (
            ai_service.summarize_legislation(
                str(selected.get("bill_number") or ""),
                str(selected.get("title") or ""),
                str(selected.get("summary") or ""),
                str(selected.get("latest_action") or ""),
            )
            if lang == "zh-TW"
            else None
        )
        st.markdown(f"**{heading}**")
        st.markdown(f"`{time_label}`：{selected['date_date'].strftime('%Y-%m-%d') if selected.get('date_date') else 'N/A'}")
        st.markdown(f"`{description_label}`：{localized_summary or selected.get('summary') or selected.get('title') or ''}")
        if localized_summary:
            st.caption("AI 中文摘要" if lang == "zh-TW" else "AI summary")
        st.markdown(f"`{status_label}`：{selected.get('status') or ('未知' if lang == 'zh-TW' else 'Unknown')}")
        if selected.get("official_page"):
            st.markdown(f"`Congress.gov`：[Congress.gov]({selected['official_page']})")
        if selected.get("official_text_page"):
            st.markdown(f"`Bill text`：[Bill text]({selected['official_text_page']})")
        if selected.get("latest_action"):
            st.markdown(f"`{latest_action_label}`：{selected['latest_action']}")
        if selected.get("committees_list"):
            st.markdown(f"`{committees_label}`：{' | '.join(selected['committees_list'])}")
        if selected.get("cosponsor_count_int") is not None:
            st.markdown(f"`{cosponsors_label}`：{selected['cosponsor_count_int']}")
        st.markdown(f"`{sponsors_label}`：")
        render_person_links(sponsors, lang, key_prefix=f"sheet-legislation-{selected.get('legislation_id')}")
    return True


def _sheet_sponsors(selected: dict[str, object]) -> list[dict[str, object]]:
    sponsor_ids = list(selected.get("sponsor_ids_list") or [])
    sponsor_names = list(selected.get("sponsors_en_list") or [])
    sponsor_names_zh = list(selected.get("sponsors_zh_list") or [])
    sponsors: list[dict[str, object]] = []
    for index, name in enumerate(sponsor_names):
        person_id = sponsor_ids[index] if index < len(sponsor_ids) else None
        zh_name = sponsor_names_zh[index] if index < len(sponsor_names_zh) else ""
        display_name = f"{zh_name} {name}".strip() if zh_name else str(name)
        sponsors.append({"person_id": person_id, "display_name": display_name})
    return sponsors


def _scope_options(lang: str) -> dict[str, str]:
    if lang == "zh-TW":
        return {
            "all": "全部法案",
            "excel_history": "Excel 歷史法案",
            "non_excel": "其他法案",
        }
    return {
        "all": "All legislation",
        "excel_history": "Excel history legislation",
        "non_excel": "Other legislation",
    }


def _match_scope(row: Legislation, scope: str) -> bool:
    is_excel = row.parser_identity == "congress_bills_excel_v1"
    if scope == "excel_history":
        return is_excel
    if scope == "non_excel":
        return not is_excel
    return True


def _list_years(rows: Iterable[Legislation]) -> list[int]:
    years = {
        (row.introduced_date or row.last_action_date).year
        for row in rows
        if (row.introduced_date or row.last_action_date)
    }
    return sorted(years, reverse=True)


def _list_months(rows: Iterable[Legislation], year: int) -> list[int]:
    months = {
        (row.introduced_date or row.last_action_date).month
        for row in rows
        if (row.introduced_date or row.last_action_date)
        and (row.introduced_date or row.last_action_date).year == year
    }
    return sorted(months, reverse=True)


def _rows_for_year_month(rows: Iterable[Legislation], year: int, month: int) -> list[Legislation]:
    return [
        row
        for row in rows
        if (row.introduced_date or row.last_action_date)
        and (row.introduced_date or row.last_action_date).year == year
        and (row.introduced_date or row.last_action_date).month == month
    ]


def _format_date(value) -> str:
    if value is None:
        return "N/A"
    return value.strftime("%Y-%m-%d")
