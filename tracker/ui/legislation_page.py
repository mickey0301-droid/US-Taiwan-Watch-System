from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable

import streamlit as st

from tracker.config import use_google_sheet_primary_mode
from tracker.db import session_scope
from tracker.models import Legislation, Person
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.legislation_service import LegislationService
from tracker.ui.navigation import render_person_links
from tracker.ui import dashboard
from tracker.utils.congress_bills import congress_bill_url
from tracker.utils.source_types import source_bucket_label, source_priority_key


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["legislation"])
    sheet_service = GoogleSheetReadService()
    sheet_rows = sheet_service.list_legislation()
    sheet_people = sheet_service.list_people()

    with session_scope() as session:
        service = LegislationService(session)
        people_by_id = {person.id: person for person in session.query(Person).all()}
        all_rows = session.query(Legislation).order_by(
            Legislation.introduced_date.desc().nullslast(),
            Legislation.last_action_date.desc().nullslast(),
            Legislation.id.desc(),
        ).all()

        source = _choose_legislation_source(sheet_rows, all_rows)
        if source == "sheet":
            if _render_sheet_legislation_rows(sheet_rows, sheet_people, lang):
                return
            if use_google_sheet_primary_mode():
                st.info("No legislation is available yet." if lang != "zh-TW" else "目前沒有可顯示的法案資料。")
                return
        if not all_rows:
            if _render_sheet_legislation_rows(sheet_rows, sheet_people, lang):
                return
            st.info("目前還沒有立法資料。" if lang == "zh-TW" else "No legislation is available yet.")
            return

        type_label = "法案類型" if lang == "zh-TW" else "Legislation type"
        year_label = "年份" if lang == "zh-TW" else "Year"
        month_label = "月份" if lang == "zh-TW" else "Month"
        type_options = _type_options(lang)
        selected_type = st.selectbox(type_label, list(type_options.keys()), format_func=lambda key: type_options[key])

        typed_rows = _dedupe_db_legislation_rows(_filter_db_rows_by_type(all_rows, selected_type))
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


def _choose_legislation_source(sheet_rows: list[dict[str, object]], db_rows: list[Legislation]) -> str:
    if sheet_rows and not db_rows:
        return "sheet"
    if db_rows and not sheet_rows:
        return "db"
    if not sheet_rows and not db_rows:
        return "none"

    sheet_years = {row.get("date_date").year for row in sheet_rows if row.get("date_date")}
    db_years = {_effective_legislation_date(row).year for row in db_rows if _effective_legislation_date(row)}
    sheet_score = (len(sheet_years), max(sheet_years) if sheet_years else 0, len(sheet_rows))
    db_score = (len(db_years), max(db_years) if db_years else 0, len(db_rows))
    return "sheet" if sheet_score >= db_score else "db"


def _render_db_legislation_card(selected: Legislation, service: LegislationService, people_by_id: dict[int, Person], lang: str, index: int) -> None:
    chamber_label = "所屬議院" if lang == "zh-TW" else "Chamber"
    sponsor_label = "提案議員" if lang == "zh-TW" else "Sponsor"
    cosponsor_label = "共同提案議員" if lang == "zh-TW" else "Cosponsor"
    introduced_label = "提案時間" if lang == "zh-TW" else "Introduced"

    sponsor_records = service.list_sponsors(selected.id)
    sponsor_person: dict[str, object] | None = None
    cosponsor_people: list[dict[str, object]] = []
    for sponsor in sponsor_records:
        person = people_by_id.get(sponsor.person_id)
        if not person:
            continue
        role = str(getattr(sponsor, "role", "") or "").lower()
        chinese_name = _current_chinese_alias(person)
        person_payload = {
            "person_id": person.id,
            "display_name": chinese_name or person.full_name,
            "english_name": person.full_name,
            "chinese_name": chinese_name,
        }
        if role == "sponsor" and not sponsor_person:
            sponsor_person = person_payload
        elif role == "cosponsor":
            cosponsor_people.append(person_payload)
        elif not sponsor_person:
            sponsor_person = person_payload
        else:
            cosponsor_people.append(person_payload)

    sponsor_text = dashboard._format_people_inline([sponsor_person], lang) if sponsor_person else ("未提供" if lang == "zh-TW" else "Not available")
    cosponsor_text = _format_cosponsor_people(cosponsor_people, lang)

    raw_payload = selected.raw_payload or {}
    official_link = raw_payload.get("congress_gov_url") or congress_bill_url(
        raw_payload.get("congress"),
        selected.bill_number,
    ) or selected.source_url

    chamber_text = dashboard._format_legislation_chamber(
        level=str(selected.level or ""),
        chamber=str(selected.chamber or ""),
        jurisdiction_name=str(selected.jurisdiction_name or ""),
        lang=lang,
    )

    with st.container(border=True):
        preferred_title = dashboard._select_preferred_legislation_title(
            title=str(selected.title or ""),
            source_url=str(official_link or selected.source_url or ""),
            raw_payload=raw_payload if isinstance(raw_payload, dict) else {},
        )
        title = dashboard._format_legislation_title_with_description(
            title=preferred_title,
            summary=str(selected.summary or ""),
            lang=lang,
        )
        if selected.bill_number:
            title = f"{selected.bill_number} {title}".strip()
        st.markdown(f"**{index}. {title}**")
        st.markdown(f"`{chamber_label}`：{chamber_text}")
        st.markdown(f"`{sponsor_label}`：{sponsor_text}")
        st.markdown(f"`{cosponsor_label}`：{cosponsor_text}")
        st.markdown(f"`{introduced_label}`：{_format_date(_effective_legislation_date(selected))}")
        if official_link:
            st.markdown(f"[link]({official_link})")

