from __future__ import annotations

import re
import pandas as pd
import streamlit as st
from sqlalchemy import desc, func, select

from tracker.config import get_settings, use_google_sheet_primary_mode
from tracker.db import session_scope
from tracker.models import Alias, Appointment, Jurisdiction, Legislation, LegislationSponsor, Office, Person, Statement, SyncRun, Tracker
from tracker.services.ai_assist_service import AIAssistService
from tracker.services.google_sheet_read_service import GoogleSheetReadService
from tracker.services.manual_statement_ingest_service import ManualStatementIngestService
from tracker.services.officials_service import OfficialsService
from tracker.services.statements_service import StatementsService
from tracker.services.x_candidate_confirmation_service import XCandidateConfirmationService
from tracker.ui.badges import render_source_badges
from tracker.ui.display import localize_dataframe, localize_value, style_source_columns
from tracker.ui.navigation import person_detail_href, render_person_links
from tracker.ui.social_links import render_social_links
from tracker.ui.source_labels import source_label, statement_source_label
from tracker.utils.congress import build_congress_member_search_url, extract_legislator_metadata
from tracker.utils.names import display_person_name
from tracker.utils.official_search import (
    build_google_official_bio_search_url,
    build_google_official_search_url,
    build_x_search_url,
)
from tracker.utils.source_types import is_government_url, source_bucket_label
from tracker.utils.wikipedia_links import build_wikipedia_search_url, resolve_wikipedia_url


PERSON_CATEGORIES = {
    "federal_executive": {"label_zh": "聯邦政府部門官員", "label_en": "Federal executive officials", "level": "federal", "branch": "executive", "chamber": None},
    "federal_military": {"label_zh": "軍職人員", "label_en": "Military personnel", "level": "federal", "branch": "executive", "chamber": None},
    "federal_senate": {"label_zh": "聯邦參議員", "label_en": "U.S. Senators", "level": "federal", "branch": "legislative", "chamber": "senate"},
    "federal_house": {"label_zh": "聯邦眾議員", "label_en": "U.S. Representatives", "level": "federal", "branch": "legislative", "chamber": "house"},
    "state_executive": {"label_zh": "州政府官員", "label_en": "State executive officials", "level": "state", "branch": "executive", "chamber": None},
    "state_senate": {"label_zh": "州參議員", "label_en": "State senators", "level": "state", "branch": "legislative", "chamber": "senate"},
    "state_house": {"label_zh": "州眾議員", "label_en": "State representatives", "level": "state", "branch": "legislative", "chamber": "house"},
    "all": {"label_zh": "全部人物", "label_en": "All people", "level": None, "branch": None, "chamber": None},
}

CABINET_DEPARTMENT_ORDER = [
    "White House",
    "Department of State",
    "Department of the Treasury",
    "Department of Defense",
    "Department of Justice",
    "Department of the Interior",
    "Department of Agriculture",
    "Department of Commerce",
    "Department of Labor",
    "Department of Health and Human Services",
    "Department of Housing and Urban Development",
    "Department of Transportation",
    "Department of Energy",
    "Department of Education",
    "Department of Veterans Affairs",
    "Department of Homeland Security",
]

CABINET_DEPARTMENT_RANK = {name: index for index, name in enumerate(CABINET_DEPARTMENT_ORDER)}

DEPARTMENT_LABELS_ZH = {
    "White House": "白宮",
    "Department of State": "國務院",
    "Department of the Treasury": "財政部",
    "Department of Defense": "國防部",
    "Department of Justice": "司法部",
    "Department of the Interior": "內政部",
    "Department of Agriculture": "農業部",
    "Department of Commerce": "商務部",
    "Department of Labor": "勞工部",
    "Department of Health and Human Services": "衛生與公共服務部",
    "Department of Housing and Urban Development": "住房與城市發展部",
    "Department of Transportation": "運輸部",
    "Department of Energy": "能源部",
    "Department of Education": "教育部",
    "Department of Veterans Affairs": "退伍軍人事務部",
    "Department of Homeland Security": "國土安全部",
    "Office of the Director of National Intelligence": "國家情報總監辦公室",
    "Office of Management and Budget": "白宮管理及預算局",
    "Office of the United States Trade Representative": "美國貿易代表署",
    "United States Mission to the United Nations": "美國駐聯合國代表團",
    "Environmental Protection Agency": "環境保護署",
    "Small Business Administration": "小企業署",
    "Central Intelligence Agency": "中央情報局",
    "Council of Economic Advisers": "白宮經濟顧問委員會",
    "Department of Defense Leadership": "國防部高階領導",
    "Joint Chiefs of Staff": "參謀首長聯席會議",
    "U.S. Army Leadership": "美國陸軍高階領導",
    "U.S. Navy Leadership": "美國海軍高階領導",
    "U.S. Marine Corps Leadership": "美國海軍陸戰隊高階領導",
    "U.S. Air Force Biographies": "美國空軍高階領導",
    "U.S. Space Force Leadership": "美國太空軍高階領導",
    "National Guard Bureau Leadership": "國民兵局高階領導",
    "U.S. Indo-Pacific Command": "美軍印太司令部",
    "U.S. Central Command": "美軍中央司令部",
    "U.S. European Command": "美軍歐洲司令部",
    "U.S. Northern Command": "美軍北方司令部",
    "U.S. Southern Command": "美軍南方司令部",
    "U.S. Africa Command": "美軍非洲司令部",
    "U.S. Strategic Command": "美軍戰略司令部",
    "U.S. Transportation Command": "美軍運輸司令部",
    "U.S. Special Operations Command": "美軍特種作戰司令部",
    "U.S. Cyber Command": "美軍網路司令部",
    "U.S. Space Command": "美軍太空司令部",
    "Other": "其他",
}

POSITION_LABELS_ZH = {
    "President of the United States": "美國總統",
    "Vice President of the United States": "美國副總統",
    "Chief of Staff": "幕僚長",
    "White House Chief of Staff": "白宮幕僚長",
    "National Security Adviser": "國家安全顧問",
    "National Security Advisor": "國家安全顧問",
    "Deputy National Security Adviser": "副國家安全顧問",
    "Deputy National Security Advisor": "副國家安全顧問",
    "Executive Secretary": "執行秘書",
    "Governor": "州長",
    "Lieutenant Governor": "副州長",
    "Secretary of State": "州務卿",
    "Attorney General": "州檢察長",
    "Treasurer": "州財務長",
    "Comptroller": "主計長",
    "Auditor": "審計長",
}

WHITE_HOUSE_SUBDEPARTMENT_LABELS_ZH = {
    "National Security Council": "國家安全會議",
    "White House Office": "白宮辦公室",
    "Homeland Security Council": "國土安全會議",
}

WHITE_HOUSE_UNIT_LABELS_ZH = {
    "Strategic Communications": "戰略溝通",
    "Cyber": "網路安全",
    "Asia": "亞洲事務",
    "European Affairs": "歐洲事務",
    "European and Russian Affairs": "歐洲與俄羅斯事務",
    "Middle East and North Africa": "中東與北非事務",
    "Middle East and Africa": "中東與非洲事務",
    "South and Central Asian Affairs": "南亞與中亞事務",
    "Western Hemisphere": "西半球事務",
    "Intelligence": "情報事務",
    "Defense": "國防事務",
}

MILITARY_SUBDEPARTMENT_LABELS_ZH = {
    "Joint Chiefs of Staff": "參謀首長聯席會議",
    "Combatant Commands": "聯合作戰司令部",
}

MILITARY_UNIT_LABELS_ZH = {
    "Joint Staff": "參謀本部",
    "Army": "陸軍",
    "Navy": "海軍",
    "Marine Corps": "海軍陸戰隊",
    "Air Force": "空軍",
    "Space Force": "太空軍",
    "National Guard": "國民兵",
    "U.S. Africa Command": "美軍非洲司令部",
    "U.S. Central Command": "美軍中央司令部",
    "U.S. Cyber Command": "美軍網路司令部",
    "U.S. European Command": "美軍歐洲司令部",
    "U.S. Indo-Pacific Command": "美軍印太司令部",
    "U.S. Northern Command": "美軍北方司令部",
    "U.S. Southern Command": "美軍南方司令部",
    "U.S. Space Command": "美軍太空司令部",
    "U.S. Special Operations Command": "美軍特種作戰司令部",
    "U.S. Strategic Command": "美軍戰略司令部",
    "U.S. Transportation Command": "美軍運輸司令部",
}

COMBATANT_COMMAND_UNIT_MAP = {
    "africom": "U.S. Africa Command",
    "africa command": "U.S. Africa Command",
    "centcom": "U.S. Central Command",
    "central command": "U.S. Central Command",
    "cyber command": "U.S. Cyber Command",
    "eucom": "U.S. European Command",
    "european command": "U.S. European Command",
    "indopacom": "U.S. Indo-Pacific Command",
    "indo-pacific command": "U.S. Indo-Pacific Command",
    "northern command": "U.S. Northern Command",
    "northcom": "U.S. Northern Command",
    "southern command": "U.S. Southern Command",
    "southcom": "U.S. Southern Command",
    "space command": "U.S. Space Command",
    "spaccom": "U.S. Space Command",
    "special operations command": "U.S. Special Operations Command",
    "socom": "U.S. Special Operations Command",
    "strategic command": "U.S. Strategic Command",
    "stratcom": "U.S. Strategic Command",
    "transportation command": "U.S. Transportation Command",
    "transcom": "U.S. Transportation Command",
}

MILITARY_DEPARTMENT_ORDER = [
    "Department of Defense",
    "Department of Defense Leadership",
    "Joint Chiefs of Staff",
    "U.S. Army Leadership",
    "U.S. Navy Leadership",
    "U.S. Marine Corps Leadership",
    "U.S. Air Force Biographies",
    "U.S. Space Force Leadership",
    "National Guard Bureau Leadership",
    "U.S. Indo-Pacific Command",
    "U.S. Central Command",
    "U.S. European Command",
    "U.S. Northern Command",
    "U.S. Southern Command",
    "U.S. Africa Command",
    "U.S. Strategic Command",
    "U.S. Transportation Command",
    "U.S. Special Operations Command",
    "U.S. Cyber Command",
    "U.S. Space Command",
]
MILITARY_DEPARTMENT_RANK = {name: index for index, name in enumerate(MILITARY_DEPARTMENT_ORDER)}

MILITARY_SUBDEPARTMENT_ORDER = [
    "Joint Chiefs of Staff",
    "Combatant Commands",
]
MILITARY_SUBDEPARTMENT_RANK = {name: index for index, name in enumerate(MILITARY_SUBDEPARTMENT_ORDER)}

MILITARY_UNIT_ORDER = [
    "Joint Staff",
    "Army",
    "Navy",
    "Marine Corps",
    "Air Force",
    "Space Force",
    "National Guard",
    "U.S. Indo-Pacific Command",
    "U.S. Central Command",
    "U.S. European Command",
    "U.S. Northern Command",
    "U.S. Southern Command",
    "U.S. Africa Command",
    "U.S. Strategic Command",
    "U.S. Transportation Command",
    "U.S. Special Operations Command",
    "U.S. Cyber Command",
    "U.S. Space Command",
]
MILITARY_UNIT_RANK = {name: index for index, name in enumerate(MILITARY_UNIT_ORDER)}


def _category_label(category: dict, lang: str) -> str:
    return category["label_zh"] if lang == "zh-TW" else category["label_en"]


def _department_label(department_name: str | None, lang: str) -> str:
    label = (department_name or "").strip()
    if not label:
        return ""
    if lang != "zh-TW":
        return label
    return DEPARTMENT_LABELS_ZH.get(label, label)


def _bilingual_text(english: str | None, chinese: str | None) -> str:
    en = str(english or "").strip()
    zh = str(chinese or "").strip()
    if en and zh:
        if en == zh:
            return en
        return f"{zh} / {en}"
    return zh or en


