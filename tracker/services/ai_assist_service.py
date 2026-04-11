from __future__ import annotations

from functools import lru_cache
import json
import re
import time
from typing import Any

import httpx

from tracker.config import get_settings


class AIAssistService:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openai_api_key)

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.settings.gemini_api_key)

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

    def research_legislation_metadata_with_gemini(
        self,
        current: dict[str, Any],
        page_title: str,
        page_body: str,
        source_url: str,
    ) -> dict[str, Any] | None:
        if not self.gemini_enabled:
            return None
        prompt = (
            "You are enriching a US federal/state legislation database for a Taiwan-related civic dashboard. "
            "Use the official URL, provided page excerpt, and Google Search when helpful. "
            "Return strict JSON only. Do not invent unsupported facts; use null or [] when unknown. "
            "Prefer official legislature, Congress.gov, or government sources. "
            "Keys: title, bill_number, level, jurisdiction_name, chamber, legislation_type, summary, status_text, "
            "introduced_date, last_action_date, sponsor_names, cosponsor_names, is_taiwan_related, relevance_score, sources. "
            "Dates must be YYYY-MM-DD. level must be federal/state/other. chamber must be senate/house/unknown. "
            "legislation_type must be bill/resolution/joint_resolution/concurrent_resolution/other. "
            "summary should be one concise Traditional Chinese sentence. "
            "sources must be an array of objects with title and url for pages supporting the extracted data.\n\n"
            f"Current database record JSON:\n{json.dumps(current, ensure_ascii=False)}\n\n"
            f"Official/source URL: {source_url}\n"
            f"Fetched page title: {page_title}\n"
            f"Fetched page text excerpt:\n{page_body[:10000]}\n"
        )
        result = _gemini_grounded_json(
            api_key=self.settings.gemini_api_key or "",
            model=self.settings.gemini_model,
            prompt=prompt,
            max_output_tokens=1200,
        )
        if not result:
            return None
        result_text = str(result.get("text") or "")
        payload = _parse_json_object(result_text)
        if not isinstance(payload, dict):
            payload = _fallback_legislation_metadata_from_text(result_text, current)
        if not isinstance(payload, dict):
            return None
        if not payload.get("sponsor_names") and not payload.get("cosponsor_names"):
            try:
                sponsor_probe = _research_legislation_sponsors_with_gemini(
                    api_key=self.settings.gemini_api_key or "",
                    model=self.settings.gemini_model,
                    source_url=source_url,
                    bill_number=_clean_string(current.get("bill_number"), 100) or "",
                    bill_title=_clean_string(current.get("title"), 500) or page_title,
                    page_body=page_body,
                )
            except Exception:
                sponsor_probe = None
            if isinstance(sponsor_probe, dict):
                if sponsor_probe.get("sponsor_names"):
                    payload["sponsor_names"] = sponsor_probe.get("sponsor_names")
                if sponsor_probe.get("cosponsor_names"):
                    payload["cosponsor_names"] = sponsor_probe.get("cosponsor_names")
                if sponsor_probe.get("sources") and not payload.get("sources"):
                    payload["sources"] = sponsor_probe.get("sources")
        sanitized = _sanitize_legislation_metadata(payload)
        if not sanitized.get("sponsor_names") and not sanitized.get("cosponsor_names"):
            fallback_sponsors = _extract_sponsors_from_text(result_text or page_body)
            if fallback_sponsors.get("sponsor_names"):
                sanitized["sponsor_names"] = fallback_sponsors["sponsor_names"]
            if fallback_sponsors.get("cosponsor_names"):
                sanitized["cosponsor_names"] = fallback_sponsors["cosponsor_names"]
        sources = _clean_source_list(payload.get("sources"))
        if not sources:
            sources = _grounding_sources(result.get("raw") or {})
        sanitized["sources"] = sources
        return sanitized


    def research_legislation_metadata_with_openai(
        self,
        current: dict[str, Any],
        page_title: str,
        page_body: str,
        source_url: str,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        system_prompt = (
            "You enrich metadata for a US federal/state legislation record. "
            "Return strict JSON only. Do not invent facts; use null or [] when unknown. "
            "Keys: title, bill_number, level, jurisdiction_name, chamber, legislation_type, summary, status_text, "
            "introduced_date, last_action_date, sponsor_names, cosponsor_names, is_taiwan_related, relevance_score. "
            "Dates must be YYYY-MM-DD. level must be federal/state/other. chamber must be senate/house/unknown. "
            "legislation_type must be bill/resolution/joint_resolution/concurrent_resolution/other. "
            "summary should be one concise Traditional Chinese sentence."
        )
        user_prompt = (
            f"Current database record JSON:\n{json.dumps(current, ensure_ascii=False)}\n\n"
            f"Official/source URL: {source_url}\n"
            f"Fetched page title: {page_title}\n"
            f"Fetched page text excerpt:\n{(page_body or '')[:10000]}\n\n"
            "Task: Return the metadata JSON now."
        )
        result_text = _responses_text(
            self.settings.openai_api_key or "",
            self.settings.openai_model,
            system_prompt,
            user_prompt,
            1000,
        )
        if not result_text:
            return None
        payload = _parse_json_object(result_text)
        if not isinstance(payload, dict):
            payload = _fallback_legislation_metadata_from_text(result_text, current)
        if not isinstance(payload, dict):
            return None

        sanitized = _sanitize_legislation_metadata(payload)
        if not sanitized.get("sponsor_names") and not sanitized.get("cosponsor_names"):
            fallback_sponsors = _extract_sponsors_from_text(result_text or page_body)
            if fallback_sponsors.get("sponsor_names"):
                sanitized["sponsor_names"] = fallback_sponsors["sponsor_names"]
            if fallback_sponsors.get("cosponsor_names"):
                sanitized["cosponsor_names"] = fallback_sponsors["cosponsor_names"]

        sources = _clean_source_list(payload.get("sources"))
        if not sources and source_url:
            sources = [{"title": _clean_string(page_title, 500) or source_url, "url": source_url}]
        sanitized["sources"] = sources
        return sanitized


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


def _extract_json_string_field(text: str, key: str, max_len: int) -> str | None:
    if not text:
        return None
    pattern = rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"'
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return None
    candidate = match.group(1)
    candidate = candidate.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    candidate = candidate.replace('\"', '"')
    candidate = candidate.replace('\\', '\\')
    return _clean_string(candidate, max_len)


def _extract_json_list_field(text: str, key: str, max_items: int) -> list[str]:
    if not text:
        return []
    pattern = rf'"{re.escape(key)}"\s*:\s*\[(.*?)\]'
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return []
    body = match.group(1)
    values = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
    parsed: list[str] = []
    for value in values:
        candidate = value.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
        candidate = candidate.replace('\"', '"').replace('\\', '\\')
        cleaned = _clean_string(candidate, 160)
        if cleaned:
            parsed.append(cleaned)
    return _clean_string_list(parsed, max_items)


def _fallback_legislation_metadata_from_text(text: str, current: dict[str, Any]) -> dict[str, Any] | None:
    raw = str(text or '').strip()
    if not raw:
        return None
    cleaned = re.sub(r'```(?:json)?', '', raw, flags=re.I).replace('```', '').strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)

    summary = _extract_json_string_field(cleaned, 'summary', 1800)
    if not summary:
        summary = _clean_string(cleaned, 1800)
    if summary and summary.startswith('{') and '"title"' in summary and '"summary"' in summary:
        summary = _clean_string(current.get('summary'), 1800) or _clean_string(current.get('title'), 500)
    if not summary:
        return None

    status_text = _extract_json_string_field(cleaned, 'status_text', 255)
    if not status_text:
        status_match = re.search(r'(?:status|目前狀態|最新動作)\s*[:：]\s*([^\n]+)', raw, flags=re.I)
        if status_match:
            status_text = _clean_string(status_match.group(1), 255)

    introduced_date = _extract_json_string_field(cleaned, 'introduced_date', 20) or _clean_string(current.get('introduced_date'), 20)
    last_action_date = _extract_json_string_field(cleaned, 'last_action_date', 20) or _clean_string(current.get('last_action_date'), 20)
    if not introduced_date:
        m = re.search(r'(20\d{2}-\d{2}-\d{2})', raw)
        if m:
            introduced_date = m.group(1)

    sponsor_names = _extract_json_list_field(cleaned, 'sponsor_names', 20)
    cosponsor_names = _extract_json_list_field(cleaned, 'cosponsor_names', 100)

    lower = cleaned.casefold()
    is_taiwan_related = bool(('taiwan' in lower) or ('台灣' in cleaned) or ('臺灣' in cleaned))

    return {
        'title': _extract_json_string_field(cleaned, 'title', 500) or _clean_string(current.get('title'), 500),
        'bill_number': _extract_json_string_field(cleaned, 'bill_number', 100) or _clean_string(current.get('bill_number'), 100),
        'level': (_extract_json_string_field(cleaned, 'level', 20) or _clean_string(current.get('level'), 20) or 'other'),
        'jurisdiction_name': _extract_json_string_field(cleaned, 'jurisdiction_name', 255) or _clean_string(current.get('jurisdiction_name'), 255),
        'chamber': (_extract_json_string_field(cleaned, 'chamber', 20) or _clean_string(current.get('chamber'), 20) or 'unknown'),
        'legislation_type': (_extract_json_string_field(cleaned, 'legislation_type', 40) or _clean_string(current.get('legislation_type'), 40) or 'other'),
        'summary': summary,
        'status_text': status_text or _clean_string(current.get('status_text'), 255),
        'introduced_date': introduced_date,
        'last_action_date': last_action_date,
        'sponsor_names': sponsor_names,
        'cosponsor_names': cosponsor_names,
        'is_taiwan_related': is_taiwan_related,
        'relevance_score': 0.8 if is_taiwan_related else None,
        'sources': [],
    }


