from __future__ import annotations

import json
import re
from datetime import date, datetime
from html import escape
from typing import Iterable

import streamlit as st

from tracker.config import use_google_sheet_primary_mode
from tracker.db import session_scope
from tracker.models import Legislation, Person
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.legislation_ai_enrichment_service import LegislationAIEnrichmentService
from tracker.services.legislation_service import LegislationService
from tracker.services.manual_legislation_ingest_service import ManualLegislationIngestService
from tracker.ui.navigation import render_person_links
from tracker.ui import dashboard
from tracker.utils.congress_bills import congress_bill_url
from tracker.utils.source_types import source_bucket_label, source_priority_key


def _payload_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _select_preferred_legislation_title(title: str, source_url: str, raw_payload: dict[str, object] | None) -> str:
    selector = getattr(dashboard, "_select_preferred_legislation_title", None)
    if callable(selector):
        return str(selector(title=title, source_url=source_url, raw_payload=raw_payload))
    title_text = str(title or "").strip()
    if not title_text:
        return ""
    candidates = [part.strip() for part in re.split(r"\s*[|｜]+\s*", title_text) if part.strip()]
    return candidates[0] if candidates else title_text


def _should_prefix_bill_number(bill_number: str) -> bool:
    checker = getattr(dashboard, "_should_prefix_bill_number", None)
    if callable(checker):
        return bool(checker(bill_number))
    text = str(bill_number or "").strip()
    if not text:
        return False
    return text.lower() not in {"n/a", "na", "unknown", "none", "null"}


def _clean_legislation_summary_display(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("{") and '"summary"' in text:
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                candidate = str(payload.get("summary") or "").strip()
                if candidate:
                    return re.sub(r"\s+", " ", candidate)
        except Exception:
            pass
    match = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if match:
        candidate = match.group(1).replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
        candidate = candidate.replace('\"', '"').replace('\\', '\\').strip()
        if candidate:
            return re.sub(r"\s+", " ", candidate)
    return re.sub(r"\s+", " ", text)


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["legislation"])
    selected_legislation_id = _query_legislation_id()
    with session_scope() as session:
        service = LegislationService(session)
        _render_manual_legislation_ingest_form(session, lang)
        people_by_id = {person.id: person for person in session.query(Person).all()}
        all_rows = session.query(Legislation).order_by(
            Legislation.introduced_date.desc().nullslast(),
            Legislation.last_action_date.desc().nullslast(),
            Legislation.id.desc(),
        ).all()

        if not all_rows:
            st.info("目前還沒有立法資料。" if lang == "zh-TW" else "No legislation is available yet.")
            return

        if selected_legislation_id is not None:
            selected = next((row for row in all_rows if int(row.id) == int(selected_legislation_id)), None)
            if not selected:
                st.warning("找不到指定法案，已返回列表。" if lang == "zh-TW" else "Requested bill was not found. Returned to list.")
                _clear_legislation_id()
                st.rerun()
                return
            _render_legislation_detail(selected, service, people_by_id, lang)
            return

        type_label = "法案類型" if lang == "zh-TW" else "Legislation type"
        year_label = "年份" if lang == "zh-TW" else "Year"
        month_label = "月份" if lang == "zh-TW" else "Month"
        type_options = _type_options(lang)
        selected_type = st.selectbox(type_label, list(type_options.keys()), format_func=lambda key: type_options[key])

        typed_rows = _dedupe_db_legislation_rows(_filter_db_rows_by_type(all_rows, selected_type))
        if selected_type == "state":
            state_label = "州" if lang == "zh-TW" else "State"
            states = sorted(
                {
                    str(row.jurisdiction_name or "").strip()
                    for row in typed_rows
                    if str(row.jurisdiction_name or "").strip()
                }
            )
            if states:
                selected_state = st.selectbox(state_label, states, key="legislation-state-filter")
                typed_rows = [
                    row
                    for row in typed_rows
                    if str(row.jurisdiction_name or "").strip() == selected_state
                ]

        years = _list_years(typed_rows)
        if not years:
            st.info("目前沒有符合條件的法案。" if lang == "zh-TW" else "No legislation matches this filter.")
            return

        year_options: list[object] = ["all", *years]
        selected_year = st.selectbox(
            year_label,
            year_options,
            format_func=lambda value: ("全部" if lang == "zh-TW" else "All") if value == "all" else str(value),
        )
        if selected_year == "all":
            selected_month = 0
        else:
            months = [0, *_list_months(typed_rows, int(selected_year))]
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


