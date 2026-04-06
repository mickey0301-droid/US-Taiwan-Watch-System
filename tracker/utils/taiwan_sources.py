from __future__ import annotations

from urllib.parse import quote_plus


def build_taiwan_domain_rss_url(full_name: str, domain: str, chinese_name: str | None = None) -> str:
    english = f'"{full_name}"'
    chinese = f'OR "{chinese_name}"' if chinese_name else ""
    query = f"({english} {chinese}) (Taiwan OR 台灣 OR 訪台) site:{domain}"
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"


def build_taiwan_source_targets(full_name: str, chinese_name: str | None = None) -> list[dict[str, str]]:
    return [
        {
            "target_type": "rss_feed",
            "target_name": "president.gov.tw Taiwan",
            "target_url": build_taiwan_domain_rss_url(full_name, "president.gov.tw", chinese_name),
            "parser_identity": "taiwan_president_rss_v1",
        },
        {
            "target_type": "rss_feed",
            "target_name": "mofa.gov.tw Taiwan",
            "target_url": build_taiwan_domain_rss_url(full_name, "mofa.gov.tw", chinese_name),
            "parser_identity": "taiwan_mofa_rss_v1",
        },
        {
            "target_type": "rss_feed",
            "target_name": "cna.com.tw Taiwan",
            "target_url": build_taiwan_domain_rss_url(full_name, "cna.com.tw", chinese_name),
            "parser_identity": "taiwan_cna_rss_v1",
        },
    ]
