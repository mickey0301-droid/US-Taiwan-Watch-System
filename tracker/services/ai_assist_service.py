from __future__ import annotations

from functools import lru_cache
import json
import re
from typing import Any

import httpx

from tracker.config import get_settings


class AIAssistService:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openai_api_key)

    def chinese_name_for_person(self, full_name: str, office_title: str | None = None, jurisdiction: str | None = None) -> str | None:
        if not self.enabled or not full_name.strip():
            return None
        system_prompt = (
            "You localize names of U.S. public officials into Traditional Chinese used in Taiwan. "
            "Return only the best concise Chinese rendering of the person's name. "
            "Do not include explanations, romanization, titles, or punctuation."
        )
        user_prompt = (
            f"English name: {full_name}\n"
            f"Office: {office_title or 'Unknown'}\n"
            f"Jurisdiction: {jurisdiction or 'Unknown'}\n"
            "Task: Provide the Traditional Chinese name only."
        )
        result = _responses_text(self.settings.openai_api_key or "", self.settings.openai_model, system_prompt, user_prompt, 40)
        return result.strip() if result else None

    def summarize_statement(self, title: str, summary: str) -> str | None:
        if not self.enabled:
            return None
        system_prompt = (
            "You write short Traditional Chinese summaries for a political intelligence dashboard. "
            "Return one sentence, factual, concise, and neutral."
        )
        user_prompt = f"Title: {title}\nSource text: {summary}\nTask: Summarize in Traditional Chinese in one sentence."
        result = _responses_text(self.settings.openai_api_key or "", self.settings.openai_model, system_prompt, user_prompt, 120)
        return result.strip() if result else None

    def summarize_legislation(self, bill_number: str, title: str, summary: str, latest_action: str | None = None) -> str | None:
        if not self.enabled:
            return None
        system_prompt = (
            "You write short Traditional Chinese summaries for Taiwan-related U.S. legislation. "
            "Return one concise sentence focused on what the bill does."
        )
        user_prompt = (
            f"Bill number: {bill_number}\n"
            f"Title: {title}\n"
            f"Summary: {summary}\n"
            f"Latest action: {latest_action or 'Unknown'}\n"
            "Task: Summarize in Traditional Chinese in one sentence."
        )
        result = _responses_text(self.settings.openai_api_key or "", self.settings.openai_model, system_prompt, user_prompt, 120)
        return result.strip() if result else None

    def classify_person_type(self, title: str, body: str, source_url: str) -> str | None:
        if not self.enabled:
            return None
        system_prompt = (
            "You classify a US public figure page into one category. "
            "Return only one token from: federal_official, federal_senator, federal_house, state_official, state_legislator."
        )
        user_prompt = (
            f"URL: {source_url}\n"
            f"Title: {title}\n"
            f"Body excerpt: {body[:1200]}\n"
            "Task: choose exactly one category token."
        )
        result = _responses_text(self.settings.openai_api_key or "", self.settings.openai_model, system_prompt, user_prompt, 24)
        if not result:
            return None
        token = result.strip().lower()
        allowed = {"federal_official", "federal_senator", "federal_house", "state_official", "state_legislator"}
        return token if token in allowed else None

    def classify_event_category(self, title: str, body: str, source_url: str) -> str | None:
        if not self.enabled:
            return None
        system_prompt = (
            "Classify event category for US-Taiwan monitoring. "
            "Return only one token from: federal_official, congress_member, state_official, state_legislator, other."
        )
        user_prompt = (
            f"URL: {source_url}\n"
            f"Title: {title}\n"
            f"Body excerpt: {body[:1500]}\n"
            "Task: choose exactly one category token."
        )
        result = _responses_text(self.settings.openai_api_key or "", self.settings.openai_model, system_prompt, user_prompt, 20)
        if not result:
            return None
        token = result.strip().lower()
        allowed = {"federal_official", "congress_member", "state_official", "state_legislator", "other"}
        return token if token in allowed else None

    def extract_legislation_metadata(self, title: str, body: str, source_url: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        system_prompt = (
            "Extract structured metadata for a U.S. federal or state legislative bill page. "
            "Return strict JSON only. Do not invent details that are not supported by the provided text; use null or [] when unknown. "
            "Keys: title, bill_number, level, jurisdiction_name, chamber, legislation_type, summary, status_text, "
            "introduced_date, last_action_date, sponsor_names, cosponsor_names, is_taiwan_related, relevance_score. "
            "Dates must be YYYY-MM-DD. level must be federal/state/other. chamber must be senate/house/unknown. "
            "legislation_type must be bill/resolution/joint_resolution/concurrent_resolution/other. "
            "summary should be one concise Traditional Chinese sentence if possible."
        )
        user_prompt = (
            f"URL: {source_url}\n"
            f"Page title: {title}\n"
            f"Page text excerpt:\n{body[:7000]}\n\n"
            "Task: Extract the metadata JSON now."
        )
        result = _responses_text(self.settings.openai_api_key or "", self.settings.openai_model, system_prompt, user_prompt, 900)
        if not result:
            return None
        payload = _parse_json_object(result)
        if not isinstance(payload, dict):
            return None
        return _sanitize_legislation_metadata(payload)

    def classify_legislation_scope(self, title: str, body: str, source_url: str) -> dict[str, str] | None:
        if not self.enabled:
            return None
        system_prompt = (
            "Classify legislation scope. Return strict JSON with keys: "
            "level (federal/state/other), chamber (senate/house/unknown), legislation_type (bill/resolution/joint_resolution/concurrent_resolution/other)."
        )
        user_prompt = (
            f"URL: {source_url}\n"
            f"Title: {title}\n"
            f"Body excerpt: {body[:1600]}\n"
            "Task: Return JSON only."
        )
        result = _responses_text(self.settings.openai_api_key or "", self.settings.openai_model, system_prompt, user_prompt, 80)
        if not result:
            return None
        try:
            payload = json.loads(result)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        level = str(payload.get("level") or "").lower()
        chamber = str(payload.get("chamber") or "").lower()
        legislation_type = str(payload.get("legislation_type") or "").lower()
        if level not in {"federal", "state", "other"}:
            level = "other"
        if chamber not in {"senate", "house", "unknown"}:
            chamber = "unknown"
        allowed_types = {"bill", "resolution", "joint_resolution", "concurrent_resolution", "other"}
        if legislation_type not in allowed_types:
            legislation_type = "other"
        return {"level": level, "chamber": chamber, "legislation_type": legislation_type}


@lru_cache(maxsize=512)
def _responses_text(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
) -> str | None:
    if not api_key:
        return None
    response = httpx.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
            ],
            "max_output_tokens": max_output_tokens,
        },
        timeout=45.0,
    )
    response.raise_for_status()
    payload = response.json()
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
            if isinstance(content.get("output_text"), str) and content["output_text"].strip():
                return content["output_text"].strip()
    if isinstance(payload.get("response"), str):
        return payload["response"].strip()
    return json.dumps(payload, ensure_ascii=False)[:200]


