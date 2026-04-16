from __future__ import annotations

from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import Legislation, LegislationSource


EVENT_SOURCES = [
    {"name_zh": "中央社", "name_en": "Central News Agency (CNA)", "domain": "cna.com.tw"},
    {"name_zh": "總統府", "name_en": "Office of the President, ROC (Taiwan)", "domain": "president.gov.tw"},
    {"name_zh": "外交部", "name_en": "Ministry of Foreign Affairs, ROC (Taiwan)", "domain": "mofa.gov.tw"},
]


def _domain_from_url(url: str | None) -> str:
    parsed = urlparse(str(url or "").strip())
    domain = (parsed.netloc or "").lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _read_state_legislation_domains() -> list[dict[str, object]]:
    with session_scope() as session:
        bill_rows = session.execute(
            select(Legislation.source_url).where(
                Legislation.level == "state",
                Legislation.source_url.isnot(None),
                Legislation.source_url != "",
            )
        ).all()
        source_rows = session.execute(
            select(LegislationSource.source_url)
            .join(Legislation, Legislation.id == LegislationSource.legislation_id)
            .where(
                Legislation.level == "state",
                LegislationSource.source_url.isnot(None),
                LegislationSource.source_url != "",
            )
        ).all()

    counter: dict[str, dict[str, object]] = {}
    for rows in (bill_rows, source_rows):
        for (source_url,) in rows:
            url = str(source_url or "").strip()
            domain = _domain_from_url(url)
            if not domain:
                continue
            item = counter.get(domain)
            if item is None:
                counter[domain] = {
                    "domain": domain,
                    "count": 1,
                    "sample_url": url,
                }
            else:
                item["count"] = int(item["count"]) + 1

    return sorted(counter.values(), key=lambda item: (-int(item["count"]), str(item["domain"])))


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["sources_catalog"])
    st.caption(
        "列出事件與立法來源（名稱 + 網域網址），立法來源會依現有州議會法案資料自動歸納。"
        if lang == "zh-TW"
        else "Lists event and legislation sources (name + domain URL). State-legislation sources are derived from existing records."
    )

    st.subheader("事件來源" if lang == "zh-TW" else "Event sources")
    event_rows = [
        {
            "名稱" if lang == "zh-TW" else "Name": item["name_zh"] if lang == "zh-TW" else item["name_en"],
            "網域網址" if lang == "zh-TW" else "Domain URL": f"https://{item['domain']}",
        }
        for item in EVENT_SOURCES
    ]
    st.dataframe(pd.DataFrame(event_rows), use_container_width=True, hide_index=True)

    st.subheader("立法來源" if lang == "zh-TW" else "Legislation sources")
    federal_label = "美國國會法案官方來源" if lang == "zh-TW" else "U.S. federal official source"
    st.markdown(
        f"- **Congress.gov**: [https://www.congress.gov](https://www.congress.gov)  \\\n  {'名稱' if lang == 'zh-TW' else 'Name'}: {federal_label}"
    )

    state_domains = _read_state_legislation_domains()
    if not state_domains:
        st.info("目前尚無州議會法案來源可歸納。" if lang == "zh-TW" else "No state-legislation source domains available yet.")
        return

    st.markdown("**州議會法案可用來源（依現有法案資料歸納）**" if lang == "zh-TW" else "**State bill source domains (derived from existing records)**")
    state_rows = [
        {
            "名稱" if lang == "zh-TW" else "Name": "州議會法案來源",
            "網域網址" if lang == "zh-TW" else "Domain URL": f"https://{item['domain']}",
            "法案來源筆數" if lang == "zh-TW" else "Source records": int(item["count"]),
            "範例網址" if lang == "zh-TW" else "Sample URL": str(item["sample_url"]),
        }
        for item in state_domains
    ]
    st.dataframe(pd.DataFrame(state_rows), use_container_width=True, hide_index=True)
