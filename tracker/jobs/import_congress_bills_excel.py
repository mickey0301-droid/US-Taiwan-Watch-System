from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from tracker.db import session_scope
from tracker.services.legislation_service import LegislationService
from tracker.utils.congress_bills import congress_bill_url
from tracker.utils.hashing import sha256_text


DEFAULT_EXCEL_PATH = Path(r"C:/Users/pitch/Downloads/US Congress bills_ TW, HK, XJ, TB.xlsx")
TOPIC_COLUMNS = ["TW", "TW_S", "HK", "TB", "UG", "MC", "DL", "FL"]


def run_import_congress_bills_excel(excel_path: str | None = None) -> dict[str, Any]:
    path = Path(excel_path) if excel_path else DEFAULT_EXCEL_PATH
    if not path.exists():
        return {
            "job_name": "import_congress_bills_excel",
            "status": "failed",
            "records_found": 0,
            "records_created": 0,
            "records_updated": 0,
            "errors": [f"Excel file not found: {path}"],
        }

    dataframe = pd.read_excel(path, sheet_name="bills")
    prepared = _prepare_bills_dataframe(dataframe)
    eligible_rows = prepared[prepared.apply(_is_modern_taiwan_row, axis=1)].copy()

    created = 0
    updated = 0
    with session_scope() as session:
        service = LegislationService(session)
        for _, row in eligible_rows.iterrows():
            payload = _row_to_legislation_payload(row, path)
            _, was_created = service.upsert_legislation(payload)
            if was_created:
                created += 1
            else:
                updated += 1

    return {
        "job_name": "import_congress_bills_excel",
        "status": "success",
        "records_found": int(len(eligible_rows)),
        "records_created": created,
        "records_updated": updated,
        "metadata": {
            "excel_path": str(path),
            "filter": "all Congresses with TW>0",
        },
        "errors": [],
    }


def _prepare_bills_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    for column in TOPIC_COLUMNS + ["cong", "code"]:
        if column in working.columns:
            working[column] = pd.to_numeric(working[column], errors="coerce")
    working = working[working["cong"].notna()].copy()
    working = working[working["Title"].notna()].copy()
    working = working[working["type"].notna()].copy()
    working = working[~working["Title"].astype(str).str.fullmatch(r"\s*", na=False)].copy()
    working["date_parsed"] = working["date"].apply(_parse_excel_yyyymmdd)
    for column in ["Title", "description", "Current status", "status", "Sponsor", "details", "text", "search", "last_name", "first_name", "party", "district", "chamber"]:
        if column in working.columns:
            working[column] = working[column].fillna("").astype(str).str.strip()
    return working


def _parse_excel_yyyymmdd(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def _contains_taiwan_language(row: pd.Series) -> bool:
    haystack = " ".join(
        [
            str(row.get("Title", "")),
            str(row.get("description", "")),
            str(row.get("title", "")),
            str(row.get("title 2", "")),
        ]
    ).lower()
    return "taiwan" in haystack or "formosa" in haystack


def _is_modern_taiwan_row(row: pd.Series) -> bool:
    tw_value = float(row.get("TW", 0) or 0)
    return tw_value > 0


def _row_to_legislation_payload(row: pd.Series, excel_path: Path) -> dict[str, Any]:
    bill_number = f"{row.get('type', '').strip()} {int(row['code'])}".replace("  ", " ").strip()
    bill_slug = sha256_text(f"excel_congress_bills|{row.get('cong')}|{bill_number}|{row.get('Title')}")
    other_topics = {topic: int(row.get(topic, 0) or 0) for topic in TOPIC_COLUMNS if topic != "TW" and int(row.get(topic, 0) or 0) > 0}
    sponsor_name = _build_sponsor_name(row)
    derived_official_url = congress_bill_url(row.get("cong"), bill_number)
    primary_url = derived_official_url or next(
        (
            candidate
            for candidate in [row.get("details"), row.get("text"), row.get("search")]
            if isinstance(candidate, str) and candidate.startswith("http")
        ),
        f"file://{excel_path}",
    )
    status_text = row.get("Current status") or row.get("status") or "Unknown"
    summary = row.get("description") or row.get("Title")
    raw_payload = {
        "seeded_from": "congress_bills_excel_v1",
        "source_dataset_path": str(excel_path),
        "congress": int(row.get("cong", 0) or 0),
        "congress_gov_url": derived_official_url,
        "topic_flags": {topic: int(row.get(topic, 0) or 0) for topic in TOPIC_COLUMNS},
        "additional_topics": other_topics,
        "needs_topic_review": False,
        "topic_review_status": "accepted",
        "sponsor_text": row.get("Sponsor") or None,
        "status_original": row.get("status") or None,
        "source_priority": "official" if derived_official_url else "seed",
    }

    payload: dict[str, Any] = {
        "bill_slug": bill_slug,
        "bill_number": bill_number,
        "title": row.get("Title") or bill_number,
        "legislation_type": _map_legislation_type(row.get("type", "")),
        "level": "federal",
        "jurisdiction_name": "United States",
        "chamber": _map_chamber(row.get("chamber", ""), row.get("type", "")),
        "summary": summary,
        "status_text": status_text,
        "introduced_date": row.get("date_parsed"),
        "last_action_date": row.get("date_parsed"),
        "source_url": primary_url,
        "source_type": "official" if derived_official_url else "seed",
        "parser_identity": "congress_bills_excel_v1",
        "relevance_score": 1.0,
        "is_taiwan_related": True,
        "raw_payload": raw_payload,
        "sources": [
            {
                "source_url": primary_url,
                "source_type": "official" if derived_official_url else "seed",
                "source_title": "Congress.gov" if derived_official_url else "Congress bills Excel seed",
                "parser_identity": "congress_bill_url_derived_v1" if derived_official_url else "congress_bills_excel_v1",
                "raw_payload": {**raw_payload, "derived_from_excel_seed": bool(derived_official_url)},
            }
        ],
        "sponsors": [],
    }
    if sponsor_name:
        payload["sponsors"].append(
            {
                "full_name": sponsor_name,
                "role": "sponsor",
                "source_url": primary_url,
                "source_type": "seed",
            }
        )
    return payload


def _build_sponsor_name(row: pd.Series) -> str | None:
    first_name = str(row.get("first_name", "") or "").strip()
    last_name = str(row.get("last_name", "") or "").strip()
    if first_name and last_name:
        return f"{first_name} {last_name}".replace("  ", " ").strip()
    sponsor = str(row.get("Sponsor", "") or "").strip()
    if sponsor.startswith("Rep. "):
        sponsor = sponsor[5:]
    elif sponsor.startswith("Sen. "):
        sponsor = sponsor[5:]
    if "[" in sponsor:
        sponsor = sponsor.split("[", 1)[0].strip()
    return sponsor or None


def _map_legislation_type(raw_type: str) -> str:
    raw = (raw_type or "").strip().lower()
    if "res" in raw and "con" in raw:
        return "concurrent resolution"
    if "res" in raw and "j" in raw:
        return "joint resolution"
    if "res" in raw:
        return "resolution"
    return "bill"


def _map_chamber(chamber: str, bill_type: str) -> str:
    raw_chamber = (chamber or "").strip().upper()
    raw_type = (bill_type or "").strip().upper()
    if raw_chamber == "S" or raw_type.startswith("S."):
        return "senate"
    if raw_chamber == "H" or raw_type.startswith("H."):
        return "house"
    return "unknown"