def _render_manual_legislation_ingest_form(session, lang: str) -> None:
    flash_key = "manual-legislation-ingest-flash"
    flash = st.session_state.pop(flash_key, None)
    if flash:
        message = str(flash.get("message") or "")
        level = str(flash.get("level") or "success")
        if level == "warning":
            st.warning(message)
        elif level == "error":
            st.error(message)
        else:
            st.success(message)
        errors = [str(item) for item in flash.get("errors") or [] if str(item).strip()]
        if errors:
            with st.expander("錯誤明細" if lang == "zh-TW" else "Error details"):
                for error in errors[:10]:
                    st.write(error)
        items = flash.get("items") or []
        if items:
            with st.expander("匯入結果明細" if lang == "zh-TW" else "Import result details"):
                st.json(items[:30])

    title = "手動批次新增法案" if lang == "zh-TW" else "Manual Bill Batch Import"
    help_text = (
        "貼上 Congress.gov 或州議會法案網址，一行一筆。系統會先用 OpenAI 抽基本欄位讓法案入庫，再在有 GEMINI_API_KEY 時自動用 Gemini 補背景資料、日期、狀態與提案人來源。Congress.gov 仍會優先使用官方資料。"
        if lang == "zh-TW"
        else "Paste Congress.gov or state legislature bill URLs, one per line. Bills are first seeded with OpenAI-extracted fields, then automatically enriched with Gemini background research when GEMINI_API_KEY is configured. Congress.gov bills still prefer official details first."
    )
    with st.expander(title, expanded=False):
        st.caption(help_text)
        with st.form("manual-legislation-ingest-form", clear_on_submit=True):
            raw_urls = st.text_area(
                "法案網址（每行一筆）" if lang == "zh-TW" else "Bill URLs (one per line)",
                height=150,
                placeholder=(
                    "https://www.congress.gov/bill/119th-congress/senate-bill/1216\nhttps://legiscan.com/CA/bill/SBXX/2025"
                    if lang == "zh-TW"
                    else "https://www.congress.gov/bill/119th-congress/senate-bill/1216\nhttps://legiscan.com/CA/bill/SBXX/2025"
                ),
                key="manual-legislation-ingest-urls",
            )
            submitted = st.form_submit_button("加入並補上法案細節" if lang == "zh-TW" else "Import and enrich bill details")

        if not submitted:
            return
        if not str(raw_urls or "").strip():
            st.warning("請先貼上至少一個法案網址。" if lang == "zh-TW" else "Paste at least one bill URL first.")
            return

        try:
            with st.spinner("正在加入法案並補上細節，請稍候..." if lang == "zh-TW" else "Importing and enriching bill details..."):
                result = ManualLegislationIngestService(session).import_from_urls(raw_urls)
                session.commit()
        except Exception as exc:
            session.rollback()
            st.error(("匯入失敗：" if lang == "zh-TW" else "Import failed: ") + f"{type(exc).__name__}: {exc}")
            return

        level = "warning" if result.failed or result.detail_failed else "success"
        message = (
            f"法案匯入完成：新增 {result.created}、更新 {result.updated}、Congress.gov 官方補詳情 {result.detail_ok}、"
            f"OpenAI 補資料 {result.ai_detail_ok}、Gemini 背景補強 {result.gemini_detail_ok}、Gemini 失敗 {result.gemini_detail_failed}、詳情未補齊 {result.detail_failed}、提案人 +{result.sponsors_added}、"
            f"共同提案人 +{result.cosponsors_added}、州議會/其他網址 {result.other_urls}、加入失敗 {result.failed}。"
            if lang == "zh-TW"
            else f"Bill import complete: created {result.created}, updated {result.updated}, Congress.gov official details {result.detail_ok}, "
            f"OpenAI details {result.ai_detail_ok}, Gemini background enrichment {result.gemini_detail_ok}, Gemini failed {result.gemini_detail_failed}, detail incomplete {result.detail_failed}, sponsors +{result.sponsors_added}, "
            f"cosponsors +{result.cosponsors_added}, state/other URLs {result.other_urls}, import failed {result.failed}."
        )
        st.session_state[flash_key] = {
            "level": level,
            "message": message,
            "errors": result.errors[:10],
            "items": result.items[:30],
        }
        st.rerun()