def _position_label_zh(position_name: str | None) -> str:
    title = str(position_name or "").strip()
    if not title:
        return ""
    if title in POSITION_LABELS_ZH:
        return POSITION_LABELS_ZH[title]

    lower = title.lower()
    if lower.startswith("secretary of "):
        return f"{title.replace('Secretary of ', '', 1)}部長"
    if lower.startswith("deputy secretary of "):
        return f"{title.replace('Deputy Secretary of ', '', 1)}副部長"
    if lower.startswith("under secretary for "):
        return f"{title.replace('Under Secretary for ', '', 1)}次長"
    if lower.startswith("assistant secretary for "):
        return f"{title.replace('Assistant Secretary for ', '', 1)}助理部長"
    return ""


def _subdepartment_label(subdepartment_name: str | None, lang: str, department_name: str | None = None) -> str:
    label = (subdepartment_name or "").strip()
    if not label:
        return ""
    if lang != "zh-TW":
        return label
    if (department_name or "").strip() == "White House":
        return WHITE_HOUSE_SUBDEPARTMENT_LABELS_ZH.get(label, label)
    if (department_name or "").strip() == "Department of Defense":
        return MILITARY_SUBDEPARTMENT_LABELS_ZH.get(label, label)
    if label in MILITARY_SUBDEPARTMENT_LABELS_ZH:
        return MILITARY_SUBDEPARTMENT_LABELS_ZH[label]
    return WHITE_HOUSE_SUBDEPARTMENT_LABELS_ZH.get(label, label)


def _unit_label(unit_name: str | None, lang: str, department_name: str | None = None) -> str:
    label = (unit_name or "").strip()
    if not label:
        return ""
    if lang != "zh-TW":
        return label
    if (department_name or "").strip() == "White House":
        return WHITE_HOUSE_UNIT_LABELS_ZH.get(label, label)
    if (department_name or "").strip() == "Department of Defense":
        return MILITARY_UNIT_LABELS_ZH.get(label, label)
    if label in MILITARY_UNIT_LABELS_ZH:
        return MILITARY_UNIT_LABELS_ZH[label]
    return WHITE_HOUSE_UNIT_LABELS_ZH.get(label, label)


