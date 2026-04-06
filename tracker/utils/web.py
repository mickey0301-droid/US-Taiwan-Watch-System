from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urljoin, urlparse


def absolute_url(base_url: str, href: str) -> str:
    return urljoin(base_url, href)


def domain_from_url(url: str) -> str:
    return urlparse(url).netloc.lower()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    candidates = [value.strip()]
    if value.endswith("Z"):
        candidates.append(value[:-1] + "+00:00")
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None


def build_google_news_rss_url(query: str, hl: str = "en-US", gl: str = "US", ceid: str = "US:en") -> str:
    encoded = quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl={hl}&gl={gl}&ceid={ceid}"


def build_cspan_search_url(query: str) -> str:
    encoded = quote_plus(query)
    return f"https://www.c-span.org/search/?searchtype=Videos&query={encoded}"