def _research_legislation_sponsors_with_gemini(
    api_key: str,
    model: str,
    source_url: str,
    bill_number: str,
    bill_title: str,
    page_body: str,
) -> dict[str, Any] | None:
    if not api_key:
        return None
    prompt = (
        "Find sponsor and cosponsor names for the following legislation. "
        "Use Google Search grounding and prioritize official legislature sources. "
        "Return strict JSON only with keys sponsor_names, cosponsor_names, sources. "
        "Each sponsor_names/cosponsor_names item must be full person name text only.\n\n"
        f"Bill number: {bill_number}\n"
        f"Bill title: {bill_title}\n"
        f"Official/source URL: {source_url}\n"
        f"Page excerpt:\n{(page_body or '')[:8000]}\n"
    )
    result = _gemini_grounded_json(
        api_key=api_key,
        model=model,
        prompt=prompt,
        max_output_tokens=700,
    )
    if not result:
        return None
    payload = _parse_json_object(str(result.get("text") or ""))
    if not isinstance(payload, dict):
        return None
    return {
        "sponsor_names": [
            name
            for name in (_normalize_legislator_name(item) for item in _clean_string_list(payload.get("sponsor_names"), 20))
            if name
        ],
        "cosponsor_names": [
            name
            for name in (_normalize_legislator_name(item) for item in _clean_string_list(payload.get("cosponsor_names"), 100))
            if name
        ],
        "sources": _clean_source_list(payload.get("sources")) or _grounding_sources(result.get("raw") or {}),
    }


