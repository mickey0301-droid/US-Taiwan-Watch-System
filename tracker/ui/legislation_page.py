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

        for idx, item in enumerate(legislation_rows, start=1):
            _render_db_legislation_card(item, service, people_by_id, lang, idx)


def _render_db_legislation_card(selected: Legislation, service: LegislationService, people_by_id: dict[int, Person], lang: str, index: int) -> None:
    chamber_label = "所屬議院" if lang == "zh-TW" else "Chamber"
    sponsor_label = "提案議員" if lang == "zh-TW" else "Sponsor"
    introduced_label = "提案時間" if lang == "zh-TW" else "Introduced"
    date_label = "日期" if lang == "zh-TW" else "Date"

    sponsors = []
    for sponsor in service.list_sponsors(selected.id):
        person = people_by_id.get(sponsor.person_id)
        if person:
            sponsors.append(person.full_name)

    raw_payload = selected.raw_payload or {}
    official_link = raw_payload.get("congress_gov_url") or congress_bill_url(
        raw_payload.get("congress"),
        selected.bill_number,
    ) or selected.source_url

    chamber = str(selected.chamber or "").strip().lower()
    level = str(selected.level or "").strip().lower()
    jurisdiction = str(selected.jurisdiction_name or "").strip()
    chamber_name_zh = "參議院" if chamber == "senate" else "眾議院" if chamber == "house" else "議會"
    chamber_name_en = "Senate" if chamber == "senate" else "House" if chamber == "house" else "Legislature"
    if level == "federal":
        chamber_text = f"聯邦{chamber_name_zh}" if lang == "zh-TW" else f"U.S. {chamber_name_en}"
    elif level == "state":
        chamber_text = f"{jurisdiction}{chamber_name_zh}" if lang == "zh-TW" else f"{jurisdiction} {chamber_name_en}"
    else:
        chamber_text = chamber_name_zh if lang == "zh-TW" else chamber_name_en

    sponsor_text = "、".join(sponsors[:3]) if (lang == "zh-TW" and sponsors) else (", ".join(sponsors[:3]) if sponsors else ("未提供" if lang == "zh-TW" else "Not available"))

    with st.container(border=True):
        title = str(selected.title or "").strip()
        if selected.bill_number:
            title = f"{selected.bill_number} {title}".strip()
        st.markdown(f"**{index}. {title}**")
        st.markdown(f"`{chamber_label}`：{chamber_text}")
        st.markdown(f"`{sponsor_label}`：{sponsor_text}")
        st.markdown(f"`{introduced_label}`：{_format_date(selected.introduced_date)}")
        st.caption(f"{date_label}: {_format_date(selected.introduced_date or selected.last_action_date)}")
        if official_link:
            st.markdown(f"[link]({official_link})")

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

    for idx, selected in enumerate(filtered_rows, start=1):
        sponsors = _sheet_sponsors(selected)
        _render_sheet_legislation_card(selected, sponsors, lang, idx)
    return True


def _render_sheet_legislation_card(selected: dict[str, object], sponsors: list[dict[str, object]], lang: str, index: int) -> None:
    chamber_label = "所屬議院" if lang == "zh-TW" else "Chamber"
    sponsor_label = "提案議員" if lang == "zh-TW" else "Sponsor"
    introduced_label = "提案時間" if lang == "zh-TW" else "Introduced"
    date_label = "日期" if lang == "zh-TW" else "Date"

    level = str(selected.get("level") or "").strip().lower()
    chamber = str(selected.get("chamber") or "").strip().lower()
    jurisdiction = str(selected.get("jurisdiction_name") or selected.get("jurisdiction") or "").strip()
    if not level:
        level = "federal" if jurisdiction.lower() in {"united states", "us", "u.s."} else "state"

    chamber_name_zh = "參議院" if chamber == "senate" else "眾議院" if chamber == "house" else "議會"
    chamber_name_en = "Senate" if chamber == "senate" else "House" if chamber == "house" else "Legislature"
    if level == "federal":
        chamber_text = f"聯邦{chamber_name_zh}" if lang == "zh-TW" else f"U.S. {chamber_name_en}"
    elif level == "state":
        chamber_text = f"{jurisdiction}{chamber_name_zh}" if lang == "zh-TW" else f"{jurisdiction} {chamber_name_en}"
    else:
        chamber_text = chamber_name_zh if lang == "zh-TW" else chamber_name_en

    names=[str(item.get("display_name") or "").strip() for item in sponsors if str(item.get("display_name") or "").strip()]
    sponsor_text = "、".join(names[:3]) if (lang == "zh-TW" and names) else (", ".join(names[:3]) if names else ("未提供" if lang == "zh-TW" else "Not available"))

    with st.container(border=True):
        title = str(selected.get("title") or "").strip()
        bill_number = str(selected.get("bill_number") or "").strip()
        if bill_number:
            title = f"{bill_number} {title}".strip()
        st.markdown(f"**{index}. {title}**")
        st.markdown(f"`{chamber_label}`：{chamber_text}")
        st.markdown(f"`{sponsor_label}`：{sponsor_text}")
        st.markdown(f"`{introduced_label}`：{_format_date(selected.get('date_date'))}")
        st.caption(f"{date_label}: {_format_date(selected.get('date_date'))}")
        source_url = str(selected.get("source_url") or selected.get("official_page") or "").strip()
        if source_url:
            st.markdown(f"[link]({source_url})")

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
