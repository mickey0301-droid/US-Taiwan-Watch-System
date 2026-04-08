from __future__ import annotations

from typing import Any

import streamlit as st

from tracker.utils.social import social_button_label, social_display_name


def render_social_links(profiles: Any, key_prefix: str) -> None:
    safe_profiles = profiles if isinstance(profiles, dict) else {}
    if not safe_profiles:
        return

    platforms = [(str(platform), str(url)) for platform, url in safe_profiles.items() if url]
    if not platforms:
        return

    columns = st.columns(len(platforms))
    for index, (platform, url) in enumerate(platforms):
        with columns[index]:
            st.link_button(
                social_button_label(platform),
                url,
                help=social_display_name(platform),
                use_container_width=True,
                type="secondary",
            )