def _extract_sponsors_from_text(text: str) -> dict[str, list[str]]:
    raw = str(text or "")
    if not raw.strip():
        return {"sponsor_names": [], "cosponsor_names": []}

    def _extract(patterns: list[str], limit: int) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for pattern in patterns:
            for match in re.finditer(pattern, raw, flags=re.I):
                candidate = _clean_person_name(match.group(1))
                if not candidate:
                    continue
                key = candidate.casefold()
                if key in seen:
                    continue
                seen.add(key)
                out.append(candidate)
                if len(out) >= limit:
                    return out
        return out

    sponsor_names = _extract(
        [
            r"(?:sponsor|primary sponsor|chief sponsor)\s*[:：]\s*([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,4})",
            r"sponsored by\s+([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,4})",
            r"representative\s+([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,4})\s*\[[A-Z]\]",
            r"senator\s+([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,4})\s*\[[A-Z]\]",
        ],
        20,
    )
    cosponsor_names = _extract(
        [
            r"(?:cosponsor|co-sponsor|co sponsor)s?\s*[:：]\s*([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,4})",
            r"cosponsored by\s+([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,4})",
        ],
        100,
    )
    return {"sponsor_names": sponsor_names, "cosponsor_names": cosponsor_names}


def _clean_person_name(value: str) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = re.sub(r"^[,\-–—:;]+|[,\-–—:;]+$", "", text)
    text = re.sub(r"^(?:senator|rep\.?|representative|assemblymember|delegate|member)\s+", "", text, flags=re.I)
    if not text or len(text.split()) < 2:
        return None
    if any(ch.isdigit() for ch in text):
        return None
    return text[:120]


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


def _normalize_legislator_name(value: object) -> str | None:
    text = _clean_string(value, 160)
    if not text:
        return None
    text = re.sub(r"^(?:senator|rep\.?|representative|assemblymember|delegate|member)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,;:-")
    if not text:
        return None
    parts = text.split()
    if len(parts) < 2:
        return None
    if any(part.casefold() in {"unknown", "tbd", "vacant", "vacancy"} for part in parts):
        return None
    if any(ch.isdigit() for ch in text):
        return None
    return text[:160]


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
        "sponsor_names": [
            name
            for name in (_normalize_legislator_name(item) for item in _clean_string_list(payload.get("sponsor_names"), 20))
            if name
        ],
        "cosponsor_names": [
            name
            for name in (_normalize_legislator_name(item) for item in _clean_string_list(payload.get("cosponsor_names"), 100))
            if name
        ],
        "is_taiwan_related": is_taiwan_related,
        "relevance_score": relevance_score,
    }



