from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote_plus


def _coerce_payload_dict(value: object) -> dict[str, Any]:
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


def build_congress_member_search_url(full_name: str, role_title: str | None = None) -> str:
    query = full_name
    if role_title:
        query = f"{full_name} {role_title}"
    return f"https://www.congress.gov/search?q={quote_plus(query)}"


def congress_profile_url(source_url: str | None, canonical_official_url: str | None, raw_payload: dict[str, Any] | None) -> str | None:
    raw_payload = _coerce_payload_dict(raw_payload)
    for candidate in [
        raw_payload.get("congress_profile_url"),
        canonical_official_url,
        source_url,
    ]:
        if isinstance(candidate, str) and "congress.gov/member/" in candidate:
            return candidate
    return None


def extract_legislator_metadata(
    person_raw_payload: dict[str, Any] | None,
    current_appointment_payload: dict[str, Any] | None,
    current_role_title: str | None,
    current_party: str | None,
    current_district: str | None,
    chamber: str | None,
) -> dict[str, Any]:
    person_raw_payload = _coerce_payload_dict(person_raw_payload)
    current_appointment_payload = _coerce_payload_dict(current_appointment_payload)

    party = (
        current_party
        or current_appointment_payload.get("party")
        or person_raw_payload.get("party")
    )
    district = (
        current_district
        or current_appointment_payload.get("district")
        or person_raw_payload.get("district")
    )
    state = current_appointment_payload.get("state") or person_raw_payload.get("state")
    committees = (
        current_appointment_payload.get("committees")
        or person_raw_payload.get("committees")
        or []
    )
    service_history = (
        current_appointment_payload.get("congress_service_history")
        or person_raw_payload.get("congress_service_history")
        or []
    )

    return {
        "party": party,
        "district": district,
        "state": state,
        "chamber": chamber,
        "committees": committees if isinstance(committees, list) else [],
        "congress_service_history": service_history if isinstance(service_history, list) else [],
        "congress_profile_url": congress_profile_url(
            person_raw_payload.get("source_url"),
            person_raw_payload.get("canonical_official_url"),
            person_raw_payload,
        ),
        "congress_search_url": build_congress_member_search_url(
            str(person_raw_payload.get("full_name") or ""),
            current_role_title,
        ),
    }