def _clean_background_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\[\s*\d+\s*\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*", ", ", text)
    return text.strip(" ,;")


def _format_statement_rows(statements: list[Statement], lang: str, source_counts: dict[int, int] | None = None) -> pd.DataFrame:
    source_counts = source_counts or {}
    return pd.DataFrame(
        [
            {
                "published_at": item.date_published or item.date_collected,
                "title": item.title,
                "source_type": statement_source_label(item, lang, item.event_source_preference or item.source_type),
                "source_count": source_counts.get(item.id, 0),
                "review_status": item.review_status,
                "source_url": item.source_url,
            }
            for item in statements
        ]
    )


def _get_base_people_query(category_key: str):
    category = PERSON_CATEGORIES[category_key]
    stmt = (
        select(
            Person.id,
            Person.full_name,
            Person.given_name,
            Person.family_name,
            Office.office_name,
            Jurisdiction.name,
            Appointment.raw_payload,
            Appointment.district,
        )
        .join(Appointment, Appointment.person_id == Person.id)
        .join(Office, Office.id == Appointment.office_id)
        .outerjoin(Jurisdiction, Jurisdiction.id == Office.jurisdiction_id)
        .order_by(Person.full_name.asc())
        .distinct()
    )
    if category["level"]:
        stmt = stmt.where(Office.level == category["level"])
    if category["branch"]:
        stmt = stmt.where(Office.branch == category["branch"])
    if category["chamber"]:
        stmt = stmt.where(Office.chamber == category["chamber"])
    return stmt


def _get_people_for_category(
    session,
    category_key: str,
    state_filter: str | None = None,
    department_filter: str | None = None,
    subdepartment_filter: str | None = None,
    unit_filter: str | None = None,
    status_filter: str | None = None,
    minister_only: bool = False,
    include_military_roles: bool = False,
) -> list[tuple[int, str, str | None, str | None, str, str | None, dict | None, str | None]]:
    stmt = _get_base_people_query(category_key)
    if state_filter:
        stmt = stmt.where(Jurisdiction.name == state_filter)
    if status_filter:
        stmt = stmt.where(Appointment.status == status_filter)
    rows = session.execute(stmt).all()
    if category_key == "federal_military":
        rows = [row for row in rows if _is_military_role(row[4], row[6])]
    if department_filter and category_key == "federal_executive":
        rows = [row for row in rows if _executive_hierarchy(row[4], row[6])[0] == department_filter]
    if department_filter and category_key == "federal_military":
        rows = [row for row in rows if _executive_hierarchy(row[4], row[6])[0] == department_filter]
    if subdepartment_filter and category_key == "federal_executive":
        rows = [row for row in rows if _executive_hierarchy(row[4], row[6])[1] == subdepartment_filter]
    if subdepartment_filter and category_key == "federal_military":
        rows = [row for row in rows if _executive_hierarchy(row[4], row[6])[1] == subdepartment_filter]
    if unit_filter and category_key == "federal_executive":
        rows = [row for row in rows if _executive_hierarchy(row[4], row[6])[2] == unit_filter]
    if unit_filter and category_key == "federal_military":
        rows = [row for row in rows if _executive_hierarchy(row[4], row[6])[2] == unit_filter]
    if minister_only and category_key == "federal_executive":
        rows = [
            row
            for row in rows
            if _executive_role_rank(_display_office_name(row[4], row[6]))[0] <= 4
            or (include_military_roles and _is_military_role(row[4], row[6]))
        ]
    return rows


def _categories_with_state_filter() -> set[str]:
    return {"federal_senate", "federal_house", "state_executive", "state_senate", "state_house"}


def _categories_with_department_filter() -> set[str]:
    return {"federal_executive", "federal_military"}


def _district_sort_key(value: str | None) -> tuple[int, object]:
    text = str(value or "").strip()
    if not text:
        return (2, "")
    match = re.search(r"district\s*([0-9A-Za-z-]+)", text, flags=re.I)
    normalized = match.group(1) if match else text
    if normalized.isdigit():
        return (0, int(normalized))
    return (1, normalized.lower())


def _render_member_roster(
    candidates: list[tuple[int, str, str | None, str | None, str, str | None, dict | None, str | None]],
    lang: str,
    selected_category: str,
) -> None:
    title = "成員名單" if lang == "zh-TW" else "Member roster"
    st.markdown(f"**{title}**")
    if not candidates:
        st.caption("目前無資料" if lang == "zh-TW" else "No data yet")
        return

    ordered = candidates
    if selected_category in {"state_senate", "state_house"}:
        ordered = sorted(
            candidates,
            key=lambda row: (_district_sort_key(row[7]), display_person_name(row[1], row[2], row[3]).lower()),
        )

    headers = ("姓名", "部門", "職位") if lang == "zh-TW" else ("Name", "Department", "Position")
    lines: list[str] = [f"| {headers[0]} | {headers[1]} | {headers[2]} |", "|---|---|---|"]

    def _clean_cell(text: str) -> str:
        return str(text or "").replace("|", "\\|").replace("\n", " ").strip()

    for row in ordered:
        person_id = int(row[0])
        name = display_person_name(row[1], row[2], row[3])
        office = _display_office_name(row[4], row[6])
        district = str(row[7] or "").strip()

        hierarchy = _executive_hierarchy(row[4], row[6])
        if selected_category in {"federal_executive", "federal_military"}:
            department_en = hierarchy[0] or (row[5] or "")
        else:
            department_en = str(row[5] or "")
        department = _bilingual_text(department_en, _department_label(department_en, "zh-TW"))

        office_zh = _position_label_zh(office)
        office_bilingual = _bilingual_text(office, office_zh)

        if selected_category in {"state_senate", "state_house"}:
            district_label = district or "Unspecified district"
            position = f"{office_bilingual} (第{district_label}選區 / District {district_label})"
        else:
            position = office_bilingual

        name_link = f"[{_clean_cell(name)}]({person_detail_href(person_id)})"
        lines.append(f"| {name_link} | {_clean_cell(department)} | {_clean_cell(position)} |")

    st.markdown("\n".join(lines))


def _get_state_options(session, category_key: str) -> list[str]:
    rows = session.execute(_get_base_people_query(category_key)).all()
    return sorted({row[5] for row in rows if row[5]})


def _get_department_options(session, category_key: str) -> list[str]:
    rows = session.execute(_get_base_people_query(category_key)).all()
    if category_key == "federal_military":
        rows = [row for row in rows if _is_military_role(row[4], row[6])]
    if category_key == "federal_executive":
        departments = {_executive_hierarchy(row[4], row[6])[0] for row in rows if row[4]}
        return sorted(departments, key=_executive_department_sort_key)
    if category_key == "federal_military":
        departments = {_executive_hierarchy(row[4], row[6])[0] for row in rows if row[4]}
        return sorted(departments, key=_military_department_sort_key)
    return sorted({row[4] for row in rows if row[4]})


def _get_subdepartment_options(session, category_key: str, department_filter: str) -> list[str]:
    rows = session.execute(_get_base_people_query(category_key)).all()
    if category_key == "federal_military":
        rows = [row for row in rows if _is_military_role(row[4], row[6])]
    options = {
        hierarchy[1]
        for row in rows
        for hierarchy in [_executive_hierarchy(row[4], row[6])]
        if hierarchy[0] == department_filter and hierarchy[1]
    }
    if category_key == "federal_military":
        return sorted(options, key=_military_subdepartment_sort_key)
    return sorted(options)


def _get_unit_options(session, category_key: str, department_filter: str, subdepartment_filter: str) -> list[str]:
    rows = session.execute(_get_base_people_query(category_key)).all()
    if category_key == "federal_military":
        rows = [row for row in rows if _is_military_role(row[4], row[6])]
    options = {
        hierarchy[2]
        for row in rows
        for hierarchy in [_executive_hierarchy(row[4], row[6])]
        if hierarchy[0] == department_filter and hierarchy[1] == subdepartment_filter and hierarchy[2]
    }
    if category_key == "federal_military":
        return sorted(options, key=_military_unit_sort_key)
    return sorted(options)


def _executive_department_name(office_name: str | None) -> str:
    if not office_name:
        return ""

    cleaned_office_name = " ".join(str(office_name).split()).strip()
    if cleaned_office_name.startswith(":"):
        cleaned_office_name = cleaned_office_name[1:].strip()
    if ":" in cleaned_office_name:
        prefix, suffix = cleaned_office_name.split(":", 1)
        prefix = prefix.strip()
        suffix = suffix.strip()
        if prefix:
            return prefix
        cleaned_office_name = suffix

    lower_name = cleaned_office_name.lower().strip()
    normalized_name = lower_name
    for prefix in ["acting ", "interim ", "former ", "principal ", "performing the delegable duties of the ", "performing the duties of the "]:
        if normalized_name.startswith(prefix):
            normalized_name = normalized_name[len(prefix) :].strip()

    if normalized_name.startswith("deputy secretary of the "):
        suffix = cleaned_office_name[lower_name.index("deputy secretary of the ") + len("deputy secretary of the ") :].strip()
        return f"Department of the {suffix}" if suffix else cleaned_office_name
    if normalized_name.startswith("deputy secretary of "):
        suffix = cleaned_office_name[lower_name.index("deputy secretary of ") + len("deputy secretary of ") :].strip()
        return f"Department of {suffix}" if suffix else cleaned_office_name
    if normalized_name.startswith("secretary of "):
        suffix = cleaned_office_name[lower_name.index("secretary of ") + len("secretary of ") :].strip()
        return f"Department of {suffix}" if suffix else cleaned_office_name
    if normalized_name.startswith("secretary of the "):
        suffix = cleaned_office_name[lower_name.index("secretary of the ") + len("secretary of the ") :].strip()
        return f"Department of the {suffix}" if suffix else cleaned_office_name

    department_map = {
        "attorney general": "Department of Justice",
        "acting attorney general": "Department of Justice",
        "deputy attorney general": "Department of Justice",
        "deputy secretary performing the delegable duties of the secretary": "Department of the Treasury",
        "director of national intelligence": "Office of the Director of National Intelligence",
        "principal deputy director of national intelligence": "Office of the Director of National Intelligence",
        "administrator of the environmental protection agency": "Environmental Protection Agency",
        "deputy administrator of the environmental protection agency": "Environmental Protection Agency",
        "administrator of the small business administration": "Small Business Administration",
        "deputy administrator of the small business administration": "Small Business Administration",
        "director of the office of management and budget": "Office of Management and Budget",
        "deputy director of the office of management and budget": "Office of Management and Budget",
        "united states trade representative": "Office of the United States Trade Representative",
        "deputy united states trade representative": "Office of the United States Trade Representative",
        "ambassador to the united nations": "United States Mission to the United Nations",
        "united states ambassador to the united nations": "United States Mission to the United Nations",
        "deputy ambassador to the united nations": "United States Mission to the United Nations",
        "chair of the council of economic advisers": "Council of Economic Advisers",
        "chief of staff": "White House",
        "white house chief of staff": "White House",
        "president of the united states": "White House",
        "vice president of the united states": "White House",
        "vice president": "White House",
        "assistant to the president for national security affairs": "White House",
        "national security advisor": "White House",
        "national security adviser": "White House",
        "director of the central intelligence agency": "Central Intelligence Agency",
        "deputy director of the central intelligence agency": "Central Intelligence Agency",
        "general counsel of the central intelligence agency": "Central Intelligence Agency",
        "inspector general of the central intelligence agency": "Central Intelligence Agency",
        "director of the office of science and technology policy": "Office of Science and Technology Policy",
        "director of the national counter intelligence and security center": "National Counterintelligence and Security Center",
        "inspector general of the intelligence community": "Office of the Director of National Intelligence",
        "general counsel of the office of the director of national intelligence": "Office of the Director of National Intelligence",
        "general counsel of veterans affairs": "Department of Veterans Affairs",
        "inspector general of veterans affairs": "Department of Veterans Affairs",
        "chief financial officer of veterans affairs": "Department of Veterans Affairs",
    }

    if normalized_name in department_map:
        return department_map[normalized_name]
    if lower_name in department_map:
        return department_map[lower_name]

    pattern_map = [
        (" of veterans affairs", "Department of Veterans Affairs"),
        (" of the environmental protection agency", "Environmental Protection Agency"),
        (" of the office of management and budget", "Office of Management and Budget"),
        (" of the office of the director of national intelligence", "Office of the Director of National Intelligence"),
        (" of the central intelligence agency", "Central Intelligence Agency"),
        (" of national intelligence", "Office of the Director of National Intelligence"),
    ]
    for pattern, department in pattern_map:
        if normalized_name.endswith(pattern):
            return department

    if "white house" in normalized_name or "executive office of the president" in normalized_name:
        return "White House"
    if "national security council" in normalized_name or "national security affairs" in normalized_name:
        return "White House"
    if any(
        keyword in normalized_name
        for keyword in [
            "joint chiefs",
            "chairman of the joint chiefs",
            "vice chairman of the joint chiefs",
            "chief of naval operations",
            "chief of staff of the army",
            "chief of staff of the air force",
            "commandant of the marine corps",
            "chief of space operations",
            "chief of the national guard bureau",
            "combatant command",
            "commander, u.s.",
            "commander, united states",
            "commander of u.s.",
            "commander of united states",
            "africom",
            "centcom",
            "eucom",
            "indopacom",
            "northcom",
            "southcom",
            "socom",
            "stratcom",
            "transcom",
        ]
    ):
        return "Department of Defense"
    if "council of economic advisers" in normalized_name:
        return "Council of Economic Advisers"
    if "trade representative" in normalized_name:
        return "Office of the United States Trade Representative"
    if "small business administration" in normalized_name:
        return "Small Business Administration"
    if "environmental protection agency" in normalized_name:
        return "Environmental Protection Agency"
    if "central intelligence agency" in normalized_name:
        return "Central Intelligence Agency"
    if "office of science and technology policy" in normalized_name:
        return "Office of Science and Technology Policy"
    if "national counter intelligence and security center" in normalized_name or "national counterintelligence and security center" in normalized_name:
        return "National Counterintelligence and Security Center"
    if "united nations" in normalized_name:
        return "United States Mission to the United Nations"
    if normalized_name == "secretary of war":
        return "Department of War"

    phrase_prefixes = [
        "administrator of the ",
        "administrator of ",
        "deputy administrator of the ",
        "deputy administrator of ",
        "director of the ",
        "director of ",
        "deputy director of the ",
        "deputy director of ",
        "chairman of the ",
        "chairman of ",
        "chairwoman of the ",
        "chairwoman of ",
        "chair of the ",
        "chair of ",
        "commissioner of the ",
        "commissioner of ",
        "member of the ",
        "member of ",
        "general counsel of the ",
        "general counsel of ",
        "chief financial officer of the ",
        "chief financial officer of ",
        "chief counsel for ",
        "chief counsel of ",
        "special counsel of the ",
        "special counsel of ",
        "archivist of the ",
        "archivist of ",
        "chief executive officer of the ",
        "ceo of the ",
        "associate administrator for ",
        "senior advisor to the ",
        "vice chair of the ",
        "vice chairman of the ",
        "vice chairman and vice president of the ",
        "chairman and president of the ",
        "board of directors of ",
        "board of governors of ",
        "commissioners of the ",
        "commissioners of ",
    ]
    for prefix in phrase_prefixes:
        if normalized_name.startswith(prefix):
            organization = cleaned_office_name[len(prefix) :].strip()
            if organization:
                if organization.lower() in {
                    "agriculture",
                    "commerce",
                    "defense",
                    "education",
                    "energy",
                    "health and human services",
                    "homeland security",
                    "housing and urban development",
                    "labor",
                    "state",
                    "transportation",
                    "veterans affairs",
                }:
                    return f"Department of {organization}"
                if organization.lower() in {"the interior", "the treasury"}:
                    return f"Department of {organization}"
                return organization

    return cleaned_office_name


def _executive_hierarchy(office_name: str | None, appointment_payload: dict | None) -> tuple[str, str | None, str | None]:
    payload = appointment_payload or {}
    payload_office_title = payload.get("office_title") if isinstance(payload, dict) else None
    department_name = payload.get("department_name") if isinstance(payload, dict) else None
    top_department_name = payload.get("top_department_name") if isinstance(payload, dict) else None
    top_department = (
        top_department_name
        or department_name
        or _executive_department_name(payload_office_title)
        or _executive_department_name(office_name)
        or "Other"
    )
    if top_department in {"Other", ""} and payload_office_title:
        top_department = _executive_department_name(payload_office_title) or top_department
    if top_department in {"Other", ""} and office_name:
        top_department = _executive_department_name(office_name) or top_department
    if top_department in {"", ":"}:
        top_department = "Other"

    subdepartment = payload.get("subdepartment_name")
    unit = payload.get("unit_name")
    title_for_grouping = str(payload_office_title or office_name or "").lower()
    if top_department == "White House" and not subdepartment:
        if (
            "national security council" in title_for_grouping
            or "national security adviser" in title_for_grouping
            or "national security advisor" in title_for_grouping
            or "national security affairs" in title_for_grouping
        ):
            subdepartment = "National Security Council"
        elif "chief of staff" in title_for_grouping or "white house office" in title_for_grouping:
            subdepartment = "White House Office"
    if top_department == "Department of Defense":
        if not subdepartment:
            if any(
                keyword in title_for_grouping
                for keyword in [
                    "joint chiefs",
                    "chairman of the joint chiefs",
                    "vice chairman of the joint chiefs",
                    "chief of naval operations",
                    "chief of staff of the army",
                    "chief of staff of the air force",
                    "commandant of the marine corps",
                    "chief of space operations",
                    "chief of the national guard bureau",
                ]
            ):
                subdepartment = "Joint Chiefs of Staff"
            elif "commander" in title_for_grouping and any(
                key in title_for_grouping for key in COMBATANT_COMMAND_UNIT_MAP
            ):
                subdepartment = "Combatant Commands"
        if subdepartment == "Joint Chiefs of Staff" and not unit:
            if "chairman of the joint chiefs" in title_for_grouping or "vice chairman of the joint chiefs" in title_for_grouping:
                unit = "Joint Staff"
            elif "chief of staff of the army" in title_for_grouping:
                unit = "Army"
            elif "chief of naval operations" in title_for_grouping:
                unit = "Navy"
            elif "commandant of the marine corps" in title_for_grouping:
                unit = "Marine Corps"
            elif "chief of staff of the air force" in title_for_grouping:
                unit = "Air Force"
            elif "chief of space operations" in title_for_grouping:
                unit = "Space Force"
            elif "chief of the national guard bureau" in title_for_grouping:
                unit = "National Guard"
        if subdepartment == "Combatant Commands" and not unit:
            for key, mapped_unit in COMBATANT_COMMAND_UNIT_MAP.items():
                if key in title_for_grouping:
                    unit = mapped_unit
                    break
    return top_department, subdepartment, unit


def _is_military_role(office_name: str | None, appointment_payload: dict | None) -> bool:
    title = _display_office_name(office_name, appointment_payload).lower()
    civilian_indicators = [
        "secretary of defense",
        "deputy secretary of defense",
        "under secretary",
        "assistant secretary",
        "general counsel",
        "chief financial officer",
        "chief data and artificial intelligence officer",
        "comptroller",
    ]
    if any(keyword in title for keyword in civilian_indicators):
        return False
    return any(
        keyword in title
        for keyword in [
            "joint chiefs",
            "chief of naval operations",
            "chief of staff of the army",
            "chief of staff of the air force",
            "commandant of the marine corps",
            "chief of space operations",
            "chief of the national guard bureau",
            "combatant command",
            "commander, u.s.",
            "commander, united states",
            "commander of u.s.",
            "commander of united states",
            "africom",
            "centcom",
            "eucom",
            "indopacom",
            "northcom",
            "southcom",
            "socom",
            "stratcom",
            "transcom",
        ]
    )


def _executive_role_rank(office_name: str | None) -> tuple[int, str]:
    title = (office_name or "").lower()
    if ":" in title:
        title = title.split(":", 1)[1].strip()
    if title.startswith("president of the united states"):
        return (0, title)
    if title.startswith("vice president of the united states"):
        return (1, title)
    if "chief of staff" in title:
        return (2, title)
    if title.startswith("secretary of") or title.startswith("secretary of the"):
        return (3, title)
    if title.startswith("attorney general"):
        return (3, title)
    if "director of national intelligence" in title or "trade representative" in title:
        return (4, title)
    if title.startswith("administrator"):
        return (5, title)
    if title.startswith("deputy secretary"):
        return (6, title)
    if title.startswith("under secretary"):
        return (7, title)
    if title.startswith("principal deputy assistant secretary"):
        return (8, title)
    if title.startswith("deputy assistant secretary"):
        return (9, title)
    if title.startswith("assistant secretary"):
        return (10, title)
    if "general counsel" in title:
        return (11, title)
    if title.startswith("director"):
        return (12, title)
    return (99, title)


def _military_role_rank(office_name: str | None, appointment_payload: dict | None = None) -> tuple[int, str]:
    title = _display_office_name(office_name, appointment_payload).lower()
    if "chairman, joint chiefs of staff" in title or "chairman of the joint chiefs" in title:
        return (0, title)
    if "vice chairman, joint chiefs of staff" in title or "vice chairman of the joint chiefs" in title:
        return (1, title)
    if any(
        phrase in title
        for phrase in [
            "chief of staff of the army",
            "chief of naval operations",
            "chief of staff of the air force",
            "commandant of the marine corps",
            "chief of space operations",
            "chief of the national guard bureau",
        ]
    ):
        return (2, title)
    if "commander" in title and "deputy commander" not in title and "vice commander" not in title:
        return (3, title)
    if "deputy commander" in title or "vice commander" in title:
        return (4, title)
    if "chief of staff" in title:
        return (5, title)
    if "command senior enlisted leader" in title or "senior enlisted advisor" in title or "sergeant major" in title:
        return (6, title)
    return (99, title)


def _executive_department_sort_key(department_name: str | None) -> tuple[int, str]:
    department = (department_name or "").strip()
    if not department:
        return (999, "")
    if department in CABINET_DEPARTMENT_RANK:
        return (CABINET_DEPARTMENT_RANK[department], department.lower())
    return (100 + len(CABINET_DEPARTMENT_RANK), department.lower())


def _military_department_sort_key(department_name: str | None) -> tuple[int, str]:
    department = (department_name or "").strip()
    if not department:
        return (999, "")
    if department in MILITARY_DEPARTMENT_RANK:
        return (MILITARY_DEPARTMENT_RANK[department], department.lower())
    return (200 + len(MILITARY_DEPARTMENT_RANK), department.lower())


def _military_subdepartment_sort_key(name: str | None) -> tuple[int, str]:
    subdepartment = (name or "").strip()
    if not subdepartment:
        return (999, "")
    if subdepartment in MILITARY_SUBDEPARTMENT_RANK:
        return (MILITARY_SUBDEPARTMENT_RANK[subdepartment], subdepartment.lower())
    return (200 + len(MILITARY_SUBDEPARTMENT_RANK), subdepartment.lower())


def _military_unit_sort_key(name: str | None) -> tuple[int, str]:
    unit = (name or "").strip()
    if not unit:
        return (999, "")
    if unit in MILITARY_UNIT_RANK:
        return (MILITARY_UNIT_RANK[unit], unit.lower())
    return (200 + len(MILITARY_UNIT_RANK), unit.lower())


def _display_office_name(office_name: str | None, appointment_payload: dict | None = None) -> str:
    payload = appointment_payload or {}
    payload_title = payload.get("office_title") if isinstance(payload, dict) else None
    clean_title = " ".join(str(payload_title).split()).strip() if payload_title else ""
    if clean_title:
        return clean_title
    clean_office = " ".join(str(office_name or "").split()).strip()
    if clean_office.startswith(":"):
        clean_office = clean_office[1:].strip()
    if ":" in clean_office:
        prefix, suffix = clean_office.split(":", 1)
        if suffix.strip():
            return suffix.strip()
        return prefix.strip()
    return clean_office


def _format_background_source(field_name: str, person_data: dict[str, object], labels: dict[str, str], lang: str) -> str | None:
    background_sources = person_data.get("background_sources") or {}
    if not isinstance(background_sources, dict):
        return None
    source_info = background_sources.get(field_name)
    if not isinstance(source_info, dict):
        return None
    source_type = source_bucket_label(source_info.get("source_type"), source_info.get("source_url"), lang)
    return f"{labels['field_source']}: {source_type}"


def _background_search_links(person_data: dict[str, object], current_appointment: str | None) -> list[tuple[str, str]]:
    raw_payload = person_data.get("raw_payload") or {}
    if not isinstance(raw_payload, dict):
        raw_payload = {}
    stored_links = raw_payload.get("background_search_urls") or {}
    if not isinstance(stored_links, dict):
        stored_links = {}

    wikipedia_url = resolve_wikipedia_url(person_data.get("source_url"), raw_payload)
    links: list[tuple[str, str]] = []
    if wikipedia_url:
        links.append(("wikipedia_page", wikipedia_url))
    elif stored_links.get("wikipedia_search"):
        links.append(("wikipedia_search", str(stored_links["wikipedia_search"])))
    else:
        links.append(("wikipedia_search", build_wikipedia_search_url(str(person_data.get("full_name") or ""), current_appointment)))

    links.append(
        (
            "google_official_search",
            str(
                stored_links.get("google_official_search")
                or build_google_official_search_url(str(person_data.get("full_name") or ""), current_appointment)
            ),
        )
    )
    links.append(
        (
            "google_official_bio_search",
            str(
                stored_links.get("google_official_bio_search")
                or build_google_official_bio_search_url(str(person_data.get("full_name") or ""), current_appointment)
            ),
        )
    )

    for key in ("official_page", "whitehouse_search", "department_search"):
        if stored_links.get(key):
            links.append((key, str(stored_links[key])))

    deduped: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for key, url in links:
        cleaned_url = (url or "").strip()
        if not cleaned_url or cleaned_url in seen_urls:
            continue
        seen_urls.add(cleaned_url)
        deduped.append((key, cleaned_url))
    return deduped


def _background_search_label(link_key: str, lang: str) -> str:
    labels_zh = {
        "wikipedia_page": "Wikipedia 頁面",
        "wikipedia_search": "Wikipedia 搜尋",
        "google_official_search": "Google 搜尋官方資料",
        "google_official_bio_search": "Google 搜尋官方簡歷",
        "official_page": "官方頁面",
        "whitehouse_search": "Google 搜尋白宮資料",
        "department_search": "Google 搜尋部會資料",
    }
    labels_en = {
        "wikipedia_page": "Wikipedia page",
        "wikipedia_search": "Wikipedia search",
        "google_official_search": "Search official sources",
        "google_official_bio_search": "Search official biography",
        "official_page": "Official page",
        "whitehouse_search": "Search White House sources",
        "department_search": "Search department sources",
    }
    mapping = labels_zh if lang == "zh-TW" else labels_en
    return mapping.get(link_key, link_key)


def _candidate_sections(raw_payload: dict[str, object]) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    x_links = raw_payload.get("x_candidate_links", {}) if isinstance(raw_payload, dict) else {}
    candidates = x_links.get("candidates", []) if isinstance(x_links, dict) else []
    if not isinstance(candidates, list):
        return [], [], []
    high_confidence = [item for item in candidates if item.get("status") == "high_confidence"]
    needs_review = [item for item in candidates if item.get("status") == "needs_review"]
    rejected = [item for item in candidates if item.get("status") == "rejected"]
    return high_confidence, needs_review, rejected


def _confirmed_x_profiles(raw_payload: dict[str, object]) -> list[dict[str, str]]:
    x_links = raw_payload.get("x_candidate_links", {}) if isinstance(raw_payload, dict) else {}
    confirmed_profiles = x_links.get("confirmed_profiles", []) if isinstance(x_links, dict) else []
    if not isinstance(confirmed_profiles, list):
        return []
    return [item for item in confirmed_profiles if isinstance(item, dict) and item.get("profile_url")]


def _merged_confirmed_x_profiles(raw_payload: dict[str, object], social_profiles: dict[str, str]) -> list[dict[str, str]]:
    confirmed_profiles = list(_confirmed_x_profiles(raw_payload))
    existing_urls = {
        item.get("profile_url")
        for item in confirmed_profiles
        if isinstance(item, dict) and item.get("profile_url")
    }
    official_x_url = (social_profiles or {}).get("x")
    if official_x_url and official_x_url not in existing_urls:
        confirmed_profiles.insert(
            0,
            {
                "profile_url": official_x_url,
                "source_reason": "official_site_match",
            },
        )
    return confirmed_profiles


def _confirmed_x_source_label(source_reason: str | None, lang: str) -> str:
    reason = (source_reason or "").strip().lower()
    if "official" in reason:
        return "官方網站確認" if lang == "zh-TW" else "Official website confirmation"
    if "manual" in reason:
        return "人工確認" if lang == "zh-TW" else "Manual confirmation"
    if "candidate" in reason or "search" in reason:
        return "搜尋結果認證線索" if lang == "zh-TW" else "Search-result verification hint"
    return "人工確認" if lang == "zh-TW" else "Manual confirmation"


def _group_confirmed_x_profiles(
    confirmed_profiles: list[dict[str, str]],
    lang: str,
) -> list[tuple[str, list[dict[str, str]]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for confirmed_profile in confirmed_profiles:
        label = _confirmed_x_source_label(confirmed_profile.get("source_reason"), lang)
        grouped.setdefault(label, []).append(confirmed_profile)
    ordering = [
        "官方網站確認" if lang == "zh-TW" else "Official website confirmation",
        "搜尋結果認證線索" if lang == "zh-TW" else "Search-result verification hint",
        "人工確認" if lang == "zh-TW" else "Manual confirmation",
    ]
    return [(label, grouped[label]) for label in ordering if label in grouped]


def _query_person_id() -> int | None:
    raw_value = st.query_params.get("person_id")
    if raw_value is None:
        return None
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else None
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if text.isdigit():
        return int(text)
    return None


def _render_x_candidate_block(person_id: int, candidate: dict[str, str], lang: str, button_prefix: str) -> None:
    title = candidate.get("title") or candidate.get("handle") or candidate.get("profile_url") or ""
    profile_url = candidate.get("profile_url") or ""
    if profile_url:
        st.markdown(f"- [{title}]({profile_url})")
    else:
        st.markdown(f"- {title}")
    if candidate.get("snippet"):
        st.caption(candidate["snippet"])
    if candidate.get("verification_hint") == "true":
        st.caption("認證線索: 有" if lang == "zh-TW" else "Verification hint: yes")
    if candidate.get("reasons"):
        reason_label = "理由" if lang == "zh-TW" else "Reasons"
        st.caption(f"{reason_label}: {candidate['reasons']}")
    confirm_label = "加入已確認 X 帳號" if lang == "zh-TW" else "Add confirmed X account"
    if profile_url and st.button(confirm_label, key=f"{button_prefix}-{person_id}-{profile_url}"):
        with session_scope() as session:
            service = XCandidateConfirmationService(session)
            source_reason = (
                "x_candidate_search_verified"
                if candidate.get("verification_hint") == "true"
                else "x_candidate_manual_confirmed"
            )
            confirmed = service.confirm_candidate(person_id, profile_url, source_reason=source_reason)
        if confirmed:
            st.success("已加入已確認 X 帳號。" if lang == "zh-TW" else "Confirmed X account added.")
            st.rerun()
        st.error("無法更新 X 帳號。" if lang == "zh-TW" else "Unable to update X account.")


def _participant_entries(participants: list[object]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    for participant in participants:
        person = getattr(participant, "person", None)
        person_id = getattr(participant, "person_id", None)
        if not person or not person_id or person_id in seen_ids:
            continue
        entries.append(
            {
                "person_id": person_id,
                "display_name": display_person_name(person.full_name, person.given_name, person.family_name),
            }
        )
        seen_ids.add(person_id)
    return entries


def _render_statement_cards(
    items: list[Statement],
    source_counts_map: dict[int, int],
    sources_map: dict[int, list[object]],
    participants_map: dict[int, list[dict[str, object]]],
    lang: str,
    labels: dict[str, str],
) -> None:
    if not items:
        st.info(labels["no_recent_statements"])
        return
    for item in items:
        with st.container(border=True):
            st.markdown(f"**{item.title}**")
            source_count = source_counts_map.get(item.id, 0)
            source_type = statement_source_label(item, lang, str(localize_value(item.event_source_preference or item.source_type, lang)))
            review_status = localize_value(item.review_status, lang)
            st.caption(f"{item.date_published or item.date_collected} | {source_type} | {source_count} | {review_status}")
            participants = participants_map.get(item.id, [])
            if participants:
                participants_label = "參與人" if lang == "zh-TW" else "Participants"
                st.write(f"{participants_label}:")
                render_person_links(participants, lang, key_prefix=f"statement-{item.id}")
            if item.excerpt:
                st.write(item.excerpt[:500])
            representative_label = statement_source_label(item, lang, str(item.event_source_preference or item.source_type or labels["unknown"]))
            st.write(f"{labels['representative_source']}: {representative_label} | {item.source_url}")
            sources = sources_map.get(item.id, [])
            if sources:
                citation_label = "引述來源" if lang == "zh-TW" else "Quoted sources"
                top_sources = " | ".join(
                    f"[{source.source_title or source_label(source, lang, str(source.source_type or labels['unknown']))}]({source.source_url})"
                    for source in sources[:3]
                )
                st.markdown(f"{citation_label}: {top_sources}")
                with st.expander(labels["sources"]):
                    for source in sources:
                        display_label = source_label(source, lang, str(source.source_type or labels["unknown"]))
                        st.write(f"[{display_label}] {source.source_url}")


def _render_manual_event_ingest_form(person_id: int, lang: str, labels: dict[str, str]) -> None:
    st.markdown(f"**{labels['manual_event_ingest']}**")
    flash_key = f"manual-event-ingest-flash-{person_id}"
    flash = st.session_state.pop(flash_key, None)
    if isinstance(flash, dict):
        level = str(flash.get("level") or "")
        message = str(flash.get("message") or "")
        if message:
            if level == "success":
                st.success(message)
            elif level == "info":
                st.info(message)
            else:
                st.error(message)

    with st.form(key=f"manual-event-ingest-form-{person_id}", clear_on_submit=True):
        source_url = st.text_input(labels["manual_event_url"])
        submitted = st.form_submit_button(labels["manual_event_submit"])

    if not submitted:
        return
    if not source_url.strip():
        st.error(labels["manual_event_url_required"])
        return

    try:
        with st.spinner(labels["manual_event_ingesting"]):
            with session_scope() as session:
                ingest_service = ManualStatementIngestService(session)
                statement, created = ingest_service.ingest_from_url(person_id=person_id, source_url=source_url.strip())
        if created:
            st.session_state[flash_key] = {
                "level": "success",
                "message": labels["manual_event_created"].format(title=statement.title),
            }
        else:
            st.session_state[flash_key] = {
                "level": "info",
                "message": labels["manual_event_updated"].format(title=statement.title),
            }
        st.rerun()
    except Exception as exc:
        st.error(f"{labels['manual_event_failed']}: {exc}")


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["person_detail"])

    pending_person_id = _query_person_id()
    if pending_person_id:
        clear_label = "返回人物瀏覽" if lang == "zh-TW" else "Back to person browser"
        if st.button(clear_label, key="clear-pending-person"):
            if "person_id" in st.query_params:
                del st.query_params["person_id"]
            if st.query_params.get("page") == "person_detail":
                del st.query_params["page"]
            st.rerun()

    settings = get_settings()
    has_sheet_config = bool(
        settings.google_sheet_id
        and (settings.google_service_account_json or settings.google_service_account_file)
    )
    # DB-first: only use sheet fallback when explicitly configured as primary.
    prefer_sheet_person_view = use_google_sheet_primary_mode()

    if prefer_sheet_person_view:
        if _render_google_sheet_fallback_v2(lang, labels, pending_person_id):
            return
        if use_google_sheet_primary_mode():
            st.info(labels["no_people_loaded"])
            return

    with session_scope() as session:
        total_people = session.scalar(select(func.count()).select_from(Person)) or 0
    if total_people == 0 and _render_google_sheet_fallback_v2(lang, labels, pending_person_id):
        return

    selected_category = st.selectbox(
        labels["person_category"],
        list(PERSON_CATEGORIES.keys()),
        format_func=lambda key: _category_label(PERSON_CATEGORIES[key], lang),
    )

    with session_scope() as session:
        state_filter = None
        department_filter = None
        subdepartment_filter = None
        unit_filter = None
        minister_only = False
        include_military_roles = False
        person = None
        person_id = None

        if pending_person_id:
            person = session.get(Person, int(pending_person_id))
            if person:
                person_id = int(pending_person_id)
            else:
                pass

        if person is None and selected_category in _categories_with_department_filter():
            if selected_category == "federal_executive":
                role_scope_label = "職級" if lang == "zh-TW" else "Role scope"
                role_scope_options = (
                    ["部長級與軍職", "部長級", "全部"]
                    if lang == "zh-TW"
                    else ["Minister-level + Military", "Minister-level", "All"]
                )
                role_scope = st.selectbox(role_scope_label, role_scope_options, index=2)
                minister_only = role_scope in {"部長級與軍職", "部長級", "Minister-level + Military", "Minister-level"}
                include_military_roles = role_scope in {"部長級與軍職", "全部", "Minister-level + Military", "All"}
            department_options = _get_department_options(session, selected_category)
            if not department_options:
                st.info(labels["no_people_loaded"])
                return
            department_filter = st.selectbox(
                labels["department"],
                department_options,
                format_func=lambda item: _department_label(item, lang),
            )
            subdepartment_options = _get_subdepartment_options(session, selected_category, department_filter)
            if subdepartment_options:
                subdepartment_label = "次部門" if lang == "zh-TW" else "Subdepartment"
                sub_selection = st.selectbox(
                    subdepartment_label,
                    ["__all__", *subdepartment_options],
                    format_func=lambda item: ("全部" if item == "__all__" else _subdepartment_label(item, lang, department_filter)),
                )
                subdepartment_filter = None if sub_selection == "__all__" else sub_selection
                if subdepartment_filter:
                    unit_options = _get_unit_options(session, selected_category, department_filter, subdepartment_filter)
                    if unit_options:
                        unit_label = "下屬部門" if lang == "zh-TW" else "Sub-unit"
                        unit_selection = st.selectbox(
                            unit_label,
                            ["__all__", *unit_options],
                            format_func=lambda item: ("全部" if item == "__all__" else _unit_label(item, lang, department_filter)),
                        )
                        unit_filter = None if unit_selection == "__all__" else unit_selection

        if person is None and selected_category in _categories_with_state_filter():
            state_options = _get_state_options(session, selected_category)
            if not state_options:
                st.info(labels["no_people_loaded"])
                return
            state_selection = st.selectbox(labels["state"], [labels["all"], *state_options])
            state_filter = None if state_selection == labels["all"] else state_selection

        if person is None:
            status_options = ["all", "current", "former"]
            status_labels = {
                "all": labels["all"],
                "current": localize_value("current", lang),
                "former": localize_value("former", lang),
            }
            default_status_index = 0 if selected_category == "federal_military" else 1
            selected_status = st.selectbox(
                labels["status_filter"],
                status_options,
                format_func=lambda value: str(status_labels[value]),
                key=f"person-status-{selected_category}",
                index=default_status_index,
            )

            candidates = _get_people_for_category(
                session,
                selected_category,
                state_filter=state_filter,
                department_filter=department_filter,
                subdepartment_filter=subdepartment_filter,
                unit_filter=unit_filter,
                status_filter=(None if selected_status == "all" else selected_status),
                minister_only=minister_only,
                include_military_roles=include_military_roles,
            )
            if not candidates:
                st.info(labels["no_people_loaded"])
                return

            if selected_category == "federal_executive":
                candidates = sorted(
                    candidates,
                    key=lambda row: (
                        _executive_role_rank(_display_office_name(row[4], row[6])),
                        display_person_name(row[1], row[2], row[3]).lower(),
                    ),
                )
            elif selected_category == "federal_military":
                candidates = sorted(
                    candidates,
                    key=lambda row: (
                        _military_department_sort_key(_executive_hierarchy(row[4], row[6])[0]),
                        _military_subdepartment_sort_key(_executive_hierarchy(row[4], row[6])[1]),
                        _military_unit_sort_key(_executive_hierarchy(row[4], row[6])[2]),
                        _military_role_rank(row[4], row[6]),
                        display_person_name(row[1], row[2], row[3]).lower(),
                    ),
                )

            if selected_category in _categories_with_department_filter() or selected_category in {"state_executive", "state_senate", "state_house"}:
                _render_member_roster(candidates, lang=lang, selected_category=selected_category)

            person_options = {
                f"{display_person_name(row[1], row[2], row[3])} ({_display_office_name(row[4], row[6])})": row[0]
                for row in candidates
            }
            selected_person_label = st.selectbox(labels["select_person"], list(person_options.keys()))
            person_id = person_options[selected_person_label]
            person = session.get(Person, int(person_id))
            if not person:
                st.info(labels["person_not_found"])
                return

        person_data = {
            "full_name": display_person_name(person.full_name, person.given_name, person.family_name),
            "full_name_display": (person.raw_payload or {}).get("full_name_display"),
            "portrait_url": person.portrait_url,
            "portrait_source_url": person.portrait_source_url,
            "portrait_source_type": person.portrait_source_type,
            "profile_status": person.profile_status,
            "seed_source_type": person.seed_source_type,
            "source_type": person.source_type,
            "canonical_official_url": person.canonical_official_url,
            "source_url": person.source_url,
            "social_profiles": person.social_profiles or {},
            "date_of_birth": person.date_of_birth,
            "place_of_birth": person.place_of_birth,
            "ethnicity": person.ethnicity,
            "religion": person.religion,
            "education": person.education,
            "career_history": person.career_history,
            "bio": person.bio,
            "background_sources": (person.raw_payload or {}).get("background_sources", {}),
            "raw_payload": person.raw_payload or {},
        }

        statements_service = StatementsService(session)
        officials_service = OfficialsService(session)
        aliases = session.execute(select(Alias.alias).where(Alias.person_id == person.id, Alias.alias_type != "chinese_name")).scalars().all()
        chinese_aliases = officials_service.list_chinese_aliases(person.id)
        appointments = session.execute(
            select(Appointment.role_title, Appointment.party, Appointment.status, Appointment.start_date, Appointment.end_date, Appointment.district)
            .where(Appointment.person_id == person.id)
            .order_by(Appointment.start_date.desc())
        ).all()
        current_appointment_row = session.execute(
            select(Appointment.role_title, Appointment.party, Appointment.district, Appointment.raw_payload, Office.chamber)
            .join(Office, Office.id == Appointment.office_id)
            .where(Appointment.person_id == person.id, Appointment.status == "current")
            .order_by(Appointment.start_date.desc())
            .limit(1)
        ).first()
        current_appointment = current_appointment_row[0] if current_appointment_row else None
        legislator_metadata = (
            extract_legislator_metadata(
                {
                    **(person_data["raw_payload"] if isinstance(person_data["raw_payload"], dict) else {}),
                    "full_name": person_data["full_name"],
                    "source_url": person.source_url,
                    "canonical_official_url": person.canonical_official_url,
                },
                current_appointment_row[3] if current_appointment_row else {},
                current_appointment,
                current_appointment_row[1] if current_appointment_row else None,
                current_appointment_row[2] if current_appointment_row else None,
                current_appointment_row[4] if current_appointment_row else None,
            )
            if selected_category in {"federal_senate", "federal_house"}
            else None
        )

        ai_service = AIAssistService()
        recent_statements = statements_service.list_recent_taiwan_statements(person.id, limit=3)
        recent_official_statements = statements_service.list_recent_official_statements(person.id, limit=3)
        recent_social_posts = statements_service.list_recent_social_posts(person.id, limit=5)
        statement_years = statements_service.list_statement_years(person.id)
        media_reports = statements_service.list_recent_media_reports(person.id, limit=10)
        legislation_sponsor_rows = (
            session.execute(
                select(LegislationSponsor.role, Legislation)
                .join(Legislation, Legislation.id == LegislationSponsor.legislation_id)
                .where(LegislationSponsor.person_id == person.id)
                .order_by(
                    Legislation.introduced_date.desc().nullslast(),
                    Legislation.last_action_date.desc().nullslast(),
                    Legislation.id.desc(),
                )
            )
            .all()
        )
        recent_legislation = [item[1] for item in legislation_sponsor_rows[:5]]
        proposal_count = sum(1 for role, _item in legislation_sponsor_rows if str(role or "").lower() == "sponsor")
        cosponsor_count = sum(1 for role, _item in legislation_sponsor_rows if str(role or "").lower() == "cosponsor")
        recent_visits = [item for item in recent_statements if _looks_like_taiwan_visit_statement(item)][:5]

        recent_statement_sources = {item.id: statements_service.list_sources_for_statement(item.id) for item in recent_statements}
        recent_statement_source_counts = {item.id: len(recent_statement_sources[item.id]) for item in recent_statements}
        recent_statement_participants = {
            item.id: _participant_entries(statements_service.list_participants_for_statement(item.id))
            for item in recent_statements
        }
        recent_official_sources = {item.id: statements_service.list_sources_for_statement(item.id) for item in recent_official_statements}
        recent_official_source_counts = {item.id: len(recent_official_sources[item.id]) for item in recent_official_statements}
        recent_official_participants = {
            item.id: _participant_entries(statements_service.list_participants_for_statement(item.id))
            for item in recent_official_statements
        }
        recent_social_sources = {item.id: statements_service.list_sources_for_statement(item.id) for item in recent_social_posts}
        recent_social_source_counts = {item.id: len(recent_social_sources[item.id]) for item in recent_social_posts}
        recent_social_participants = {
            item.id: _participant_entries(statements_service.list_participants_for_statement(item.id))
            for item in recent_social_posts
        }
        media_sources = {item.id: statements_service.list_sources_for_statement(item.id) for item in media_reports}
        media_source_counts = {item.id: len(media_sources[item.id]) for item in media_reports}
        media_participants = {
            item.id: _participant_entries(statements_service.list_participants_for_statement(item.id))
            for item in media_reports
        }

        trackers = session.execute(
            select(Tracker.name, Tracker.status, Tracker.last_run_at, Tracker.last_run_status).where(Tracker.person_id == person.id)
        ).all()
        last_sync = None
        if trackers:
            last_sync = session.execute(
                select(SyncRun.job_name, SyncRun.status, SyncRun.started_at, SyncRun.ended_at, SyncRun.error_message)
                .where(SyncRun.job_name.like("tracker_sync_%"))
                .order_by(desc(SyncRun.started_at))
                .limit(1)
            ).first()

    top_left, top_right = st.columns([1, 2])
    with top_left:
        if person_data["portrait_url"]:
            st.image(person_data["portrait_url"])
            if person_data["portrait_source_type"] or person_data["portrait_source_url"]:
                source_type = source_bucket_label(person_data["portrait_source_type"], person_data["portrait_source_url"], lang)
                source_url = person_data["portrait_source_url"] or ""
                render_source_badges(source_type, source_url, lang)
        else:
            st.info(labels["no_portrait"])
        st.markdown(f"**{'背景資料' if lang == 'zh-TW' else 'Background'}**")
        if person_data["date_of_birth"]:
            st.write(f"{labels['date_of_birth']}: {_clean_background_text(person_data['date_of_birth'])}")
            source_note = _format_background_source("date_of_birth", person_data, labels, lang)
            if source_note:
                st.caption(source_note)
        if person_data["place_of_birth"]:
            st.write(f"{labels['place_of_birth']}: {_clean_background_text(person_data['place_of_birth'])}")
            source_note = _format_background_source("place_of_birth", person_data, labels, lang)
            if source_note:
                st.caption(source_note)
        if person_data["ethnicity"]:
            st.write(f"{labels['ethnicity']}: {_clean_background_text(person_data['ethnicity'])}")
        if person_data["religion"]:
            st.write(f"{labels['religion']}: {_clean_background_text(person_data['religion'])}")
        if person_data["education"]:
            st.write(labels["education"])
            st.write(_clean_background_text(person_data["education"]))
            source_note = _format_background_source("education", person_data, labels, lang)
            if source_note:
                st.caption(source_note)
        if person_data["career_history"]:
            st.write(labels["career_history"])
            st.write(_clean_background_text(person_data["career_history"]))
            source_note = _format_background_source("career_history", person_data, labels, lang)
            if source_note:
                st.caption(source_note)
        if person_data["bio"]:
            st.write(_clean_background_text(person_data["bio"]))

    with top_right:
        display_title = person_data["full_name"]
        generated_chinese_name = None
        if chinese_aliases:
            primary_chinese_name = chinese_aliases[0]
            display_title = f"{primary_chinese_name} ({person_data['full_name']})"
        elif lang == "zh-TW":
            generated_chinese_name = ai_service.chinese_name_for_person(
                person_data["full_name"],
                current_appointment,
                str((person_data["raw_payload"] or {}).get("jurisdiction_name") or ""),
            )
            if generated_chinese_name:
                display_title = f"{generated_chinese_name} ({person_data['full_name']})"
        st.subheader(display_title)
        chinese_name_label = "中文譯名" if lang == "zh-TW" else "Chinese names"
        if chinese_aliases:
            st.write(chinese_name_label)
            st.write(" / ".join(chinese_aliases) if lang == "zh-TW" else ", ".join(chinese_aliases))
        elif generated_chinese_name:
            st.caption("中文名由 AI 協助生成" if lang == "zh-TW" else "Chinese name generated with AI assistance")

        _render_db_person_highlights(
            recent_events=recent_statements,
            recent_legislation=recent_legislation,
            recent_visits=recent_visits,
            proposal_count=proposal_count,
            cosponsor_count=cosponsor_count,
            ai_service=ai_service,
            lang=lang,
            labels=labels,
        )

        if person_data["full_name_display"] and person_data["full_name_display"] != person_data["full_name"]:
            full_name_label = "全名" if lang == "zh-TW" else "Full name"
            st.write(f"{full_name_label}: {person_data['full_name_display']}")
            source_note = _format_background_source("full_name_display", person_data, labels, lang)
            if source_note:
                st.caption(source_note)
        if legislator_metadata:
            party_label = "黨籍" if lang == "zh-TW" else "Party"
            district_label = "選區" if lang == "zh-TW" else "District"
            committees_label = "委員會" if lang == "zh-TW" else "Committees"
            service_label = "過去國會資歷" if lang == "zh-TW" else "Prior congressional service"
            congress_label = "Congress.gov 頁面" if lang == "zh-TW" else "Congress.gov profile"
            congress_search_label = "Congress.gov 搜尋" if lang == "zh-TW" else "Congress.gov search"
            if legislator_metadata.get("party"):
                st.write(f"{party_label}: {legislator_metadata['party']}")
            if legislator_metadata.get("district"):
                st.write(f"{district_label}: {legislator_metadata['district']}")
            if legislator_metadata.get("committees"):
                st.write(f"{committees_label}:")
                for committee in legislator_metadata["committees"]:
                    if isinstance(committee, dict):
                        label = committee.get("name") or committee.get("title") or str(committee)
                    else:
                        label = str(committee)
                    st.markdown(f"- {label}")
            if legislator_metadata.get("congress_service_history"):
                st.write(service_label)
                for item in legislator_metadata["congress_service_history"]:
                    chamber_label = item.get("label") or item.get("chamber") or "Congress"
                    congress_number = item.get("congress")
                    district = item.get("district")
                    years = " - ".join([str(value) for value in [item.get("start_year"), item.get("end_year")] if value])
                    detail_bits = [str(chamber_label)]
                    if congress_number:
                        detail_bits.append(f"{congress_number}th Congress")
                    if district:
                        detail_bits.append(f"district {district}")
                    if years:
                        detail_bits.append(years)
                    st.markdown(f"- {' | '.join(detail_bits)}")
            if legislator_metadata.get("congress_profile_url"):
                st.markdown(f"[{congress_label}]({legislator_metadata['congress_profile_url']})")
            else:
                st.markdown(
                    f"[{congress_search_label}]({build_congress_member_search_url(person_data['full_name'], current_appointment)})"
                )
        if person_data["social_profiles"]:
            st.write(labels["social_profiles"])
            render_social_links(person_data["social_profiles"], key_prefix=f"person-social-{person_id}")

        person_source_url = person_data["source_url"]
        official_page_url = person_data["canonical_official_url"] if is_government_url(person_data["canonical_official_url"]) else None
        if not official_page_url and is_government_url(person_source_url):
            official_page_url = person_source_url

        st.write(f"{labels['primary_source']}: {source_bucket_label(person_data['source_type'], person_source_url, lang)}")
        st.write(f"{labels['official_page']}: {official_page_url or 'N/A'}")

        st.write(labels["aliases"])
        st.write(", ".join(aliases) if aliases else "N/A")

        wikipedia_url = resolve_wikipedia_url(person_data["source_url"], person_data["raw_payload"])
        wikipedia_search_url = build_wikipedia_search_url(person_data["full_name"], current_appointment)
        wikipedia_label = "Wikipedia 頁面" if lang == "zh-TW" else "Wikipedia page"
        wikipedia_search_label = "Wikipedia 搜尋" if lang == "zh-TW" else "Wikipedia search"
        if wikipedia_url:
            st.markdown(f"[{wikipedia_label}]({wikipedia_url})")
        else:
            st.markdown(f"[{wikipedia_search_label}]({wikipedia_search_url})")

        if not official_page_url:
            official_search_url = build_google_official_search_url(person_data["full_name"], current_appointment)
            official_bio_search_url = build_google_official_bio_search_url(person_data["full_name"], current_appointment)
            official_search_label = "Google 搜尋官方資料" if lang == "zh-TW" else "Search official sources"
            official_bio_label = "Google 搜尋官方簡歷" if lang == "zh-TW" else "Search official biography"
            st.markdown(f"[{official_search_label}]({official_search_url})")
            st.markdown(f"[{official_bio_label}]({official_bio_search_url})")
            extra_official_links = (person_data["raw_payload"] or {}).get("official_search_urls", {})
            if extra_official_links.get("whitehouse_search"):
                whitehouse_label = "Google 搜尋白宮資料" if lang == "zh-TW" else "Search White House sources"
                st.markdown(f"[{whitehouse_label}]({extra_official_links['whitehouse_search']})")
            if extra_official_links.get("department_search"):
                department_label = "Google 搜尋部會資料" if lang == "zh-TW" else "Search department sources"
                st.markdown(f"[{department_label}]({extra_official_links['department_search']})")

        x_links = (person_data["raw_payload"] or {}).get("x_candidate_links", {})
        x_search_url = x_links.get("google_x_search") or build_x_search_url(person_data["full_name"], current_appointment)
        x_search_label = "X 搜尋候選帳號" if lang == "zh-TW" else "Search X candidates"
        st.markdown(f"[{x_search_label}]({x_search_url})")

        confirmed_x_profiles = _merged_confirmed_x_profiles(person_data["raw_payload"], person_data["social_profiles"])
        if confirmed_x_profiles:
            st.caption("已確認 X 帳號" if lang == "zh-TW" else "Confirmed X accounts")
            for source_label_text, source_profiles in _group_confirmed_x_profiles(confirmed_x_profiles, lang):
                st.caption(source_label_text)
                for confirmed_profile in source_profiles:
                    profile_url = confirmed_profile.get("profile_url")
                    if profile_url:
                        st.markdown(f"- [{profile_url}]({profile_url})")

        high_confidence_candidates, review_candidates, rejected_candidates = _candidate_sections(person_data["raw_payload"])
        if not high_confidence_candidates and not review_candidates and not rejected_candidates:
            st.caption("目前尚未解析出 X 候選帳號，請先使用搜尋連結。" if lang == "zh-TW" else "No parsed X candidates yet. Use the search link for now.")
        if high_confidence_candidates:
            st.caption("高可信 X 候選帳號" if lang == "zh-TW" else "High-confidence X candidates")
            for candidate in high_confidence_candidates:
                _render_x_candidate_block(person_id, candidate, lang, "x-high")
        if review_candidates:
            with st.expander("待審核 X 候選帳號" if lang == "zh-TW" else "X candidates needing review"):
                for candidate in review_candidates:
                    _render_x_candidate_block(person_id, candidate, lang, "x-review")
        if rejected_candidates:
            with st.expander("已排除 X 候選帳號" if lang == "zh-TW" else "Rejected X candidates"):
                for candidate in rejected_candidates:
                    st.markdown(f"- [{candidate.get('title') or candidate.get('handle')}]({candidate.get('profile_url')})")
                    if candidate.get("reasons"):
                        st.caption(f"排除原因: {candidate['reasons']}" if lang == "zh-TW" else f"Rejected because: {candidate['reasons']}")
        missing_background_fields = [
            field_name
            for field_name in ("date_of_birth", "place_of_birth", "education", "career_history")
            if not person_data.get(field_name)
        ]
        if missing_background_fields:
            section_label = "背景資料搜尋入口" if lang == "zh-TW" else "Background research links"
            missing_label = "尚缺欄位" if lang == "zh-TW" else "Missing fields"
            with st.expander(section_label):
                st.caption(f"{missing_label}: {', '.join(labels.get(field_name, field_name) for field_name in missing_background_fields)}")
                for link_key, link_url in _background_search_links(person_data, current_appointment):
                    st.markdown(f"- [{_background_search_label(link_key, lang)}]({link_url})")
        if last_sync:
            st.caption(f"{labels['last_sync']}: {last_sync.started_at} | {last_sync.status} | {last_sync.error_message or 'OK'}")
        _render_manual_event_ingest_form(person_id=int(person_id), lang=lang, labels=labels)

    st.subheader(labels["recent_taiwan_statements"])
    overview_tab_label = "最新綜覽" if lang == "zh-TW" else "Overview"
    official_tab_label = "官方聲明" if lang == "zh-TW" else "Official statements"
    social_tab_label = "社群貼文" if lang == "zh-TW" else "Social posts"
    media_tab_label = "媒體報導" if lang == "zh-TW" else "Media reports"
    overview_tab, official_tab, social_tab, media_tab = st.tabs(
        [overview_tab_label, official_tab_label, social_tab_label, media_tab_label]
    )

    with overview_tab:
        _render_statement_cards(recent_statements, recent_statement_source_counts, recent_statement_sources, recent_statement_participants, lang, labels)
    with official_tab:
        _render_statement_cards(recent_official_statements, recent_official_source_counts, recent_official_sources, recent_official_participants, lang, labels)
    with social_tab:
        _render_statement_cards(recent_social_posts, recent_social_source_counts, recent_social_sources, recent_social_participants, lang, labels)
    with media_tab:
        _render_statement_cards(media_reports, media_source_counts, media_sources, media_participants, lang, labels)

    st.subheader(labels["browse_by_year"])
    if statement_years:
        selected_year = st.selectbox(labels["year"], statement_years)
        with session_scope() as session:
            statements_service = StatementsService(session)
            yearly_statements = statements_service.list_statements_by_year(person_id, int(selected_year))
            yearly_source_counts = {item.id: statements_service.get_source_count(item.id) for item in yearly_statements}
        yearly_df = localize_dataframe(
            _format_statement_rows(yearly_statements, lang, yearly_source_counts),
            lang,
            value_columns=["review_status"],
        )
        yearly_df = style_source_columns(yearly_df, ["來源類型", "Source type"])
        st.dataframe(yearly_df, use_container_width=True)
    else:
        st.info(labels["no_historical_statements"])

    tab1, tab2, tab3 = st.tabs([labels["office_history"], labels["recent_media_reports"], labels["tracker_status"]])
    with tab1:
        st.dataframe(
            localize_dataframe(
                pd.DataFrame(appointments, columns=["role", "party", "status", "start_date", "end_date", "district"]),
                lang,
                value_columns=["status"],
            ),
            use_container_width=True,
        )
    with tab2:
        media_df = localize_dataframe(
            _format_statement_rows(media_reports, lang, media_source_counts),
            lang,
            value_columns=["review_status"],
        )
        media_df = style_source_columns(media_df, ["來源類型", "Source type"])
        st.dataframe(media_df, use_container_width=True)
    with tab3:
        st.dataframe(
            localize_dataframe(
                pd.DataFrame(trackers, columns=["name", "status", "last_run_at", "last_run_status"]),
                lang,
                value_columns=["status", "last_run_status"],
            ),
            use_container_width=True,
        )


def _render_google_sheet_fallback(lang: str, labels: dict[str, str], pending_person_id: int | None) -> bool:
    sheet_service = GoogleSheetReadService()
    people = sheet_service.list_people()
    if not people:
        return False

    st.info(
        "Google Sheet fallback mode is active. The cloud app is showing exported profile data."
        if lang != "zh-TW"
        else "目前使用 Google Sheet fallback 模式，雲端版先顯示已匯出的人物資料。"
    )
    categories = list(PERSON_CATEGORIES.keys())
    selected_category = st.selectbox(
        labels["person_category"],
        categories,
        format_func=lambda key: _category_label(PERSON_CATEGORIES[key], lang),
        key="sheet-person-category",
    )
    selected_status = st.selectbox(
        labels["status_filter"],
        ["current", "former", "unknown"],
        format_func=lambda value: str(localize_value(value, lang)),
        key="sheet-person-status",
    )
    candidates = [person for person in people if _sheet_person_matches_category(person, selected_category) and person.get("status") == selected_status]
    if not candidates:
        candidates = [person for person in people if _sheet_person_matches_category(person, selected_category)]
    if not candidates:
        st.info(labels["no_people_loaded"])
        return True

    person = sheet_service.get_person(int(pending_person_id)) if pending_person_id else None
    if person is None:
        person_options = {
            f"{person_item.get('display_name_en') or person_item.get('full_name')} ({person_item.get('office_title') or labels['unknown']})": person_item
            for person_item in candidates
        }
        selected_person_label = st.selectbox(labels["select_person"], list(person_options.keys()), key="sheet-person-select")
        person = person_options[selected_person_label]

    person_id = int(person.get("person_id") or 0)
    social_profiles = {}
    if person.get("x_accounts_list"):
        social_profiles["x"] = person["x_accounts_list"][0]
    if person.get("facebook_accounts_list"):
        social_profiles["facebook"] = person["facebook_accounts_list"][0]
    if person.get("instagram_accounts_list"):
        social_profiles["instagram"] = person["instagram_accounts_list"][0]

    top_left, top_right = st.columns([1, 2])
    with top_left:
        if person.get("portrait_url"):
            st.image(person["portrait_url"])
        else:
            st.info(labels["no_portrait"])
        st.markdown(f"**{'背景資料' if lang == 'zh-TW' else 'Background'}**")
        st.write(f"{labels['date_of_birth']}: {_clean_background_text(person.get('date_of_birth') or 'N/A')}")
        st.write(f"{labels['place_of_birth']}: {_clean_background_text(person.get('place_of_birth') or 'N/A')}")
        if person.get("education"):
            st.write(labels["education"])
            st.write(_clean_background_text(person["education"]))
        if person.get("past_experience"):
            st.write(labels["career_history"])
            st.write(_clean_background_text(person["past_experience"]))
        if person.get("committees_list"):
            committees_label = "委員會" if lang == "zh-TW" else "Committees"
            st.write(f"{committees_label}: {' | '.join(person['committees_list'])}")
    with top_right:
        st.subheader(str(person.get("display_name_en") or person.get("full_name") or ""))
        if person.get("display_name_zh"):
            chinese_name_label = "ä¸­æ–‡è­¯å" if lang == "zh-TW" else "Chinese names"
            st.write(f"{chinese_name_label}: {person['display_name_zh']}")
        st.write(f"{labels['official_page']}: {person.get('official_page') or 'N/A'}")
        if social_profiles:
            st.write(labels["social_profiles"])
            render_social_links(social_profiles, key_prefix=f"sheet-person-social-{person_id}")
        if person.get("wikipedia_page"):
            st.markdown(f"[Wikipedia]({person['wikipedia_page']})")

    recent_events = sheet_service.list_events_for_person(person_id)[:5]
    related_legislation = sheet_service.list_legislation_for_person(person_id)[:5]

    st.subheader(labels["recent_taiwan_statements"])
    if recent_events:
        event_rows = pd.DataFrame(
            [
                {
                    "published_at": item.get("event_date_date"),
                    "title": item.get("title"),
                    "source_type": item.get("primary_source_type"),
                    "source_count": item.get("source_count_int"),
                    "review_status": item.get("review_status"),
                }
                for item in recent_events
            ]
        )
        st.dataframe(localize_dataframe(event_rows, lang, value_columns=["review_status"]), use_container_width=True)
    else:
        st.info(labels["no_recent_statements"])

    related_label = "ç›¸é—œç«‹æ³•" if lang == "zh-TW" else "Related legislation"
    st.subheader(related_label)
    if related_legislation:
        legislation_rows = pd.DataFrame(
            [
                {
                    "date": item.get("date_date"),
                    "bill_number": item.get("bill_number"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "official_page": item.get("official_page"),
                }
                for item in related_legislation
            ]
        )
        st.dataframe(localize_dataframe(legislation_rows, lang, value_columns=["status"]), use_container_width=True)
    else:
        st.info("ç›®å‰é‚„æ²’æœ‰ç›¸é—œç«‹æ³•è³‡æ–™ã€‚" if lang == "zh-TW" else "No related legislation is available yet.")
    return True


def _render_google_sheet_fallback_v2(lang: str, labels: dict[str, str], pending_person_id: int | None) -> bool:
    sheet_service = GoogleSheetReadService()
    ai_service = AIAssistService()
    people = sheet_service.list_people()
    if not people:
        return False

    st.info(
        "Google Sheet fallback mode is active. The cloud app is showing exported profile data."
        if lang != "zh-TW"
        else "目前使用 Google Sheet fallback 模式，雲端版先顯示已匯出的人物資料。"
    )
    categories = list(PERSON_CATEGORIES.keys())
    selected_category = st.selectbox(
        labels["person_category"],
        categories,
        format_func=lambda key: _category_label(PERSON_CATEGORIES[key], lang),
        key="sheet-person-category-v2",
    )
    selected_status = st.selectbox(
        labels["status_filter"],
        ["current", "former", "unknown"],
        format_func=lambda value: str(localize_value(value, lang)),
        key="sheet-person-status-v2",
    )
    candidates = [person for person in people if _sheet_person_matches_category(person, selected_category) and person.get("status") == selected_status]
    if not candidates and selected_status == "unknown":
        candidates = [person for person in people if _sheet_person_matches_category(person, selected_category)]
    if not candidates:
        st.info(labels["no_people_loaded"])
        return True

    person = sheet_service.get_person(int(pending_person_id)) if pending_person_id else None
    if person is None:
        person_options = {
            f"{person_item.get('display_name_en') or person_item.get('full_name')} ({person_item.get('office_title') or labels['unknown']})": person_item
            for person_item in candidates
        }
        selected_person_label = st.selectbox(labels["select_person"], list(person_options.keys()), key="sheet-person-select-v2")
        person = person_options[selected_person_label]

    person_id = int(person.get("person_id") or 0)
    all_person_legislation = sheet_service.list_legislation_for_person(person_id)
    recent_events = sheet_service.list_events_for_person(person_id)[:5]
    related_legislation = all_person_legislation[:5]
    recent_visits = [item for item in recent_events if _looks_like_taiwan_visit_sheet_event(item)][:5]
    proposal_count, cosponsor_count = _sheet_legislation_role_counts(all_person_legislation)

    social_profiles = {}
    if person.get("x_accounts_list"):
        social_profiles["x"] = person["x_accounts_list"][0]
    if person.get("facebook_accounts_list"):
        social_profiles["facebook"] = person["facebook_accounts_list"][0]
    if person.get("instagram_accounts_list"):
        social_profiles["instagram"] = person["instagram_accounts_list"][0]

    chinese_name = str(person.get("display_name_zh") or "").strip()
    generated_name = None
    if not chinese_name:
        generated_name = ai_service.chinese_name_for_person(
            str(person.get("full_name") or ""),
            str(person.get("office_title") or ""),
            str(person.get("jurisdiction") or ""),
        )
        chinese_name = generated_name or ""

    top_left, top_right = st.columns([1, 2])
    with top_left:
        if person.get("portrait_url"):
            st.image(person["portrait_url"])
        else:
            st.info(labels["no_portrait"])
        st.markdown(f"**{'背景資料' if lang == 'zh-TW' else 'Background'}**")
        st.write(f"{labels['date_of_birth']}: {person.get('date_of_birth') or 'N/A'}")
        st.write(f"{labels['place_of_birth']}: {person.get('place_of_birth') or 'N/A'}")
        if person.get("education"):
            st.write(labels["education"])
            st.write(person["education"])
        if person.get("past_experience"):
            st.write(labels["career_history"])
            st.write(person["past_experience"])
        if person.get("committees_list"):
            committees_label = "委員會" if lang == "zh-TW" else "Committees"
            st.write(f"{committees_label}: {' | '.join(person['committees_list'])}")

    with top_right:
        english_name = str(person.get("full_name") or person.get("display_name_en") or "")
        st.subheader(f"{chinese_name} ({english_name})" if chinese_name else english_name)
        if generated_name:
            st.caption("中文名由 AI 協助生成" if lang == "zh-TW" else "Chinese name generated with AI assistance")
        st.write(f"{labels['official_page']}: {person.get('official_page') or 'N/A'}")
        if social_profiles:
            st.write(labels["social_profiles"])
            render_social_links(social_profiles, key_prefix=f"sheet-person-social-v2-{person_id}")
        if person.get("wikipedia_page"):
            st.markdown(f"[Wikipedia]({person['wikipedia_page']})")
        _render_sheet_person_highlights(
            recent_events=recent_events,
            recent_legislation=related_legislation,
            recent_visits=recent_visits,
            proposal_count=proposal_count,
            cosponsor_count=cosponsor_count,
            ai_service=ai_service,
            lang=lang,
            labels=labels,
        )
    return True


def _render_sheet_person_highlights(
    recent_events: list[dict[str, object]],
    recent_legislation: list[dict[str, object]],
    recent_visits: list[dict[str, object]],
    proposal_count: int,
    cosponsor_count: int,
    ai_service: AIAssistService,
    lang: str,
    labels: dict[str, str],
) -> None:
    statements_label = "最近台灣相關言論" if lang == "zh-TW" else "Recent Taiwan-related statements"
    legislation_label = "最近台灣相關法案" if lang == "zh-TW" else "Recent Taiwan-related legislation"
    visits_label = "最近訪台記錄" if lang == "zh-TW" else "Recent Taiwan visit records"

    st.markdown(f"**{statements_label}**")
    if recent_events:
        for item in recent_events[:3]:
            summary = str(item.get("summary") or item.get("title") or "")
            localized_summary = ai_service.summarize_statement(str(item.get("title") or ""), summary) if lang == "zh-TW" else None
            display_summary = localized_summary or _truncate_text(summary, 110)
            event_date = item.get("event_date_date")
            st.markdown(f"- `{event_date.strftime('%Y-%m-%d') if event_date else 'N/A'}`: {display_summary}")
    else:
        st.caption(labels["no_recent_statements"])

    st.markdown(f"**{legislation_label}**")
    st.caption(f"{'提案數' if lang == 'zh-TW' else 'Sponsored'}: {proposal_count} | {'聯署數' if lang == 'zh-TW' else 'Cosponsored'}: {cosponsor_count}")
    if recent_legislation:
        for item in recent_legislation[:3]:
            summary = str(item.get("summary") or item.get("title") or "")
            localized_summary = (
                ai_service.summarize_legislation(
                    str(item.get("bill_number") or ""),
                    str(item.get("title") or ""),
                    summary,
                    str(item.get("latest_action") or ""),
                )
                if lang == "zh-TW"
                else None
            )
            display_summary = localized_summary or _truncate_text(summary, 110)
            item_date = item.get("date_date")
            st.markdown(f"- `{item_date.strftime('%Y-%m-%d') if item_date else 'N/A'}` {item.get('bill_number') or ''}: {display_summary}")
    else:
        st.caption("目前沒有相關法案。" if lang == "zh-TW" else "No related legislation yet.")

    st.markdown(f"**{visits_label}**")
    if recent_visits:
        for item in recent_visits[:3]:
            summary = str(item.get("summary") or item.get("title") or "")
            localized_summary = ai_service.summarize_statement(str(item.get("title") or ""), summary) if lang == "zh-TW" else None
            display_summary = localized_summary or _truncate_text(summary, 110)
            event_date = item.get("event_date_date")
            st.markdown(f"- `{event_date.strftime('%Y-%m-%d') if event_date else 'N/A'}`: {display_summary}")
    else:
        st.caption("目前沒有訪台記錄。" if lang == "zh-TW" else "No Taiwan visit records yet.")


def _render_db_person_highlights(
    recent_events: list[Statement],
    recent_legislation: list[Legislation],
    recent_visits: list[Statement],
    proposal_count: int,
    cosponsor_count: int,
    ai_service: AIAssistService,
    lang: str,
    labels: dict[str, str],
) -> None:
    statements_label = "最近台灣相關言論" if lang == "zh-TW" else "Recent Taiwan-related statements"
    legislation_label = "最近台灣相關法案" if lang == "zh-TW" else "Recent Taiwan-related legislation"
    visits_label = "最近訪台記錄" if lang == "zh-TW" else "Recent Taiwan visit records"

    st.markdown(f"**{statements_label}**")
    if recent_events:
        for item in recent_events[:3]:
            localized_summary = (
                ai_service.summarize_statement(item.title, item.excerpt or item.full_text or item.raw_text or "")
                if lang == "zh-TW"
                else None
            )
            display_summary = localized_summary or _truncate_text(item.excerpt or item.full_text or item.raw_text or item.title, 110)
            event_date = item.date_published or item.date_collected
            st.markdown(f"- `{event_date.strftime('%Y-%m-%d') if event_date else 'N/A'}`: {display_summary}")
    else:
        st.caption(labels["no_recent_statements"])

    st.markdown(f"**{legislation_label}**")
    st.caption(f"{'提案數' if lang == 'zh-TW' else 'Sponsored'}: {proposal_count} | {'聯署數' if lang == 'zh-TW' else 'Cosponsored'}: {cosponsor_count}")
    if recent_legislation:
        for item in recent_legislation[:3]:
            payload = item.raw_payload or {}
            localized_summary = (
                ai_service.summarize_legislation(
                    str(item.bill_number or ""),
                    str(item.title or ""),
                    str(item.summary or ""),
                    str(payload.get("latest_action_text") or ""),
                )
                if lang == "zh-TW"
                else None
            )
            display_summary = localized_summary or _truncate_text(item.summary or item.title, 110)
            item_date = item.introduced_date or item.last_action_date
            st.markdown(f"- `{item_date.strftime('%Y-%m-%d') if item_date else 'N/A'}` {item.bill_number or ''}: {display_summary}")
    else:
        st.caption("目前沒有相關法案。" if lang == "zh-TW" else "No related legislation yet.")

    st.markdown(f"**{visits_label}**")
    if recent_visits:
        for item in recent_visits[:3]:
            localized_summary = (
                ai_service.summarize_statement(item.title, item.excerpt or item.full_text or item.raw_text or "")
                if lang == "zh-TW"
                else None
            )
            display_summary = localized_summary or _truncate_text(item.excerpt or item.full_text or item.raw_text or item.title, 110)
            event_date = item.date_published or item.date_collected
            st.markdown(f"- `{event_date.strftime('%Y-%m-%d') if event_date else 'N/A'}`: {display_summary}")
    else:
        st.caption("目前沒有訪台記錄。" if lang == "zh-TW" else "No Taiwan visit records yet.")


def _truncate_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _looks_like_taiwan_visit_statement(statement: Statement) -> bool:
    text = "\n".join(
        [
            str(statement.title or ""),
            str(statement.excerpt or ""),
            str(statement.full_text or ""),
            str(statement.raw_text or ""),
        ]
    ).lower()
    has_taiwan = "taiwan" in text or "訪台" in text or "台灣" in text
    has_visit = any(keyword in text for keyword in ("visit", "visited", "trip", "delegation", "訪", "出訪"))
    return has_taiwan and has_visit


def _looks_like_taiwan_visit_sheet_event(item: dict[str, object]) -> bool:
    text = "\n".join(
        [
            str(item.get("title") or ""),
            str(item.get("summary") or ""),
            str(item.get("taiwan_keywords") or ""),
        ]
    ).lower()
    has_taiwan = "taiwan" in text or "訪台" in text or "台灣" in text
    has_visit = any(keyword in text for keyword in ("visit", "visited", "trip", "delegation", "訪", "出訪"))
    return has_taiwan and has_visit


def _sheet_legislation_role_counts(rows: list[dict[str, object]]) -> tuple[int, int]:
    proposal_count = 0
    cosponsor_count = 0
    for row in rows:
        sponsor_ids = list(row.get("sponsor_ids_list") or [])
        if sponsor_ids:
            proposal_count += 1
            if len(sponsor_ids) > 1:
                cosponsor_count += len(sponsor_ids) - 1
    return proposal_count, cosponsor_count


def _sheet_person_matches_category(person: dict[str, object], category_key: str) -> bool:
    if category_key == "all":
        return True
    level = str(person.get("level") or "").lower()
    branch = str(person.get("branch") or "").lower()
    office_title = str(person.get("office_title") or "").lower()
    department_name = str(person.get("department_name") or "").lower()
    is_senate = "sen" in office_title
    is_house = any(token in office_title for token in ("rep", "house", "assembly", "delegate"))
    if category_key == "federal_executive":
        if level == "federal" and branch == "executive":
            return True
        # Fallback when sheet rows are missing normalized level/branch.
        return any(token in office_title for token in ("secretary", "administrator", "attorney general", "director"))
    if category_key == "federal_military":
        if level == "federal" and branch == "executive":
            return _is_military_role(str(person.get("office_title") or ""), {"office_title": person.get("office_title")})
        # Fallback for sheet export rows that do not carry level/branch.
        title_for_match = str(person.get("office_title") or person.get("role_title") or "")
        payload = {"office_title": title_for_match}
        if _is_military_role(title_for_match, payload):
            return True
        return any(
            token in f"{department_name} {office_title}"
            for token in (
                "joint chiefs",
                "u.s. indo-pacific command",
                "u.s. central command",
                "u.s. southern command",
                "u.s. africa command",
                "u.s. european command",
                "u.s. strategic command",
                "u.s. transportation command",
                "u.s. northern command",
                "u.s. special operations command",
                "u.s. cyber command",
                "u.s. space command",
            )
        )
    if category_key == "federal_senate":
        return level == "federal" and branch == "legislative" and is_senate
    if category_key == "federal_house":
        return level == "federal" and branch == "legislative" and (is_house or not is_senate)
    if category_key == "state_executive":
        return level == "state" and branch == "executive"
    if category_key == "state_senate":
        return level == "state" and branch == "legislative" and is_senate
    if category_key == "state_house":
        return level == "state" and branch == "legislative" and (is_house or not is_senate)
    return True
