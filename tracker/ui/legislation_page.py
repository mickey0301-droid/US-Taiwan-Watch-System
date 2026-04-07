from __future__ import annotations

from typing import Iterable

import streamlit as st

from tracker.config import use_google_sheet_primary_mode
from tracker.db import session_scope
from tracker.models import Legislation, Person
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.legislation_service import LegislationService
from tracker.ui.navigation import render_person_links
from tracker.utils.congress_bills import congress_bill_url
from tracker.utils.source_types import source_bucket_label, source_priority_key


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["legislation"])
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

        type_label = "法案類型" if lang == "zh-TW" else "Legislation type"
        year_label = "年份" if lang == "zh-TW" else "Year"
        month_label = "月份" if lang == "zh-TW" else "Month"
        type_options = _type_options(lang)
        selected_type = st.selectbox(type_label, list(type_options.keys()), format_func=lambda key: type_options[key])

        typed_rows = _filter_db_rows_by_type(all_rows, selected_type)
        years = _list_years(typed_rows)
        if not years:
            st.info("目前沒有符合條件的法案。" if lang == "zh-TW" else "No legislation matches this filter.")
            return

        selected_year = st.selectbox(year_label, years)
        months = [0, *_list_months(typed_rows, selected_year)]
        selected_month = st.selectbox(
            month_label,
            months,
            format_func=lambda value: ("全部" if lang == "zh-TW" else "All") if value == 0 else f"{value:02d}",
        )

        legislation_rows = _rows_for_year_month(typed_rows, selected_year, selected_month)
        if not legislation_rows:
            st.info("這個月份目前沒有立法資料。" if lang == "zh-TW" else "No legislation is available for this month.")
            return

        for item in legislation_rows:
            _render_db_legislation_card(item, service, people_by_id, lang)


def _render_db_legislation_card(selected: Legislation, service: LegislationService, people_by_id: dict[int, Person], lang: str) -> None:
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

    with st.container(border=True):
        heading = selected.title
        if selected.bill_number:
            heading = f"{selected.bill_number} | {heading}"
        st.markdown(f"**{heading}**")
        st.markdown(f"`{time_label}`：{_format_date(selected.introduced_date or selected.last_action_date)}")
        st.markdown(f"`{description_label}`：{selected.summary or selected.title}")
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
    if not rows:
        return False

    st.info(
        "Google Sheet fallback mode is active. The cloud app is showing exported legislation data."
        if lang != "zh-TW"
        else "目前使用 Google Sheet fallback 模式，雲端版先顯示已匯出的立法資料。"
    )

    type_label = "法案類型" if lang == "zh-TW" else "Legislation type"
    year_label = "年份" if lang == "zh-TW" else "Year"
    month_label = "月份" if lang == "zh-TW" else "Month"

    type_options = _type_options(lang)
    selected_type = st.selectbox(type_label, list(type_options.keys()), format_func=lambda key: type_options[key], key="sheet-legislation-type")
    typed_rows = _filter_sheet_rows_by_type(rows, selected_type)

    years = sorted({row.get("date_date").year for row in typed_rows if row.get("date_date")}, reverse=True)
    if not years:
        return True

    selected_year = st.selectbox(year_label, years, key="sheet-legislation-year")
    months = sorted({row.get("date_date").month for row in typed_rows if row.get("date_date") and row["date_date"].year == selected_year}, reverse=True)
    months = [0, *months]
    selected_month = st.selectbox(
        month_label,
        months,
        format_func=lambda value: ("全部" if lang == "zh-TW" else "All") if value == 0 else f"{value:02d}",
        key="sheet-legislation-month",
    )

    if selected_month == 0:
        filtered_rows = [row for row in typed_rows if row.get("date_date") and row["date_date"].year == selected_year]
    else:
        filtered_rows = [
            row
            for row in typed_rows
            if row.get("date_date") and row["date_date"].year == selected_year and row["date_date"].month == selected_month
        ]
    if not filtered_rows:
        return True

    for selected in filtered_rows:
        sponsors = _sheet_sponsors(selected)
        _render_sheet_legislation_card(selected, sponsors, lang)
    return True


def _render_sheet_legislation_card(selected: dict[str, object], sponsors: list[dict[str, object]], lang: str) -> None:
    time_label = "時間" if lang == "zh-TW" else "Time"
    description_label = "法案摘要" if lang == "zh-TW" else "Summary"
    sponsors_label = "提案人" if lang == "zh-TW" else "Sponsors"
    status_label = "進度" if lang == "zh-TW" else "Status"
    latest_action_label = "最新動作" if lang == "zh-TW" else "Latest action"
    committees_label = "委員會" if lang == "zh-TW" else "Committees"
    cosponsors_label = "聯署人數" if lang == "zh-TW" else "Cosponsors"

    with st.container(border=True):
        heading = selected.get("title") or ""
        if selected.get("bill_number"):
            heading = f"{selected['bill_number']} | {heading}"
        st.markdown(f"**{heading}**")
        st.markdown(f"`{time_label}`：{selected['date_date'].strftime('%Y-%m-%d') if selected.get('date_date') else 'N/A'}")
        st.markdown(f"`{description_label}`：{selected.get('summary') or selected.get('title') or ''}")
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


def _type_options(lang: str) -> dict[str, str]:
    if lang == "zh-TW":
        return {
            "all": "全部",
            "federal": "國會法案",
            "state": "州議會法案",
        }
    return {
        "all": "All",
        "federal": "Congressional",
        "state": "State Legislature",
    }


def _filter_db_rows_by_type(rows: list[Legislation], selected_type: str) -> list[Legislation]:
    if selected_type == "federal":
        return [row for row in rows if str(row.level or "").lower() == "federal"]
    if selected_type == "state":
        return [row for row in rows if str(row.level or "").lower() == "state"]
    return rows


def _filter_sheet_rows_by_type(rows: list[dict[str, object]], selected_type: str) -> list[dict[str, object]]:
    def is_federal(item: dict[str, object]) -> bool:
        level = str(item.get("level") or "").lower()
        if level:
            return level == "federal"
        jurisdiction = str(item.get("jurisdiction_name") or "").strip().lower()
        return jurisdiction in {"united states", "us", "u.s."}

    if selected_type == "federal":
        return [item for item in rows if is_federal(item)]
    if selected_type == "state":
        return [item for item in rows if not is_federal(item)]
    return rows


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
    if month == 0:
        return [
            row
            for row in rows
            if (row.introduced_date or row.last_action_date)
            and (row.introduced_date or row.last_action_date).year == year
        ]
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
