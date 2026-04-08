from __future__ import annotations

from functools import lru_cache
import json

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
