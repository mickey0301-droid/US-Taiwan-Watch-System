from __future__ import annotations

from html import escape
from urllib.parse import quote_plus

import streamlit as st


def person_detail_href(person_id: int, display_name: str | None = None) -> str:
    href = f"?page=person_detail&person_id={int(person_id)}"
    if display_name:
        href += f"&person_name={quote_plus(str(display_name).strip())}"
    return href


def person_detail_anchor_html(label: str, person_id: int) -> str:
    href = person_detail_href(int(person_id), label)
    text = escape(str(label or "").strip())
    return f'<a href="{href}" target="_self">{text}</a>'


def render_person_links(
    participants: list[dict[str, object]],
    lang: str,
    key_prefix: str,
) -> None:
    if not participants:
        st.write("未提供" if lang == "zh-TW" else "Not available")
        return

    columns = st.columns(min(3, len(participants)))
    for index, participant in enumerate(participants):
        display_name = str(participant.get("display_name") or "").strip()
        person_id = participant.get("person_id")
        target_column = columns[index % len(columns)]
        with target_column:
            if person_id:
                st.markdown(person_detail_anchor_html(display_name, int(person_id)), unsafe_allow_html=True)
            else:
                st.caption(display_name)