def _gemini_retry_delay_seconds(exc: httpx.HTTPStatusError, attempt: int) -> float:
    fallback = min(8.0, 0.8 * (2 ** max(0, attempt)))
    response = exc.response
    if response is None:
        return fallback
    retry_after = str(response.headers.get("Retry-After") or "").strip()
    if not retry_after:
        return fallback
    try:
        seconds = float(retry_after)
    except Exception:
        return fallback
    if seconds <= 0:
        return fallback
    return min(30.0, seconds)


def _gemini_grounded_json(
    api_key: str,
    model: str,
    prompt: str,
    max_output_tokens: int = 1200,
) -> dict[str, Any] | None:
    if not api_key:
        return None
    model_name = (model or "").strip() or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

    base_payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": int(max_output_tokens),
        },
    }

    variants: list[dict[str, Any]] = [
        {
            **base_payload,
            "tools": [{"google_search": {}}],
            "generationConfig": {**base_payload["generationConfig"], "responseMimeType": "application/json"},
        },
        {
            **base_payload,
            "tools": [{"google_search_retrieval": {}}],
            "generationConfig": {**base_payload["generationConfig"], "responseMimeType": "application/json"},
        },
        {
            **base_payload,
            "tools": [{"google_search": {}}],
        },
        {
            **base_payload,
            "generationConfig": {**base_payload["generationConfig"], "responseMimeType": "application/json"},
        },
        base_payload,
    ]

    max_retries = 3
    last_400_message = ""
    last_429_message = ""
    for payload in variants:
        for attempt in range(max_retries):
            try:
                response = httpx.post(url, json=payload, timeout=60.0)
                response.raise_for_status()
                raw = response.json()
                text = ""
                try:
                    candidates = raw.get("candidates") or []
                    if candidates:
                        parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
                        for part in parts:
                            value = part.get("text")
                            if isinstance(value, str) and value.strip():
                                text = value.strip()
                                break
                except Exception:
                    text = ""
                if not text:
                    text = json.dumps(raw, ensure_ascii=False)
                return {"text": text, "raw": raw}
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 400:
                    body = ""
                    try:
                        body = (exc.response.text or "")[:500]
                    except Exception:
                        body = ""
                    last_400_message = body or str(exc)
                    break
                if status == 429:
                    body = ""
                    try:
                        body = (exc.response.text or "")[:500]
                    except Exception:
                        body = ""
                    last_429_message = body or str(exc)
                    if attempt < (max_retries - 1):
                        time.sleep(_gemini_retry_delay_seconds(exc, attempt))
                        continue
                    break
                raise

    if last_429_message:
        raise ValueError(f"Gemini rate limit reached (429). Please retry shortly. Detail: {last_429_message}")
    if last_400_message:
        raise ValueError(f"Gemini request rejected (400): {last_400_message}")
    return None


def gemini_grounded_json(
    api_key: str,
    model: str,
    prompt: str,
    max_output_tokens: int = 1200,
) -> dict[str, Any] | None:
    # Backward-compatible alias for historical callsites / stale deployments.
    return _gemini_grounded_json(
        api_key=api_key,
        model=model,
        prompt=prompt,
        max_output_tokens=max_output_tokens,
    )


def _clean_source_list(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    blocked_hosts = {
        'vertexaisearch.cloud.google.com',
        'google.com/url',
    }
    for item in value:
        if not isinstance(item, dict):
            continue
        url = _clean_string(item.get('url'), 1500)
        if not url:
            continue
        lowered = url.casefold()
        if 'vertexaisearch.cloud.google.com/grounding-api-redirect' in lowered:
            continue
        if '/grounding-api-redirect/' in lowered:
            continue
        if any(host in lowered for host in blocked_hosts):
            continue
        key = lowered
        if key in seen:
            continue
        seen.add(key)
        title = _clean_string(item.get('title'), 500) or url
        cleaned.append({'title': title, 'url': url})
        if len(cleaned) >= 30:
            break
    return cleaned


def _grounding_sources(raw: dict[str, Any]) -> list[dict[str, str]]:
    if not isinstance(raw, dict):
        return []
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        candidates = raw.get("candidates") or []
        for candidate in candidates:
            grounding = (candidate or {}).get("groundingMetadata") or {}
            chunks = grounding.get("groundingChunks") or []
            for chunk in chunks:
                web = (chunk or {}).get("web") or {}
                url = _clean_string(web.get("uri"), 1500)
                if not url:
                    continue
                key = url.casefold()
                if key in seen:
                    continue
                seen.add(key)
                title = _clean_string(web.get("title"), 500) or url
                output.append({"title": title, "url": url})
                if len(output) >= 30:
                    return output
    except Exception:
        return output
    return output
