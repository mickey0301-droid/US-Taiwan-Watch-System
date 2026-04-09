from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx

from tracker.config import get_settings
from tracker.db import session_scope
from tracker.services.legislation_service import LegislationService

CONGRESSES = [118, 119]
PARSER_IDENTITY = "sync_congress_taiwan_v1"
TAIWAN_KEYWORDS = ("taiwan", "台灣", "臺灣")


def _contains_taiwan(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(keyword in lowered for keyword in TAIWAN_KEYWORDS) or any(keyword in str(text or "") for keyword in ("台灣", "臺灣"))


def _bill_url(congress: int, bill_type: str, number: str) -> str:
    return f"https://www.congress.gov/bill/{congress}th-congress/{bill_type.lower()}-bill/{number}"


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:10]
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        return None


async def _fetch_congress_bills(congress: int, api_key: str, limit: int = 250, max_pages: int = 20) -> list[dict[str, Any]]:
    base = f"https://api.congress.gov/v3/bill/{int(congress)}"
    bills: list[dict[str, Any]] = []
    offset = 0
    pages = 0
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        while pages < max_pages:
            params = {
                "api_key": api_key,
                "format": "json",
                "limit": int(limit),
                "offset": int(offset),
                "sort": "updateDate+desc",
            }
            response = await client.get(base, params=params)
            response.raise_for_status()
            data = response.json() if response.content else {}
            chunk = data.get("bills") if isinstance(data, dict) else None
            if not isinstance(chunk, list) or not chunk:
                break
            bills.extend([item for item in chunk if isinstance(item, dict)])
            pages += 1
            if len(chunk) < limit:
                break
            offset += limit
    return bills


def _to_legislation_payload(item: dict[str, Any]) -> dict[str, Any]:
    congress = int(item.get("congress") or 0)
    bill_type = str(item.get("type") or "").strip().lower()
    number = str(item.get("number") or "").strip()
    title = str(item.get("title") or "").strip()
    latest_action = item.get("latestAction") if isinstance(item.get("latestAction"), dict) else {}
    status_text = str(latest_action.get("text") or "").strip()
    source_url = _bill_url(congress, bill_type, number) if congress and bill_type and number else ""
    chamber = str(item.get("originChamber") or "").strip().lower()
    introduced_date = _parse_date(item.get("introducedDate"))
    last_action_date = _parse_date(latest_action.get("actionDate")) or _parse_date(item.get("updateDate"))
    bill_slug = f"us-{congress}-{bill_type}-{number}".strip("-")
    return {
        "title": title or source_url or "Untitled bill",
        "bill_number": f"{bill_type.upper()} {number}".strip() if bill_type or number else None,
        "bill_slug": bill_slug,
        "legislation_type": bill_type.upper() if bill_type else None,
        "level": "federal",
        "jurisdiction_name": "United States",
        "chamber": chamber,
        "summary": status_text or None,
        "status_text": status_text or None,
        "introduced_date": introduced_date,
        "last_action_date": last_action_date,
        "source_url": source_url or "https://www.congress.gov/",
        "source_type": "official_api",
        "parser_identity": PARSER_IDENTITY,
        "relevance_score": 1.0,
        "is_taiwan_related": True,
        "raw_payload": {
            "seeded_from": PARSER_IDENTITY,
            "congress_api_item": item,
            "congress": congress,
            "updateDate": item.get("updateDate"),
            "latestAction": latest_action,
        },
        "sources": [
            {
                "source_url": source_url or "https://www.congress.gov/",
                "source_type": "official_api",
                "source_title": "Congress.gov",
                "parser_identity": PARSER_IDENTITY,
                "raw_payload": {"congress": congress},
            }
        ],
    }


def _upsert_bills(bills: list[dict[str, Any]]) -> dict[str, Any]:
    created = 0
    updated = 0
    errors: list[str] = []
    with session_scope() as session:
        service = LegislationService(session)
        for item in bills:
            try:
                payload = _to_legislation_payload(item)
                _, is_created = service.upsert_legislation(payload)
                if is_created:
                    created += 1
                else:
                    updated += 1
            except Exception as exc:  # pragma: no cover
                errors.append(f"{type(exc).__name__}: {exc}")
    return {"created": created, "updated": updated, "detail_ok": 0, "errors": errors}


def run_sync_congress_taiwan() -> dict[str, Any]:
    settings = get_settings()
    if not settings.congress_api_key:
        return {"status": "skipped", "created": 0, "updated": 0, "errors": ["CONGRESS_API_KEY not configured"]}

    all_taiwan_bills: list[dict[str, Any]] = []
    scan_meta: list[dict[str, Any]] = []
    for congress in CONGRESSES:
        try:
            bills = asyncio.run(_fetch_congress_bills(congress, settings.congress_api_key))
        except Exception as exc:  # pragma: no cover
            return {"status": "failed", "created": 0, "updated": 0, "errors": [f"{type(exc).__name__}: {exc}"]}

        filtered = []
        for item in bills:
            latest_action = item.get("latestAction") if isinstance(item.get("latestAction"), dict) else {}
            text = " ".join([str(item.get("title") or ""), str(latest_action.get("text") or "")])
            if _contains_taiwan(text):
                filtered.append(item)
        all_taiwan_bills.extend(filtered)
        scan_meta.append({"congress": congress, "fetched": len(bills), "taiwan_related": len(filtered)})

    upserted = _upsert_bills(all_taiwan_bills)
    status = "success" if not upserted.get("errors") else "partial_success"
    return {
        "status": status,
        "fetched": len(all_taiwan_bills),
        "created": int(upserted.get("created", 0)),
        "updated": int(upserted.get("updated", 0)),
        "metadata": {"scans": scan_meta},
        "errors": list(upserted.get("errors") or [])[:20],
    }

