from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from tracker.config import use_google_sheet_primary_mode
from tracker.db import session_scope
from tracker.models import Appointment, Office, Person
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.wikipedia_list_service import WikipediaListService
from tracker.ui.display import localize_dataframe
from tracker.ui.navigation import person_detail_href
from tracker.ui.person_page import (
    _display_office_name,
    _executive_department_sort_key,
    _executive_hierarchy,
    _executive_role_rank,
)
from tracker.ui.social_links import render_social_links
from tracker.utils.names import display_person_name
from tracker.utils.official_search import build_google_official_bio_search_url, build_google_official_search_url, build_x_search_url
from tracker.utils.social import social_display_name
from tracker.utils.wikipedia_links import build_wikipedia_search_url, resolve_wikipedia_url


def _load_official_rows(session):
    stmt = (
        select(
            Person.id,
            Person.full_name,
            Person.given_name,
            Person.family_name,
            Office.office_name,
            Office.level,
            Office.branch,
            Appointment.party,
            Appointment.status,
            Person.source_type,
            Person.social_profiles,
            Person.source_url,
            Person.raw_payload,
            Appointment.raw_payload,
        )
        .join(Appointment, Appointment.person_id == Person.id)
        .join(Office, Office.id == Appointment.office_id)
        .order_by(Person.full_name.asc())
        .limit(500)
    )
    return session.execute(stmt).all()


