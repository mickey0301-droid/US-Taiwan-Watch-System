from __future__ import annotations

import streamlit as st

from tracker.config import get_keywords, get_settings, get_source_registry


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["settings"])
    st.subheader(labels["settings_yaml"])
    st.json(get_settings().model_dump())
    st.subheader(labels["keywords_yaml"])
    st.json(get_keywords())
    st.subheader(labels["source_registry_yaml"])
    st.json(get_source_registry())