def _render_google_sheet_fallback(lang: str) -> bool:
    sheet_service = GoogleSheetReadService()
    rows = sheet_service.list_legislation()
    people = sheet_service.list_people()
    return _render_sheet_legislation_rows(rows, people, lang)


def _render_sheet_legislation_rows(rows: list[dict[str, object]], people: list[dict[str, object]], lang: str) -> bool:
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
    typed_rows = _dedupe_sheet_legislation_rows(_filter_sheet_rows_by_type(rows, selected_type))

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

    person_lookup = _build_sheet_person_lookup(people)
    for idx, selected in enumerate(filtered_rows, start=1):
        sponsors = _sheet_sponsors(selected, person_lookup=person_lookup)
        _render_sheet_legislation_card(selected, sponsors, lang, idx)
    return True


def _render_sheet_legislation_card(selected: dict[str, object], sponsors: list[dict[str, object]], lang: str, index: int) -> None:
    chamber_label = "所屬議院" if lang == "zh-TW" else "Chamber"
    sponsor_label = "提案議員" if lang == "zh-TW" else "Sponsor"
    cosponsor_label = "共同提案議員" if lang == "zh-TW" else "Cosponsor"
    introduced_label = "提案時間" if lang == "zh-TW" else "Introduced"

    level = str(selected.get("level") or "").strip().lower()
    chamber = str(selected.get("chamber") or "").strip().lower()
    jurisdiction = str(selected.get("jurisdiction_name") or selected.get("jurisdiction") or "").strip()
    if not level:
        level = "federal" if jurisdiction.lower() in {"united states", "us", "u.s."} else "state"

    chamber_text = dashboard._format_legislation_chamber(
        level=level,
        chamber=chamber,
        jurisdiction_name=jurisdiction,
        lang=lang,
    )

    sponsors = dashboard._dedupe_people_for_display(sponsors)
    sponsor = sponsors[0] if sponsors else None
    sponsor_text = dashboard._format_people_inline([sponsor], lang) if sponsor else ("未提供" if lang == "zh-TW" else "Not available")
    cosponsor_text = _format_cosponsor_people(sponsors[1:], lang)

    with st.container(border=True):
        preferred_title = dashboard._select_preferred_legislation_title(
            title=str(selected.get("title") or ""),
            source_url=str(selected.get("source_url") or selected.get("official_page") or ""),
            raw_payload=selected.get("raw_payload") if isinstance(selected.get("raw_payload"), dict) else {},
        )
        title = dashboard._format_legislation_title_with_description(
            title=preferred_title,
            summary=str(selected.get("summary") or ""),
            lang=lang,
        )
        bill_number = str(selected.get("bill_number") or "").strip()
        if bill_number:
            title = f"{bill_number} {title}".strip()
        st.markdown(f"**{index}. {title}**")
        st.markdown(f"`{chamber_label}`：{chamber_text}")
        st.markdown(f"`{sponsor_label}`：{sponsor_text}")
        st.markdown(f"`{cosponsor_label}`：{cosponsor_text}")
        st.markdown(f"`{introduced_label}`：{_format_date(selected.get('date_date'))}")
        source_url = str(selected.get("source_url") or selected.get("official_page") or "").strip()
        if source_url:
            st.markdown(f"[link]({source_url})")

def _format_cosponsor_people(people: list[dict[str, object]], lang: str) -> str:
    deduped_people = dashboard._dedupe_people_for_display(people)
    valid = [item for item in deduped_people if isinstance(item, dict) and str(item.get("display_name") or item.get("english_name") or "").strip()]
    if not valid:
        return "無" if lang == "zh-TW" else "None"
    shown = valid[:3]
    text = dashboard._format_people_inline(shown, lang)
    extra = len(valid) - len(shown)
    if extra > 0:
        return f"{text} 等{extra}名" if lang == "zh-TW" else f"{text} and {extra} more"
    return text

def _sheet_sponsors(selected: dict[str, object], person_lookup: dict[str, int]) -> list[dict[str, object]]:
    sponsor_ids = list(selected.get("sponsor_ids_list") or [])
    sponsor_names = list(selected.get("sponsors_en_list") or [])
    sponsor_names_zh = list(selected.get("sponsors_zh_list") or [])
    sponsors: list[dict[str, object]] = []
    for index, name in enumerate(sponsor_names):
        person_id = sponsor_ids[index] if index < len(sponsor_ids) else None
        en_name = str(name or "").strip()
        zh_name = str(sponsor_names_zh[index] or "").strip() if index < len(sponsor_names_zh) else ""
        if not en_name and not zh_name:
            continue
        if person_id is None:
            person_id = _resolve_sheet_person_id(en_name, zh_name, person_lookup)
        sponsors.append(
            {
                "person_id": person_id,
                "display_name": zh_name or en_name,
                "english_name": en_name,
                "chinese_name": zh_name,
            }
        )
    return sponsors