def _build_dataframe(rows) -> pd.DataFrame:
    data = pd.DataFrame(
        rows,
        columns=[
            "person_id",
            "full_name",
            "given_name",
            "family_name",
            "office",
            "level",
            "branch",
            "party",
            "status",
            "source_type",
            "social_profiles",
            "source_url",
            "raw_payload",
            "appointment_raw_payload",
        ],
    )
    if data.empty:
        return data
    data["name"] = data.apply(
        lambda row: display_person_name(row["full_name"], row["given_name"], row["family_name"]),
        axis=1,
    )
    data["office_display"] = data.apply(
        lambda row: _display_office_name(row["office"], row["appointment_raw_payload"]),
        axis=1,
    )
    data["department"] = data.apply(
        lambda row: _executive_hierarchy(row["office"], row["appointment_raw_payload"])[0] if row["branch"] == "executive" and row["level"] == "federal" else None,
        axis=1,
    )
    data["subdepartment"] = data.apply(
        lambda row: _executive_hierarchy(row["office"], row["appointment_raw_payload"])[1] if row["branch"] == "executive" and row["level"] == "federal" else None,
        axis=1,
    )
    data["unit"] = data.apply(
        lambda row: _executive_hierarchy(row["office"], row["appointment_raw_payload"])[2] if row["branch"] == "executive" and row["level"] == "federal" else None,
        axis=1,
    )
    return data


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["officials"])
    if use_google_sheet_primary_mode():
        _render_google_sheet_fallback(lang, labels)
        return
    with session_scope() as session:
        importer = WikipediaListService(session)
        with st.expander(labels["wiki_import"]):
            with st.form("wikipedia_import_form"):
                list_url = st.text_input(labels["wiki_url"], value="https://en.wikipedia.org/wiki/List_of_current_United_States_senators")
                office_name = st.text_input(labels["office_name"], value="Imported office")
                role_title = st.text_input(labels["role_title"], value="Imported role")
                level = st.selectbox(labels["level"], ["federal", "state", "local", "other"])
                branch = st.selectbox(labels["branch"], ["legislative", "executive", "judicial", "other", ""])
                chamber = st.text_input(labels["chamber"], value="")
                jurisdiction_name = st.text_input(labels["jurisdiction_name"], value="United States")
                jurisdiction_type = st.selectbox(labels["jurisdiction_type"], ["country", "state", "county", "city", "other"])
                appointment_status = st.selectbox(labels["appointment_status"], ["unknown", "current", "former"])
                auto_create_trackers = st.checkbox(labels["auto_create_trackers"], value=True)
                submitted = st.form_submit_button(labels["import_list"])
            if submitted and list_url:
                result = importer.import_list(
                    list_url=list_url,
                    office_name=office_name,
                    role_title=role_title,
                    level=level,
                    branch=branch or None,
                    chamber=chamber or None,
                    jurisdiction_name=jurisdiction_name,
                    jurisdiction_type=jurisdiction_type,
                    appointment_status=appointment_status,
                    auto_create_trackers=auto_create_trackers,
                )
                st.success(f"{labels['import_list']}: {result.imported_count}")
                if result.names:
                    st.write(", ".join(result.names[:30]))

        query = st.text_input(labels["search_name"])
        status_filter = st.selectbox(labels["status_filter"], ["all", "current", "former", "unknown"])
        rows = _load_official_rows(session)

    data = _build_dataframe(rows)

    if query and not data.empty:
        data = data[data["name"].str.contains(query, case=False, na=False)]
    if status_filter != "all" and not data.empty:
        data = data[data["status"] == status_filter]

    federal_executive_mask = (data["level"] == "federal") & (data["branch"] == "executive") if not data.empty else None
    if federal_executive_mask is not None and federal_executive_mask.any():
        st.subheader("è¯é‚¦å®˜å“¡éšŽå±¤ç€è¦½" if lang == "zh-TW" else "Federal executive hierarchy")
        federal_exec = data[federal_executive_mask].copy()
        departments = sorted([item for item in federal_exec["department"].dropna().unique().tolist() if item], key=_executive_department_sort_key)
        if departments:
            selected_department = st.selectbox("éƒ¨é–€" if lang == "zh-TW" else "Department", departments, key="officials-department")
            federal_exec = federal_exec[federal_exec["department"] == selected_department]

            subdepartments = sorted([item for item in federal_exec["subdepartment"].dropna().unique().tolist() if item])
            if subdepartments:
                selected_subdepartment = st.selectbox("æ¬¡éƒ¨é–€" if lang == "zh-TW" else "Subdepartment", ["å…¨éƒ¨", *subdepartments], key="officials-subdepartment")
                if selected_subdepartment != "å…¨éƒ¨":
                    federal_exec = federal_exec[federal_exec["subdepartment"] == selected_subdepartment]

            units = sorted([item for item in federal_exec["unit"].dropna().unique().tolist() if item])
            if units:
                selected_unit = st.selectbox("ä¸‹å±¬éƒ¨é–€" if lang == "zh-TW" else "Sub-unit", ["å…¨éƒ¨", *units], key="officials-unit")
                if selected_unit != "å…¨éƒ¨":
                    federal_exec = federal_exec[federal_exec["unit"] == selected_unit]

            federal_exec = federal_exec.sort_values(
                by=["office_display", "name"],
                key=lambda series: series.map(lambda value: _executive_role_rank(value)[0]) if series.name == "office_display" else series.str.lower(),
            )
            hierarchy_display = federal_exec[["name", "office_display", "status", "source_type"]].copy()
            st.dataframe(localize_dataframe(hierarchy_display, lang, value_columns=["status", "source_type"]), use_container_width=True)

    if not data.empty:
        display = data.drop(
            columns=[
                "person_id",
                "social_profiles",
                "full_name",
                "given_name",
                "family_name",
                "source_url",
                "raw_payload",
                "appointment_raw_payload",
                "office",
            ]
        ).copy()
        st.dataframe(localize_dataframe(display, lang, value_columns=["level", "status", "source_type"]), use_container_width=True)

        person_choices = {
            f"{row['name']} ({row['office_display']})": int(row["person_id"])
            for _, row in data.iterrows()
        }
        selected_label = st.selectbox(labels["select_person"], list(person_choices.keys()), key="officials_person_preview")
        selected_person = data[data["person_id"] == person_choices[selected_label]].iloc[0]
        social_profiles = selected_person["social_profiles"] or {}
        wikipedia_url = resolve_wikipedia_url(selected_person["source_url"], selected_person["raw_payload"])
        wikipedia_search_url = build_wikipedia_search_url(selected_person["name"], selected_person["office"])
        st.markdown(f"[Google æœå°‹å®˜æ–¹è³‡æ–™]({build_google_official_search_url(selected_person['name'], selected_person['office'])})")
        st.markdown(f"[Google æœå°‹å®˜æ–¹ç°¡æ­·]({build_google_official_bio_search_url(selected_person['name'], selected_person['office'])})")
        official_search_urls = (selected_person["raw_payload"] or {}).get("official_search_urls", {})
        if official_search_urls.get("whitehouse_search"):
            st.markdown(f"[Google æœå°‹ç™½å®®è³‡æ–™]({official_search_urls['whitehouse_search']})")
        if official_search_urls.get("department_search"):
            st.markdown(f"[Google æœå°‹éƒ¨æœƒè³‡æ–™]({official_search_urls['department_search']})")
        if wikipedia_url:
            st.markdown(f"[Wikipedia é é¢]({wikipedia_url})")
        else:
            st.markdown(f"[Wikipedia æœå°‹]({wikipedia_search_url})")
        x_links = (selected_person["raw_payload"] or {}).get("x_candidate_links", {})
        x_search_url = x_links.get("google_x_search") or build_x_search_url(selected_person["name"], selected_person["office"])
        x_search_label = "X æœå°‹å€™é¸å¸³è™Ÿ" if lang == "zh-TW" else "Search X candidates"
        st.markdown(f"[{x_search_label}]({x_search_url})")
        if social_profiles:
            st.caption(labels["social_profiles"])
            render_social_links(social_profiles, key_prefix=f"officials-social-{selected_person['person_id']}")
            with st.expander(labels["social_profiles"]):
                for platform, url in social_profiles.items():
                    st.markdown(f"- [{social_display_name(platform)}]({url})")
        return

    _render_google_sheet_fallback(lang, labels)


