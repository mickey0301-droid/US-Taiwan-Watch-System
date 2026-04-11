from __future__ import annotations

import streamlit as st

from tracker.config import get_keywords, get_settings, get_source_registry


def _masked_value(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "未設定" if st.session_state.get("ui_language") == "zh-TW" else "Not set"
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["settings"])

    settings = get_settings()
    openai_enabled = bool(settings.openai_api_key)
    gemini_enabled = bool(settings.gemini_api_key)

    st.subheader("AI 設定檢查" if lang == "zh-TW" else "AI Configuration Check")
    with st.container(border=True):
        st.write(
            f"OpenAI API：{'已讀取' if openai_enabled else '未讀取'}"
            if lang == "zh-TW"
            else f"OpenAI API: {'Loaded' if openai_enabled else 'Not loaded'}"
        )
        st.caption(f"OPENAI_MODEL: {settings.openai_model}")
        st.caption(f"OPENAI_API_KEY: {_masked_value(settings.openai_api_key)}")

        st.write(
            f"Gemini API：{'已讀取' if gemini_enabled else '未讀取'}"
            if lang == "zh-TW"
            else f"Gemini API: {'Loaded' if gemini_enabled else 'Not loaded'}"
        )
        st.caption(f"GEMINI_MODEL: {settings.gemini_model}")
        st.caption(f"GEMINI_API_KEY: {_masked_value(settings.gemini_api_key)}")

        if not gemini_enabled:
            st.warning(
                "請在 Streamlit secrets 設定 GEMINI_API_KEY（鍵名必須完全一致）。" if lang == "zh-TW" else "Set GEMINI_API_KEY in Streamlit secrets (exact key name required)."
            )

    safe_settings = settings.model_dump()
    if safe_settings.get("openai_api_key"):
        safe_settings["openai_api_key"] = _masked_value(settings.openai_api_key)
    if safe_settings.get("gemini_api_key"):
        safe_settings["gemini_api_key"] = _masked_value(settings.gemini_api_key)

    st.subheader(labels["settings_yaml"])
    st.json(safe_settings)
    st.subheader(labels["keywords_yaml"])
    st.json(get_keywords())
    st.subheader(labels["source_registry_yaml"])
    st.json(get_source_registry())