def _render_legislation_detail(selected: Legislation, service: LegislationService, people_by_id: dict[int, Person], lang: str) -> None:
    flash_key = f"legislation-ai-enrich-flash-{selected.id}"
    flash = st.session_state.pop(flash_key, None)
    if flash:
        message = str(flash.get("message") or "")
        if flash.get("ok"):
            st.success(message)
        else:
            st.error(message)
        details = flash.get("details") or {}
        if details:
            with st.expander("更新記錄" if lang == "zh-TW" else "Refresh details"):
                if isinstance(details, dict):
                    provider = str(details.get("provider") or "").strip().upper()
                    if provider:
                        st.write(("本次使用：" if lang == "zh-TW" else "Provider: ") + provider)
                    updated_fields = [str(item).strip() for item in details.get("updated_fields") or [] if str(item).strip()]
                    if updated_fields:
                        st.write(("更新欄位：" if lang == "zh-TW" else "Updated fields: ") + ", ".join(updated_fields))
                    st.write(
                        ("提案人新增：" if lang == "zh-TW" else "Sponsors linked: ") + str(int(details.get("sponsors_linked") or 0))
                    )
                    st.write(
                        ("共同提案人新增：" if lang == "zh-TW" else "Cosponsors linked: ") + str(int(details.get("cosponsors_linked") or 0))
                    )
                    skipped = [
                        str((item or {}).get("full_name") or "").strip()
                        for item in details.get("skipped_sponsors") or []
                        if isinstance(item, dict)
                    ]
                    skipped = [name for name in skipped if name]
                    if skipped:
                        st.write(("略過名單：" if lang == "zh-TW" else "Skipped: ") + ", ".join(skipped[:10]))

                sources = details.get("sources") if isinstance(details, dict) else []
                if isinstance(sources, list) and sources:
                    st.markdown("`參考來源`：" if lang == "zh-TW" else "`Sources`:")
                    for idx, source in enumerate(sources[:20], start=1):
                        if not isinstance(source, dict):
                            continue
                        title = str(source.get("title") or source.get("url") or "").strip()
                        url = str(source.get("url") or "").strip()
                        if not url:
                            continue
                        st.markdown(f"{idx}. [{title or url}]({url})")

    if st.button("返回法案列表" if lang == "zh-TW" else "Back to legislation list", key=f"legislation-back-{selected.id}"):
        _clear_legislation_id()
        st.rerun()

    raw_payload = _payload_dict(selected.raw_payload)
    official_link = raw_payload.get("congress_gov_url") or congress_bill_url(
        raw_payload.get("congress"),
        selected.bill_number,
    ) or selected.source_url
    bill_text_url = raw_payload.get("text_page_url")
    sponsors, cosponsors = _split_db_sponsors(service.list_sponsors(selected.id), people_by_id)
    summary = _clean_legislation_summary_display(selected.summary or raw_payload.get("summary"))
    latest_action = str(raw_payload.get("latest_action_text") or "").strip()
    if not summary:
        summary = latest_action or (str(selected.title or "").strip())

    with st.container(border=True):
        heading = str(selected.title or "").strip()
        if _should_prefix_bill_number(str(selected.bill_number or "")):
            heading = f"{selected.bill_number} {heading}".strip()
        st.markdown(f"### {heading}")
        st.markdown(f"`{'標題' if lang == 'zh-TW' else 'Title'}`：{selected.title or 'N/A'}")
        st.markdown(f"`{'摘要' if lang == 'zh-TW' else 'Summary'}`：{summary}")
        st.markdown(f"`{'提案日期' if lang == 'zh-TW' else 'Introduced'}`：{_format_date(_effective_legislation_date(selected))}")
        st.markdown(f"`{'目前狀態' if lang == 'zh-TW' else 'Current status'}`：{selected.status_text or ('未知' if lang == 'zh-TW' else 'Unknown')}")
        if official_link:
            st.markdown(f"`Congress.gov`：[Congress.gov]({official_link})")
        if bill_text_url:
            st.markdown(f"`{'法案全文' if lang == 'zh-TW' else 'Bill text'}`：[{'法案全文' if lang == 'zh-TW' else 'Bill text'}]({bill_text_url})")
        if latest_action:
            st.markdown(f"`{'最新動作' if lang == 'zh-TW' else 'Latest action'}`：{latest_action}")

        if st.button(
            "重新整理" if lang == "zh-TW" else "Refresh",
            key=f"legislation-refresh-{selected.id}",
        ):
            try:
                with st.spinner("正在重新整理法案資料..." if lang == "zh-TW" else "Refreshing legislation data..."):
                    enrichment = LegislationAIEnrichmentService(service.session).refresh_with_ai(int(selected.id))
                    service.session.commit()
                st.session_state[f"legislation-ai-enrich-flash-{selected.id}"] = {
                    "ok": enrichment.ok,
                    "message": (
                        f"{enrichment.message} 更新欄位 {len(enrichment.updated_fields)} 個、連結提案人 +{enrichment.sponsors_linked}、共同提案人 +{enrichment.cosponsors_linked}、略過 {len(enrichment.skipped_sponsors)} 個。"
                        if lang == "zh-TW"
                        else f"{enrichment.message} Updated {len(enrichment.updated_fields)} fields, linked sponsors +{enrichment.sponsors_linked}, cosponsors +{enrichment.cosponsors_linked}, skipped {len(enrichment.skipped_sponsors)}."
                    ),
                    "details": {
                        "provider": enrichment.provider,
                        "updated_fields": enrichment.updated_fields,
                        "sponsors_linked": enrichment.sponsors_linked,
                        "cosponsors_linked": enrichment.cosponsors_linked,
                        "skipped_sponsors": enrichment.skipped_sponsors,
                        "sources": enrichment.sources,
                    },
                }
            except Exception as exc:
                service.session.rollback()
                st.session_state[f"legislation-ai-enrich-flash-{selected.id}"] = {
                    "ok": False,
                    "message": f"AI 補資料失敗：{type(exc).__name__}: {exc}" if lang == "zh-TW" else f"AI enrichment failed: {type(exc).__name__}: {exc}",
                }
            st.rerun()

        st.markdown(f"`{'提案人' if lang == 'zh-TW' else 'Sponsor'}`：")
        if sponsors:
            render_person_links(sponsors, lang, key_prefix=f"legislation-detail-sponsor-{selected.id}")
        else:
            st.write("未提供" if lang == "zh-TW" else "Not available")

        st.markdown(f"`{'共同提案人' if lang == 'zh-TW' else 'Cosponsors'}`：")
        if cosponsors:
            render_person_links(cosponsors, lang, key_prefix=f"legislation-detail-cosponsor-{selected.id}")
        else:
            st.write("無" if lang == "zh-TW" else "None")


