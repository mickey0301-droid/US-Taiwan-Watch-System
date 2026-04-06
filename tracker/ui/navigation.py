from __future__ import annotations

import streamlit as st


def person_detail_href(person_id: int) -> str:
    return f"?page=person_detail&person_id={int(person_id)}"


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
                st.markdown(f"[{display_name}]({person_detail_href(int(person_id))})")
            else:
                st.caption(display_name)
