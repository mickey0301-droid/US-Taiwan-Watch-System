from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx
import pandas as pd
import streamlit as st
from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import Legislation, LegislationSource
from tracker.utils.web import build_google_news_rss_url, parse_datetime


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


def _url_key(url: str | None) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    path = (parsed.path or "").rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}".lower().strip()


def _resolve_google_link(client: httpx.Client, url: str) -> str:
    link = str(url or "").strip()
    if not link:
        return ""
    if "news.google.com" not in link:
        return link
    try:
        response = client.get(link, follow_redirects=True, timeout=20.0)
        final_url = str(response.url or "").strip()
        if final_url and "news.google.com" not in final_url:
            return final_url
    except Exception:
        return link
    return link


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


def _existing_legislation_url_keys() -> set[str]:
    with session_scope() as session:
        bill_rows = session.execute(
            select(Legislation.source_url).where(
                Legislation.source_url.isnot(None),
                Legislation.source_url != "",
            )
        ).all()
        source_rows = session.execute(
            select(LegislationSource.source_url).where(
                LegislationSource.source_url.isnot(None),
                LegislationSource.source_url != "",
            )
        ).all()

    keys: set[str] = set()
    for rows in (bill_rows, source_rows):
        for (source_url,) in rows:
            key = _url_key(source_url)
            if key:
                keys.add(key)
    return keys


def _build_legislation_query(domain: str, keyword_expr: str, start_date: date, end_date: date) -> str:
    legislation_terms = "(bill OR legislation OR resolution OR act OR senate OR house OR assembly OR \"general assembly\")"
    # Google `before:` is treated as exclusive, so add one day to include end_date.
    exclusive_end = end_date + timedelta(days=1)
    return (
        f"({keyword_expr}) {legislation_terms} site:{domain} "
        f"after:{start_date.isoformat()} before:{exclusive_end.isoformat()}"
    )


def _collect_domain_rss_hits(
    domains: list[str],
    keyword_expr: str,
    start_date: date,
    end_date: date,
    max_results_per_domain: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    existing_keys = _existing_legislation_url_keys()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"
    }

    with httpx.Client(headers=headers) as client:
        for domain in domains:
            query = _build_legislation_query(domain=domain, keyword_expr=keyword_expr, start_date=start_date, end_date=end_date)
            rss_url = build_google_news_rss_url(query=query, hl="en-US", gl="US", ceid="US:en")
            try:
                response = client.get(rss_url, follow_redirects=True, timeout=25.0)
                response.raise_for_status()
                parsed = feedparser.parse(response.text)
            except Exception as exc:
                errors.append(f"{domain}: {exc}")
                continue

            for entry in parsed.entries[: max(1, int(max_results_per_domain))]:
                raw_link = str(getattr(entry, "link", "") or "").strip()
                resolved_url = _resolve_google_link(client, raw_link)
                url = resolved_url or raw_link
                key = _url_key(url) or _url_key(raw_link)
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)

                resolved_domain = _domain_from_url(url)
                if domain and resolved_domain and domain not in resolved_domain:
                    continue

                published_at = parse_datetime(getattr(entry, "published", None)) or parse_datetime(getattr(entry, "updated", None))
                if published_at:
                    published_date = published_at.date()
                    if published_date < start_date or published_date > end_date:
                        continue
                title = str(getattr(entry, "title", "") or "").strip()
                summary = str(getattr(entry, "summary", "") or getattr(entry, "description", "") or "").strip()
                rows.append(
                    {
                        "domain": domain,
                        "title": title or url,
                        "url": url,
                        "published_at": published_at,
                        "summary": summary,
                        "already_in_db": (key in existing_keys) if key else False,
                        "query": query,
                    }
                )

    rows.sort(key=lambda item: item.get("published_at") or parse_datetime("1970-01-01T00:00:00"), reverse=True)
    return rows, errors