def _split_db_sponsors(records: list[object], people_by_id: dict[int, Person]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    sponsors: list[dict[str, object]] = []
    cosponsors: list[dict[str, object]] = []
    for record in records:
        person = people_by_id.get(int(getattr(record, "person_id", 0) or 0))
        if not person:
            continue
        chinese_name = _current_chinese_alias(person)
        payload = {
            "person_id": int(person.id),
            "display_name": chinese_name or person.full_name,
            "english_name": person.full_name,
            "chinese_name": chinese_name,
        }
        role = str(getattr(record, "role", "") or "").lower()
        if role == "cosponsor":
            cosponsors.append(payload)
        else:
            sponsors.append(payload)
    deduped_sponsors = _dedupe_people_for_display(sponsors)
    deduped_cosponsors = [item for item in _dedupe_people_for_display(cosponsors) if all(item.get("person_id") != s.get("person_id") for s in deduped_sponsors)]
    return deduped_sponsors, deduped_cosponsors


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

    raw_payload = _payload_dict(selected.raw_payload)
    official_link = raw_payload.get("congress_gov_url") or congress_bill_url(
        raw_payload.get("congress"),
        selected.bill_number,
    ) or selected.source_url
    source_links = _collect_db_source_links(selected)

    chamber_text = dashboard._format_legislation_chamber(
        level=str(selected.level or ""),
        chamber=str(selected.chamber or ""),
        jurisdiction_name=str(selected.jurisdiction_name or ""),
        lang=lang,
    )

    with st.container(border=True):
        preferred_title = _select_preferred_legislation_title(
            title=str(selected.title or ""),
            source_url=str(official_link or selected.source_url or ""),
            raw_payload=raw_payload if isinstance(raw_payload, dict) else {},
        )
        title = dashboard._format_legislation_title_with_description(
            title=preferred_title,
            summary=str(selected.summary or ""),
            lang=lang,
        )
        if _should_prefix_bill_number(str(selected.bill_number or "")):
            title = f"{selected.bill_number} {title}".strip()
        link = f"?page=legislation&legislation_id={int(selected.id)}"
        st.markdown(f'**{index}. <a href="{link}" target="_self">{escape(title)}</a>**', unsafe_allow_html=True)
        st.markdown(f"`{chamber_label}`：{chamber_text}")
        st.markdown(f"`{sponsor_label}`：{sponsor_text}")
        st.markdown(f"`{cosponsor_label}`：{cosponsor_text}")
        st.markdown(f"`{introduced_label}`：{_format_date(_effective_legislation_date(selected))}")
        _render_legislation_links(source_links or ([str(official_link)] if official_link else []))

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

    year_options: list[object] = ["all", *years]
    selected_year = st.selectbox(
        year_label,
        year_options,
        format_func=lambda value: ("全部" if lang == "zh-TW" else "All") if value == "all" else str(value),
        key="sheet-legislation-year",
    )
    if selected_year == "all":
        selected_month = 0
    else:
        months = sorted(
            {row.get("date_date").month for row in typed_rows if row.get("date_date") and row["date_date"].year == int(selected_year)},
            reverse=True,
        )
        months = [0, *months]
        selected_month = st.selectbox(
            month_label,
            months,
            format_func=lambda value: ("全部" if lang == "zh-TW" else "All") if value == 0 else f"{value:02d}",
            key="sheet-legislation-month",
        )

    filtered_rows = _rows_for_sheet_year_month(typed_rows, selected_year, selected_month)
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

    sponsors = _dedupe_people_for_display(sponsors)
    sponsor = sponsors[0] if sponsors else None
    sponsor_text = dashboard._format_people_inline([sponsor], lang) if sponsor else ("未提供" if lang == "zh-TW" else "Not available")
    cosponsor_text = _format_cosponsor_people(sponsors[1:], lang)

    with st.container(border=True):
        preferred_title = _select_preferred_legislation_title(
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
        if _should_prefix_bill_number(bill_number):
            title = f"{bill_number} {title}".strip()
        st.markdown(f"**{index}. {title}**")
        st.markdown(f"`{chamber_label}`：{chamber_text}")
        st.markdown(f"`{sponsor_label}`：{sponsor_text}")
        st.markdown(f"`{cosponsor_label}`：{cosponsor_text}")
        st.markdown(f"`{introduced_label}`：{_format_date(selected.get('date_date'))}")
        source_links = _collect_sheet_source_links(selected)
        _render_legislation_links(source_links)


def _render_legislation_links(links: list[str]) -> None:
    clean_links = [str(link or "").strip() for link in links if str(link or "").strip()]
    if not clean_links:
        return
    rendered = " | ".join(f"[link]({link})" for link in clean_links[:6])
    st.markdown(rendered)

def _format_cosponsor_people(people: list[dict[str, object]], lang: str) -> str:
    deduped_people = _dedupe_people_for_display(people)
    valid = [item for item in deduped_people if isinstance(item, dict) and str(item.get("display_name") or item.get("english_name") or "").strip()]
    if not valid:
        return "無" if lang == "zh-TW" else "None"
    shown = valid[:3]
    text = dashboard._format_people_inline(shown, lang)
    extra = len(valid) - len(shown)
    if extra > 0:
        return f"{text} 等{extra}名" if lang == "zh-TW" else f"{text} and {extra} more"
    return text


def _dedupe_people_for_display(people: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[str] = set()
    for person in people:
        if not isinstance(person, dict):
            continue
        person_id = person.get("person_id")
        english_name = str(person.get("english_name") or "").strip()
        chinese_name = str(person.get("chinese_name") or "").strip()
        display_name = str(person.get("display_name") or "").strip()
        key_parts = [
            str(person_id) if person_id not in (None, "") else "",
            _normalize_person_name_key(english_name),
            _normalize_person_name_key(chinese_name),
            _normalize_person_name_key(display_name),
        ]
        key = "|".join(part for part in key_parts if part)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(person)
    return deduped


def _normalize_person_name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value or "").lower())

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


def _rows_for_year_month(rows: Iterable[Legislation], year: object, month: int) -> list[Legislation]:
    if year == "all":
        return list(rows)
    year_int = int(year)
    if month == 0:
        return [
            row
            for row in rows
            if _effective_legislation_date(row)
            and _effective_legislation_date(row).year == year_int
        ]
    return [
        row
        for row in rows
        if _effective_legislation_date(row)
        and _effective_legislation_date(row).year == year_int
        and _effective_legislation_date(row).month == month
    ]


def _rows_for_sheet_year_month(rows: Iterable[dict[str, object]], year: object, month: int) -> list[dict[str, object]]:
    if year == "all":
        return list(rows)
    year_int = int(year)
    if month == 0:
        return [row for row in rows if row.get("date_date") and row["date_date"].year == year_int]
    return [
        row
        for row in rows
        if row.get("date_date")
        and row["date_date"].year == year_int
        and row["date_date"].month == month
    ]


def _format_date(value) -> str:
    if value is None:
        return "N/A"
    return value.strftime("%Y-%m-%d")


def _query_legislation_id() -> int | None:
    raw_value = st.query_params.get("legislation_id")
    if raw_value in (None, ""):
        return None
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else None
    if raw_value in (None, ""):
        return None
    text = str(raw_value).strip()
    return int(text) if text.isdigit() else None


def _clear_legislation_id() -> None:
    if "legislation_id" in st.query_params:
        del st.query_params["legislation_id"]


def _dedupe_db_legislation_rows(rows: list[Legislation]) -> list[Legislation]:
    best_by_key: dict[str, Legislation] = {}
    source_links_by_key: dict[str, set[str]] = {}
    order: list[str] = []
    for row in rows:
        key = str(row.bill_slug or "").strip().lower()
        if not key:
            bill_number = re.sub(r"[^a-z0-9]", "", str(row.bill_number or "").strip().lower())
            source_bill = _extract_bill_number_from_source_url(str(row.source_url or ""))
            semantic_title = _semantic_legislation_title_key(str(row.title or ""))
            effective = _effective_legislation_date(row)
            key = "|".join(
                [
                    str(row.level or "").strip().lower(),
                    str(row.jurisdiction_name or "").strip().lower(),
                    bill_number or source_bill or semantic_title or str(row.title or "").strip().lower(),
                    str(effective.year if effective else ""),
                ]
            )
        source_links_by_key.setdefault(key, set()).update(_collect_db_source_links(row))
        if key not in best_by_key:
            best_by_key[key] = row
            order.append(key)
            continue
        if _db_row_quality_score(row) > _db_row_quality_score(best_by_key[key]):
            best_by_key[key] = row

    deduped: list[Legislation] = []
    for key in order:
        selected = best_by_key[key]
        merged_links = _sort_source_links(source_links_by_key.get(key, set()))
        payload = _payload_dict(selected.raw_payload)
        payload["_source_urls"] = merged_links
        selected.raw_payload = payload
        deduped.append(selected)
    return deduped


def _dedupe_sheet_legislation_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    best_by_key: dict[str, dict[str, object]] = {}
    source_links_by_key: dict[str, set[str]] = {}
    order: list[str] = []
    for item in rows:
        key = _sheet_legislation_identity_key(item)
        source_links_by_key.setdefault(key, set()).update(_collect_sheet_source_links(item))
        if key not in best_by_key:
            best_by_key[key] = item
            order.append(key)
            continue
        if _sheet_row_quality_score(item) > _sheet_row_quality_score(best_by_key[key]):
            best_by_key[key] = item
    deduped: list[dict[str, object]] = []
    for key in order:
        selected = dict(best_by_key[key])
        selected["_source_urls"] = _sort_source_links(source_links_by_key.get(key, set()))
        deduped.append(selected)
    return _merge_related_sheet_rows(deduped)


def _sheet_legislation_identity_key(item: dict[str, object]) -> str:
    jurisdiction = str(item.get("jurisdiction") or item.get("jurisdiction_name") or "").strip().lower()
    session_year = str(item.get("session_year") or item.get("session") or "").strip().lower()
    bill_number = str(item.get("bill_number") or "").strip().lower()
    # Normalize variants such as "H.R. 8177" / "HR 8177" / "hr8177".
    bill_number = re.sub(r"[^a-z0-9]", "", bill_number)
    source_bill = _extract_bill_number_from_source_url(str(item.get("source_url") or item.get("official_page") or ""))
    if source_bill and not bill_number:
        bill_number = source_bill
    title = str(item.get("title") or "").strip().lower()
    date_value = item.get("date_date")
    year_text = str(getattr(date_value, "year", "") or "")
    if bill_number:
        return f"{jurisdiction}|{session_year}|{bill_number}"
    title_bill = _extract_bill_number_from_title(str(item.get("title") or ""))
    if title_bill:
        return f"{jurisdiction}|{session_year}|{title_bill}"
    semantic_title = _semantic_legislation_title_key(str(item.get("title") or ""))
    return f"{jurisdiction}|{session_year}|{semantic_title or title}|{year_text}"


def _extract_bill_number_from_title(title: str) -> str:
    text = str(title or "").strip().lower()
    if not text:
        return ""
    normalized = re.sub(r"[^a-z0-9]", "", text)
    match = re.search(r"(hr|hres|hjres|hconres|s|sres|sjres|sconres)\d{1,6}", normalized)
    return match.group(0) if match else ""


def _extract_bill_number_from_source_url(url: str) -> str:
    text = str(url or "").strip().lower()
    if not text:
        return ""
    match = re.search(
        r"/bill/\d+(?:st|nd|rd|th)-congress/(house-bill|senate-bill|house-resolution|senate-resolution|house-joint-resolution|senate-joint-resolution|house-concurrent-resolution|senate-concurrent-resolution)/(\d+)",
        text,
    )
    if not match:
        return ""
    kind = match.group(1)
    number = match.group(2)
    mapping = {
        "house-bill": "hr",
        "senate-bill": "s",
        "house-resolution": "hres",
        "senate-resolution": "sres",
        "house-joint-resolution": "hjres",
        "senate-joint-resolution": "sjres",
        "house-concurrent-resolution": "hconres",
        "senate-concurrent-resolution": "sconres",
    }
    prefix = mapping.get(kind, "")
    return f"{prefix}{number}" if prefix else ""


def _semantic_legislation_title_key(title: str) -> str:
    text = str(title or "").lower()
    if not text:
        return ""
    tokens = re.findall(r"[a-z0-9']+", text)
    stop = {
        "a",
        "an",
        "the",
        "and",
        "or",
        "to",
        "of",
        "for",
        "in",
        "on",
        "with",
        "from",
        "by",
        "this",
        "that",
        "bill",
        "act",
        "resolution",
        "commending",
        "commemorating",
        "expressing",
        "support",
        "its",
        "their",
        "which",
        "how",
        "manner",
        "report",
        "require",
        "requiring",
        "submit",
    }
    normalized: list[str] = []
    for token in tokens:
        token = token.strip("'")
        token = re.sub(r"[^a-z0-9]", "", token)
        if not token or token in stop:
            continue
        if token.endswith("s") and len(token) > 4:
            token = token[:-1]
        if token.isdigit() and len(token) == 4:
            # year noise (e.g. 1996) should not split same bill cards
            continue
        normalized.append(token)
    if not normalized:
        return ""
    return "|".join(sorted(set(normalized)))


def _sheet_row_quality_score(item: dict[str, object]) -> int:
    title = str(item.get("title") or "")
    summary = str(item.get("summary") or "")
    source_url = str(item.get("source_url") or item.get("official_page") or "").lower()
    text = f"{title} {summary}"
    score = 0
    if "《" in text and "》" in text:
        score += 100
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    score += min(cjk_count, 40)
    if "congress.gov" in source_url:
        score += 10
    if str(item.get("bill_number") or "").strip():
        score += 5
    return score


def _merge_related_sheet_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for item in rows:
        target_index = -1
        for idx, existing in enumerate(merged):
            if _is_probably_same_sheet_bill(existing, item):
                target_index = idx
                break
        if target_index < 0:
            merged.append(dict(item))
            continue
        current = merged[target_index]
        keep = item if _sheet_row_quality_score(item) > _sheet_row_quality_score(current) else current
        drop = current if keep is item else item
        merged_item = dict(keep)
        merged_links = set(_collect_sheet_source_links(keep)) | set(_collect_sheet_source_links(drop))
        merged_item["_source_urls"] = _sort_source_links(merged_links)
        merged[target_index] = merged_item
    return merged


def _is_probably_same_sheet_bill(a: dict[str, object], b: dict[str, object]) -> bool:
    if str(a.get("jurisdiction") or a.get("jurisdiction_name") or "").strip().lower() != str(b.get("jurisdiction") or b.get("jurisdiction_name") or "").strip().lower():
        return False
    if str(a.get("session_year") or a.get("session") or "").strip().lower() != str(b.get("session_year") or b.get("session") or "").strip().lower():
        return False

    date_a = a.get("date_date")
    date_b = b.get("date_date")
    if date_a and date_b and date_a != date_b:
        return False

    bill_a = _extract_bill_number_from_title(str(a.get("bill_number") or "")) or _extract_bill_number_from_source_url(str(a.get("source_url") or a.get("official_page") or ""))
    bill_b = _extract_bill_number_from_title(str(b.get("bill_number") or "")) or _extract_bill_number_from_source_url(str(b.get("source_url") or b.get("official_page") or ""))
    if bill_a and bill_b:
        return bill_a == bill_b

    tokens_a = set((_semantic_legislation_title_key(str(a.get("title") or "")) or "").split("|"))
    tokens_b = set((_semantic_legislation_title_key(str(b.get("title") or "")) or "").split("|"))
    tokens_a = {t for t in tokens_a if t}
    tokens_b = {t for t in tokens_b if t}
    if not tokens_a or not tokens_b:
        return False

    overlap = len(tokens_a & tokens_b) / max(1, min(len(tokens_a), len(tokens_b)))
    if overlap < 0.75:
        return False

    sponsor_a = _sheet_primary_sponsor_key(a)
    sponsor_b = _sheet_primary_sponsor_key(b)
    if sponsor_a and sponsor_b:
        return sponsor_a == sponsor_b

    # If semantic overlap is very high and dates match, treat as the same bill even when media rows omit bill id.
    return overlap >= 0.9


def _sheet_primary_sponsor_key(item: dict[str, object]) -> str:
    ids = list(item.get("sponsor_ids_list") or [])
    if ids:
        return f"id:{ids[0]}"
    names = list(item.get("sponsors_en_list") or [])
    if names:
        return re.sub(r"[^a-z0-9]", "", str(names[0]).lower())
    return ""


def _db_row_quality_score(row: Legislation) -> int:
    title = str(row.title or "")
    summary = str(row.summary or "")
    source_url = str(row.source_url or "").lower()
    text = f"{title} {summary}"
    score = 0
    if "《" in text and "》" in text:
        score += 100
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    score += min(cjk_count, 40)
    if "congress.gov" in source_url:
        score += 10
    if str(row.bill_number or "").strip():
        score += 5
    return score


def _collect_sheet_source_links(item: dict[str, object]) -> list[str]:
    links: set[str] = set()
    for key in ("source_url", "official_page"):
        value = str(item.get(key) or "").strip()
        if value:
            links.add(value)
    for value in item.get("_source_urls", []) or []:
        link = str(value or "").strip()
        if link:
            links.add(link)
    return _sort_source_links(links)


def _collect_db_source_links(row: Legislation) -> list[str]:
    links: set[str] = set()
    if row.source_url:
        links.add(str(row.source_url).strip())
    payload = _payload_dict(row.raw_payload)
    congress_link = str(payload.get("congress_gov_url") or "").strip()
    if congress_link:
        links.add(congress_link)
    official_page = str(payload.get("official_page") or "").strip()
    if official_page:
        links.add(official_page)
    for value in payload.get("_source_urls", []) if isinstance(payload.get("_source_urls"), list) else []:
        link = str(value or "").strip()
        if link:
            links.add(link)
    return _sort_source_links(links)


def _sort_source_links(links: set[str]) -> list[str]:
    return sorted(
        [link for link in links if link],
        key=lambda link: (
            0 if "congress.gov" in link.lower() else 1,
            link.lower(),
        ),
    )


def _effective_legislation_date(row: Legislation) -> date | None:
    if row.introduced_date:
        return row.introduced_date
    payload = _payload_dict(row.raw_payload)
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