def _render_google_sheet_fallback(lang: str, labels: dict[str, str]) -> bool:
    people = GoogleSheetReadService().list_people()
    if not people:
        st.info(labels["person_not_found"])
        return False

    st.info(
        "Google Sheet fallback mode is active. The cloud app is using exported people data."
        if lang != "zh-TW"
        else "目前使用 Google Sheet fallback 模式，雲端版先讀取已匯出的 People 資料。"
    )
    query = st.text_input(labels["search_name"], key="sheet-officials-search")
    status_filter = st.selectbox(labels["status_filter"], ["all", "current", "former", "unknown"], key="sheet-officials-status")
    rows = people
    if query:
        rows = [item for item in rows if query.lower() in str(item.get("display_name_en") or item.get("full_name") or "").lower()]
    if status_filter != "all":
        rows = [item for item in rows if item.get("status") == status_filter]
    if not rows:
        st.info(labels["person_not_found"])
        return True

    display_rows = [
        {
            "name": item.get("display_name_en") or item.get("full_name"),
            "office_display": item.get("office_title"),
            "department": item.get("department"),
            "jurisdiction": item.get("jurisdiction"),
            "status": item.get("status"),
            "level": item.get("level"),
        }
        for item in rows
    ]
    st.dataframe(localize_dataframe(pd.DataFrame(display_rows), lang, value_columns=["status", "level"]), use_container_width=True)

    person_choices = {
        f"{item.get('display_name_en') or item.get('full_name')} ({item.get('office_title') or labels['unknown']})": item
        for item in rows
    }
    selected_label = st.selectbox(labels["select_person"], list(person_choices.keys()), key="sheet-officials-person-preview")
    selected_person = person_choices[selected_label]
    st.markdown(f"[{labels['person_detail']}]({person_detail_href(int(selected_person['person_id']))})")
    if selected_person.get("official_page"):
        st.markdown(f"[Official page]({selected_person['official_page']})")
    if selected_person.get("wikipedia_page"):
        st.markdown(f"[Wikipedia]({selected_person['wikipedia_page']})")
    social_profiles = {}
    if selected_person.get("x_accounts_list"):
        social_profiles["x"] = selected_person["x_accounts_list"][0]
    if selected_person.get("facebook_accounts_list"):
        social_profiles["facebook"] = selected_person["facebook_accounts_list"][0]
    if selected_person.get("instagram_accounts_list"):
        social_profiles["instagram"] = selected_person["instagram_accounts_list"][0]
    if social_profiles:
        st.caption(labels["social_profiles"])
        render_social_links(social_profiles, key_prefix=f"sheet-officials-social-{selected_person['person_id']}")
    return True