def _current_chinese_alias(person: Person) -> str:
    for alias in getattr(person, "aliases", []) or []:
        if (
            getattr(alias, "is_current", False)
            and str(getattr(alias, "alias_type", "")) == "chinese_name"
            and getattr(alias, "alias", None)
        ):
            return str(alias.alias).strip()
    return ""


def _build_sheet_person_lookup(people: list[dict[str, object]]) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for item in people:
        person_id = item.get("person_id")
        if not person_id:
            continue
        for raw_name in (item.get("display_name_en"), item.get("full_name"), item.get("display_name_zh")):
            key = _normalize_sheet_person_name(raw_name)
            if key and key not in lookup:
                lookup[key] = int(person_id)
    return lookup


def _resolve_sheet_person_id(english_name: str, chinese_name: str, person_lookup: dict[str, int]) -> int | None:
    for raw_name in (english_name, chinese_name):
        key = _normalize_sheet_person_name(raw_name)
        if key and key in person_lookup:
            return person_lookup[key]
    return None


def _normalize_sheet_person_name(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[\s\.\,\-\(\)\'\"]+", "", text)


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
        jurisdiction = str(item.get("jurisdiction") or item.get("jurisdiction_name") or "").strip().lower()
        session_year = str(item.get("session_year") or item.get("session") or "").strip()
        bill_number = str(item.get("bill_number") or "").strip().lower()
        if session_year.isdigit() and int(session_year) >= 100:
            return True
        if bill_number.startswith(("hr ", "hres", "hjres", "hconres", "s ", "sres", "sjres", "sconres")):
            return True
        return jurisdiction in {"united states", "us", "u.s."}

    if selected_type == "federal":
        return [item for item in rows if is_federal(item)]
    if selected_type == "state":
        return [item for item in rows if not is_federal(item)]
    return rows


def _list_years(rows: Iterable[Legislation]) -> list[int]:
    years = {
        _effective_legislation_date(row).year
        for row in rows
        if _effective_legislation_date(row)
    }
    return sorted(years, reverse=True)


def _list_months(rows: Iterable[Legislation], year: int) -> list[int]:
    months = {
        _effective_legislation_date(row).month
        for row in rows
        if _effective_legislation_date(row)
        and _effective_legislation_date(row).year == year
    }
    return sorted(months, reverse=True)


def _rows_for_year_month(rows: Iterable[Legislation], year: int, month: int) -> list[Legislation]:
    if month == 0:
        return [
            row
            for row in rows
            if _effective_legislation_date(row)
            and _effective_legislation_date(row).year == year
        ]
    return [
        row
        for row in rows
        if _effective_legislation_date(row)
        and _effective_legislation_date(row).year == year
        and _effective_legislation_date(row).month == month
    ]


def _format_date(value) -> str:
    if value is None:
        return "N/A"
    return value.strftime("%Y-%m-%d")


def _dedupe_db_legislation_rows(rows: list[Legislation]) -> list[Legislation]:
    deduped: list[Legislation] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.bill_slug or "").strip().lower()
        if not key:
            key = "|".join(
                [
                    str(row.level or "").strip().lower(),
                    str(row.jurisdiction_name or "").strip().lower(),
                    str(row.bill_number or "").strip().lower(),
                    str(row.title or "").strip().lower(),
                ]
            )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _dedupe_sheet_legislation_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in rows:
        key = _sheet_legislation_identity_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _sheet_legislation_identity_key(item: dict[str, object]) -> str:
    jurisdiction = str(item.get("jurisdiction") or item.get("jurisdiction_name") or "").strip().lower()
    session_year = str(item.get("session_year") or item.get("session") or "").strip().lower()
    bill_number = str(item.get("bill_number") or "").strip().lower()
    title = str(item.get("title") or "").strip().lower()
    date_value = item.get("date_date")
    year_text = str(getattr(date_value, "year", "") or "")
    if bill_number:
        return f"{jurisdiction}|{session_year}|{bill_number}"
    return f"{jurisdiction}|{session_year}|{title}|{year_text}"


def _effective_legislation_date(row: Legislation) -> date | None:
    if row.introduced_date:
        return row.introduced_date
    payload = row.raw_payload or {}
    # Prefer official introduced date from Congress payload before last action date.
    for key in ("introduced_on_congress", "introduced_date"):
        parsed = _parse_date_value(payload.get(key))
        if parsed:
            return parsed
    if row.last_action_date:
        return row.last_action_date
    for key in ("latest_action_date", "update_date", "update_date_including_text"):
        parsed = _parse_date_value(payload.get(key))
        if parsed:
            return parsed
    return None


def _parse_date_value(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for parser in (date.fromisoformat, lambda raw: datetime.fromisoformat(raw).date()):
        try:
            return parser(text)
        except ValueError:
            continue
    return None
