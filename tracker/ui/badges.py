from __future__ import annotations

import html

import streamlit as st


def render_source_badges(source_type: str, source_url: str | None, lang: str) -> None:
    link_text = "查看來源" if lang == "zh-TW" else "Open source"
    badges = [
        {
            "text": source_type,
            "background": "#eef3ff",
            "foreground": "#274690",
            "href": None,
        }
    ]
    if source_url:
        badges.append(
            {
                "text": link_text,
                "background": "#f3f4f6",
                "foreground": "#374151",
                "href": source_url,
            }
        )

    html_parts: list[str] = []
    for badge in badges:
        chip = (
            f"<span style='display:inline-block;padding:0.22rem 0.6rem;border-radius:999px;"
            f"background:{badge['background']};color:{badge['foreground']};font-size:0.78rem;"
            f"font-weight:600;border:1px solid rgba(0,0,0,0.08);margin-right:0.4rem;margin-top:0.2rem;'>"
            f"{html.escape(badge['text'])}</span>"
        )
        if badge["href"]:
            chip = f"<a href='{html.escape(str(badge['href']), quote=True)}' target='_blank' style='text-decoration:none;'>{chip}</a>"
        html_parts.append(chip)

    st.markdown("".join(html_parts), unsafe_allow_html=True)