def render(lang: str, labels: dict[str, str]) -> None:
    st.header(labels["sources_catalog"])
    st.caption(
        "列出事件與立法來源（名稱 + 網域網址），並提供州議會法案來源的 Taiwan 關鍵字搜尋（可設定起訖日期）。"
        if lang == "zh-TW"
        else "Lists event and legislation sources (name + domain URL), plus Taiwan-keyword search on state-legislation domains with configurable start/end dates."
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

    st.divider()
    st.subheader("州法案 Taiwan 搜尋" if lang == "zh-TW" else "State bill Taiwan search")
    st.caption(
        "以州議會來源網域為範圍，透過 Google News RSS 搜尋並回傳期間內結果。"
        if lang == "zh-TW"
        else "Searches within state-legislation source domains via Google News RSS and returns hits in the selected date range."
    )

    domain_options = [str(item["domain"]) for item in state_domains]
    default_domain_count = min(10, len(domain_options))

    with st.form("state_bill_source_search_form"):
        selected_domains = st.multiselect(
            "搜尋網域" if lang == "zh-TW" else "Domains",
            options=domain_options,
            default=domain_options[:default_domain_count],
        )
        keyword_expr = st.text_input(
            "關鍵字（可用 OR）" if lang == "zh-TW" else "Keywords (OR supported)",
            value="Taiwan OR 台灣 OR 臺灣",
        )
        col1, col2 = st.columns(2)
        default_end = date.today()
        default_start = default_end - timedelta(days=7)
        start_date = col1.date_input("開始日期" if lang == "zh-TW" else "Start date", value=default_start)
        end_date = col2.date_input("結束日期" if lang == "zh-TW" else "End date", value=default_end)
        max_results_per_domain = st.number_input(
            "每網域最多結果數" if lang == "zh-TW" else "Max results per domain",
            min_value=5,
            max_value=100,
            value=25,
            step=5,
        )
        submit_search = st.form_submit_button("開始搜尋" if lang == "zh-TW" else "Search")

    if submit_search:
        if not selected_domains:
            st.warning("請至少選一個網域。" if lang == "zh-TW" else "Please select at least one domain.")
            return
        if start_date > end_date:
            st.warning("開始日期不可晚於結束日期。" if lang == "zh-TW" else "Start date cannot be later than end date.")
            return
        with st.spinner("搜尋中..." if lang == "zh-TW" else "Searching..."):
            rows, errors = _collect_domain_rss_hits(
                domains=[str(item).strip().lower() for item in selected_domains if str(item).strip()],
                keyword_expr=str(keyword_expr or "Taiwan OR 台灣 OR 臺灣").strip(),
                start_date=start_date,
                end_date=end_date,
                max_results_per_domain=int(max_results_per_domain),
            )

        result_label = "搜尋結果" if lang == "zh-TW" else "Results"
        st.markdown(f"**{result_label}: {len(rows)}**")

        if errors:
            with st.expander("錯誤訊息" if lang == "zh-TW" else "Errors", expanded=False):
                for error in errors:
                    st.write(f"- {error}")

        if not rows:
            st.info("此日期區間內沒有找到符合條件的結果。" if lang == "zh-TW" else "No matching results found in this date range.")
            return

        output_rows = [
            {
                "網域" if lang == "zh-TW" else "Domain": row.get("domain"),
                "標題" if lang == "zh-TW" else "Title": row.get("title"),
                "網址" if lang == "zh-TW" else "URL": row.get("url"),
                "發布時間" if lang == "zh-TW" else "Published": (row.get("published_at").strftime("%Y-%m-%d %H:%M") if row.get("published_at") else ""),
                "已在法案庫" if lang == "zh-TW" else "Already in DB": "是" if (lang == "zh-TW" and row.get("already_in_db")) else ("否" if lang == "zh-TW" else bool(row.get("already_in_db"))),
            }
            for row in rows
        ]
        st.dataframe(pd.DataFrame(output_rows), use_container_width=True, hide_index=True)
