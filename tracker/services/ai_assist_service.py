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