def _parse_json_object(value: str) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _clean_string(value: object, max_len: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "unknown", "n/a", "na"}:
        return None
    text = re.sub(r"\s+", " ", text)
    return text[:max_len].strip() if max_len else text


def _clean_string_list(value: object, max_items: int = 100) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[,;\n]+", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    results: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = _clean_string(item, 160)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
        if len(results) >= max_items:
            break
    return results


def _sanitize_legislation_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    level = (_clean_string(payload.get("level"), 20) or "other").lower()
    if level not in {"federal", "state", "other"}:
        level = "other"
    chamber = (_clean_string(payload.get("chamber"), 20) or "unknown").lower()
    if chamber not in {"senate", "house", "unknown"}:
        chamber = "unknown"
    legislation_type = (_clean_string(payload.get("legislation_type"), 40) or "other").lower()
    if legislation_type not in {"bill", "resolution", "joint_resolution", "concurrent_resolution", "other"}:
        legislation_type = "other"
    relevance_raw = payload.get("relevance_score")
    try:
        relevance_score = max(0.0, min(1.0, float(relevance_raw)))
    except (TypeError, ValueError):
        relevance_score = None
    is_taiwan_related = payload.get("is_taiwan_related")
    if not isinstance(is_taiwan_related, bool):
        is_taiwan_related = None
    return {
        "title": _clean_string(payload.get("title"), 500),
        "bill_number": _clean_string(payload.get("bill_number"), 100),
        "level": level,
        "jurisdiction_name": _clean_string(payload.get("jurisdiction_name"), 255),
        "chamber": chamber,
        "legislation_type": legislation_type,
        "summary": _clean_string(payload.get("summary"), 1800),
        "status_text": _clean_string(payload.get("status_text"), 255),
        "introduced_date": _clean_string(payload.get("introduced_date"), 20),
        "last_action_date": _clean_string(payload.get("last_action_date"), 20),
        "sponsor_names": _clean_string_list(payload.get("sponsor_names"), 20),
        "cosponsor_names": _clean_string_list(payload.get("cosponsor_names"), 100),
        "is_taiwan_related": is_taiwan_related,
        "relevance_score": relevance_score,
    }
