from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

try:
    import feedparser as _feedparser  # optional – used for Google News RSS layer
    _HAS_FEEDPARSER = True
except ImportError:
    _HAS_FEEDPARSER = False


USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"
TAIWAN_KEYWORDS = ("台灣", "臺灣", "台海", "taiwan", "taipei")
EXCERPT_MAX_LEN = 5000

# CNA's internal JSON API – returns paginated article listings by category.
# POST request; paginated via `pageidx`.
CNA_WNEWSLIST_URL = "https://www.cna.com.tw/cna2018api/api/WNewsList"
# Categories most relevant for US-officials × Taiwan stories.
CNA_RELEVANT_CATEGORIES = ("aopl", "acn", "aipl")  # international, cross-strait, politics
GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search"


@dataclass
class EventHit:
    source: str
    url: str
    title: str
    published_date: str | None
    excerpt: str


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any((needle or "").casefold() in lowered for needle in needles if needle)


# --- Pattern for timezone-only mentions ("台灣時間" / "臺灣時間" / "台灣標準時間") ---
_TAIWAN_TIMEZONE_RE = re.compile(r"[台臺]灣(?:標準)?時間", re.UNICODE)


def _contains_taiwan_substantive(text: str) -> bool:
    """Return True if `text` mentions Taiwan in a substantive way.

    Strips timezone-only phrases like "台灣時間 XX:XX" before checking, so an
    article whose only Taiwan reference is a timezone note is correctly rejected.
    """
    cleaned = _TAIWAN_TIMEZONE_RE.sub("", text)
    return _contains_any(cleaned, TAIWAN_KEYWORDS)


def _contains_person_substantive(text: str, person_terms: Iterable[str]) -> bool:
    lowered = text.casefold()
    for term in person_terms:
        candidate = (term or "").strip()
        if not candidate:
            continue
        if candidate in {"范斯", "範斯"}:
            if re.search(rf"{re.escape(candidate)}(?!高)", text):
                return True
            continue
        if candidate.casefold() == "vance":
            if "jd vance" in lowered or "j.d. vance" in lowered:
                return True
            continue
        if candidate.casefold() in lowered:
            return True
    return False


def _domain_from_url(value: str) -> str:
    host = urlparse(str(value or "")).netloc.lower()
    return host.removeprefix("www.")


def _entry_published_date(entry: object) -> date | None:
    published_struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if published_struct is None:
        return None
    try:
        return datetime(*published_struct[:6]).date()
    except Exception:
        return None


def _strip_google_source_suffix(title: str) -> str:
    return re.sub(r"\s+[-|]\s+[^-|]+$", "", str(title or "")).strip()


def _google_entry_summary_text(value: str) -> str:
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for source_label in soup.select("font"):
        source_label.decompose()
    return _clean_text(soup.get_text(" ", strip=True))


def _discover_google_news_report_hits(
    client: httpx.Client,
    person_terms: list[str],
    start: date,
    end: date,
    slice_days: int = 10,
    hl: str = "zh-TW",
    gl: str = "TW",
    ceid: str = "TW:zh-Hant",
) -> list[EventHit]:
    if not _HAS_FEEDPARSER:
        return []

    focused_terms = [term for term in person_terms if term and term.strip()][:6]
    if not focused_terms:
        return []

    urls_seen: set[str] = set()
    hits: list[EventHit] = []
    slice_size = max(1, min(31, int(slice_days or 10)))
    current = start
    while current < end:
        slice_end = min(current + timedelta(days=slice_size), end)
        for term in focused_terms:
            excluded = " -范斯高" if term in {"范斯", "範斯"} else ""
            q = (
                f'"{term}" (台灣 OR 臺灣 OR 台海 OR Taiwan OR "台灣議題")'
                f"{excluded} after:{current.isoformat()} before:{slice_end.isoformat()}"
            )
            rss_url = (
                f"{GOOGLE_NEWS_RSS_BASE}"
                f"?q={quote_plus(q)}&hl={quote_plus(hl)}&gl={quote_plus(gl)}&ceid={quote_plus(ceid)}"
            )
            try:
                resp = client.get(rss_url, timeout=25.0, follow_redirects=True)
                parsed = _feedparser.parse(resp.text)
            except Exception:
                continue

            for entry in getattr(parsed, "entries", []):
                raw_title = str(getattr(entry, "title", "") or "").strip()
                title = _strip_google_source_suffix(raw_title) or raw_title
                summary = _google_entry_summary_text(str(getattr(entry, "summary", "") or ""))
                text = _clean_text(f"{title} {summary}")
                if not _contains_person_substantive(text, person_terms):
                    continue
                if not _contains_taiwan_substantive(text):
                    continue
                published = _entry_published_date(entry)
                if not _in_range(published, start, end):
                    continue
                link = str(getattr(entry, "link", "") or "").strip()
                if not link or link in urls_seen:
                    continue
                urls_seen.add(link)
                source = getattr(entry, "source", None)
                source_href = str(getattr(source, "href", "") or "").strip()
                source_domain = _domain_from_url(source_href) or _domain_from_url(link) or "news.google.com"
                hits.append(
                    EventHit(
                        source=source_domain,
                        url=link,
                        title=title or link,
                        published_date=published.isoformat() if published else None,
                        excerpt=summary[:EXCERPT_MAX_LEN],
                    )
                )
        current = slice_end
        time.sleep(0.2)

    return dedupe_hits(hits)


def _parse_ymd(value: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _month_bounds(year: int, months: list[int]) -> tuple[date, date]:
    min_month = min(months)
    max_month = max(months)
    start = date(year, min_month, 1)
    if max_month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, max_month + 1, 1)
    return start, end


def _in_range(published: date | None, start: date, end: date) -> bool:
    if not published:
        return True
    return start <= published < end


def _safe_get(client: httpx.Client, insecure_client: httpx.Client, url: str, timeout: float = 30.0) -> httpx.Response:
    try:
        response = client.get(url, timeout=timeout)
        response.raise_for_status()
        return response
    except httpx.ConnectError as exc:
        message = str(exc).lower()
        if "certificate verify failed" not in message:
            raise
        response = insecure_client.get(url, timeout=timeout)
        response.raise_for_status()
        return response


def _discover_cna_urls_via_wnewslist(
    client: httpx.Client,
    person_terms: list[str],
    start: date,
    end: date,
    categories: tuple[str, ...] = CNA_RELEVANT_CATEGORIES,
    max_pages: int = 40,
) -> list[str]:
    """Collect CNA article URLs using the WNewsList JSON API.

    Unlike the HTML search page (which returns only ~20 results), this API
    supports proper pagination via ``pageidx``.  We paginate until the oldest
    article on a page predates ``start`` or until ``max_pages`` is exhausted.

    Each item in the API response includes the headline, so we can quickly
    pre-filter by person name *without* fetching the full article.

    Best suited for lookback windows up to ~90 days.  For longer ranges use
    ``_discover_cna_urls_via_google_rss`` instead.
    """
    person_lower = [t.casefold() for t in person_terms if t]
    urls: list[str] = []
    seen: set[str] = set()

    for category in categories:
        for page in range(1, max_pages + 1):
            body = {
                "action": "0",
                "category": category,
                "tno": "",
                "pagesize": 20,
                "pageidx": page,
            }
            try:
                resp = client.post(CNA_WNEWSLIST_URL, json=body, timeout=30.0)
                resp.raise_for_status()
                data = resp.json()
                items: list[dict] = data.get("ResultData", {}).get("Items", []) or []
            except Exception:
                break
            if not items:
                break

            oldest_on_page: date | None = None
            for item in items:
                page_url = str(item.get("PageUrl", "") or "").strip()
                headline = str(item.get("HeadLine", "") or "").strip()
                if not page_url:
                    continue
                full_url = page_url if page_url.startswith("http") else f"https://www.cna.com.tw{page_url}"
                # Extract date from URL pattern /YYYYMMDDNNNN.aspx
                dm = re.search(r"/(\d{8})\d*\.aspx", full_url, flags=re.I)
                pub: date | None = None
                if dm:
                    try:
                        pub = datetime.strptime(dm.group(1), "%Y%m%d").date()
                    except ValueError:
                        pass
                if pub and (oldest_on_page is None or pub < oldest_on_page):
                    oldest_on_page = pub
                if not _in_range(pub, start, end):
                    continue
                # Pre-filter: headline must mention at least one person term
                headline_lower = headline.casefold()
                if person_lower and not any(t in headline_lower for t in person_lower):
                    continue
                if full_url not in seen:
                    seen.add(full_url)
                    urls.append(full_url)

            # Early exit once we've passed the start of the window
            if oldest_on_page and oldest_on_page < start:
                break
            time.sleep(0.3)

    return urls


def _discover_cna_urls_via_google_rss(
    client: httpx.Client,
    person_terms: list[str],
    start: date,
    end: date,
    hl: str = "zh-TW",
    gl: str = "TW",
    ceid: str = "TW:zh-Hant",
) -> list[str]:
    """Collect CNA article URLs using Google News RSS with per-month date slicing.

    Google News RSS is limited to ~100 results per query, so by splitting the
    365-day window into monthly slices we can recover up to ~1 200 results total.

    Requires ``feedparser`` to be installed.  Silently returns [] if not available.
    """
    if not _HAS_FEEDPARSER:
        return []

    # Build the OR-clause for person terms.  Limit to 4 to avoid URL length issues.
    quoted = [f'"{t}"' for t in person_terms[:4] if t]
    if not quoted:
        return []
    person_q = " OR ".join(quoted)
    taiwan_q = "(台灣 OR 臺灣 OR Taiwan)"

    urls: list[str] = []
    seen: set[str] = set()

    # Iterate month by month inside the date window.
    current = date(start.year, start.month, 1)
    while current < end:
        if current.month == 12:
            next_month = date(current.year + 1, 1, 1)
        else:
            next_month = date(current.year, current.month + 1, 1)
        slice_end = min(next_month, end + timedelta(days=1))

        q = (
            f"site:cna.com.tw ({person_q}) {taiwan_q} "
            f"after:{current.isoformat()} before:{slice_end.isoformat()}"
        )
        rss_url = (
            f"{GOOGLE_NEWS_RSS_BASE}"
            f"?q={quote_plus(q)}&hl={quote_plus(hl)}&gl={quote_plus(gl)}&ceid={quote_plus(ceid)}"
        )
        try:
            resp = client.get(rss_url, timeout=25.0, follow_redirects=True)
            parsed = _feedparser.parse(resp.text)
        except Exception:
            try:
                parsed = _feedparser.parse(rss_url)
            except Exception:
                current = next_month
                continue

        for entry in getattr(parsed, "entries", []):
            raw_link = str(getattr(entry, "link", "") or "").strip()
            # Google RSS often returns opaque news.google.com wrappers. Do not
            # chase them here; the broader Google News layer keeps those as
            # media report hits. This CNA layer only accepts real CNA URLs.
            if "cna.com.tw/news/" in raw_link and raw_link not in seen:
                seen.add(raw_link)
                urls.append(raw_link)

        current = next_month
        time.sleep(0.5)  # be polite to Google

    return urls


def discover_cna(
    client: httpx.Client,
    insecure_client: httpx.Client,
    person_terms: list[str],
    start: date,
    end: date,
    limit: int = 300,
    require_taiwan_keyword: bool = True,
    require_dated_url: bool = True,
    use_wnewslist: bool = True,
    use_google_rss: bool = True,
    wnewslist_max_pages: int = 40,
) -> list[EventHit]:
    # ── Layer A: CNA hysearchws HTML search ────────────────────────────────────
    # Returns ~20 results per query (no server-side pagination in the URL).
    # High relevance but limited recall for long time windows.
    urls: list[str] = []
    seen_urls: set[str] = set()

    def _add_url(u: str) -> None:
        if u and u not in seen_urls:
            seen_urls.add(u)
            urls.append(u)

    # Try all aliases instead of only the first term. CNA search relevance can
    # differ significantly between English and Chinese keywords.
    base_terms = list(dict.fromkeys([term.strip() for term in person_terms if term and term.strip()]))
    search_terms = list(base_terms)
    # CNA index can miss person-only searches. Add person + Taiwan-term
    # queries to recover relevant records that are otherwise hidden.
    for base in base_terms:
        for taiwan_term in ("台灣", "臺灣"):
            combined = f"{base} {taiwan_term}".strip()
            if combined and combined not in search_terms:
                search_terms.append(combined)

    for search_term in search_terms:
        url = f"https://www.cna.com.tw/search/hysearchws.aspx?q={quote_plus(search_term)}"
        resp: httpx.Response | None = None
        for attempt in range(3):
            try:
                candidate = client.get(url, timeout=30.0)
            except Exception:
                candidate = None
                # Keep legacy certificate fallback behavior.
                try:
                    candidate = _safe_get(client, insecure_client, url=url)
                except Exception:
                    candidate = None
            if candidate is None:
                continue
            if candidate.status_code == 429:
                time.sleep(1.0 + attempt * 1.5)
                continue
            if candidate.status_code >= 400:
                candidate = None
                continue
            resp = candidate
            break
        if resp is None:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        # CNA search HTML keeps result links in multiple places (relative href,
        # JSON-LD, and inline JSON). Collect from all patterns.
        for match in re.findall(r"/news/[a-z0-9]+/\d+\.aspx", resp.text, flags=re.I):
            full = f"https://www.cna.com.tw{match}" if match.startswith("/") else match
            _add_url(full)
        for match in re.findall(r"https://www\.cna\.com\.tw/news/[a-z0-9]+/\d+\.aspx", resp.text, flags=re.I):
            _add_url(match)
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            text = script.get_text(strip=True)
            for match in re.findall(r"https://www\.cna\.com\.tw/news/[a-z0-9]+/\d+\.aspx", text, flags=re.I):
                _add_url(match)

    # ── Layer B: CNA WNewsList JSON API ────────────────────────────────────────
    # Paginated category listing that gives far more recall than the HTML search.
    # Most effective for shorter lookback windows (≤ 90 days); for longer windows
    # the page count becomes excessive so we skip it.
    lookback_days = (end - start).days
    if use_wnewslist and lookback_days <= 90:
        try:
            for u in _discover_cna_urls_via_wnewslist(
                client,
                person_terms=base_terms,
                start=start,
                end=end,
                max_pages=wnewslist_max_pages,
            ):
                _add_url(u)
        except Exception:
            pass

    # ── Layer C: Google News RSS with monthly date slices ──────────────────────
    # Each month yields up to 100 results; 12 months = up to 1 200 for a full year.
    # Most effective for longer lookback windows where the WNewsList API is too slow.
    if use_google_rss and lookback_days > 14:
        try:
            for u in _discover_cna_urls_via_google_rss(
                client,
                person_terms=base_terms,
                start=start,
                end=end,
            ):
                _add_url(u)
        except Exception:
            pass

    # ── Fetch and filter each candidate URL ────────────────────────────────────
    hits: list[EventHit] = []
    for link in urls[:limit]:
        try:
            article_resp = _safe_get(client, insecure_client, url=link)
        except Exception:
            continue
        article = BeautifulSoup(article_resp.text, "html.parser")
        title = _clean_text(
            (article.select_one("meta[property='og:title']") or {}).get("content", "")
            or (article.select_one("h1") or {}).get_text(" ", strip=True)
        )
        # Strip all <a> anchor text before extracting body.
        # This prevents false positives where the person is mentioned only in
        # "related articles" sidebar links at the bottom of a CNA article.
        for a_tag in article.select("article a, .paragraph a"):
            a_tag.decompose()
        body = _clean_text(" ".join(node.get_text(" ", strip=True) for node in article.select("article p, .paragraph p, .paragraph")))
        merged = _clean_text(f"{title} {body}")
        # Person must appear in title OR in non-link body text.
        if not _contains_person_substantive(title, person_terms) and not _contains_person_substantive(body, person_terms):
            continue
        # Use substantive-Taiwan check: strips "台灣時間" before evaluating so
        # articles that only mention the Taiwan timezone are correctly rejected.
        if require_taiwan_keyword and not _contains_taiwan_substantive(merged):
            continue

        published = None
        # CNA IDs are commonly 12 digits now (YYYYMMDD + serial).
        date_match = re.search(r"/(\d{8})\d*\.aspx(?:$|[?#])", link, flags=re.I)
        if date_match:
            try:
                published = datetime.strptime(date_match.group(1), "%Y%m%d").date()
            except ValueError:
                published = None
        # Strict mode requires date in CNA URL; legacy mode can keep undated hits.
        if require_dated_url and not published:
            continue
        if not _in_range(published, start, end):
            continue
        hits.append(
            EventHit(
                source="cna.com.tw",
                url=link,
                title=title or link,
                published_date=published.isoformat() if published else None,
                excerpt=body[:EXCERPT_MAX_LEN],
            )
        )
    return hits


def discover_mofa(
    client: httpx.Client,
    insecure_client: httpx.Client,
    person_terms: list[str],
    start: date,
    end: date,
    max_pages: int = 30,
    require_taiwan_keyword: bool = True,
) -> list[EventHit]:
    hits: list[EventHit] = []
    for page in range(1, max_pages + 1):
        url = f"https://www.mofa.gov.tw/News.aspx?n=95&sms=73&page={page}&PageSize=20"
        try:
            resp = _safe_get(client, insecure_client, url=url)
        except Exception:
            break
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("tr")
        if not rows:
            break

        oldest_on_page: date | None = None
        for row in rows:
            anchor = row.select_one("a[href*='News_Content.aspx']")
            if not anchor:
                continue
            href = anchor.get("href", "").strip()
            if not href:
                continue
            full_url = href if href.startswith("http") else f"https://www.mofa.gov.tw/{href.lstrip('/')}"
            title = _clean_text(anchor.get_text(" ", strip=True))
            date_cell = row.select_one("td.is-center span")
            published = _parse_ymd(_clean_text(date_cell.get_text(" ", strip=True) if date_cell else ""))
            if published and (oldest_on_page is None or published < oldest_on_page):
                oldest_on_page = published
            if not _in_range(published, start, end):
                continue

            try:
                detail_resp = _safe_get(client, insecure_client, url=full_url)
            except Exception:
                continue
            detail = BeautifulSoup(detail_resp.text, "html.parser")
            body = _clean_text(" ".join(node.get_text(" ", strip=True) for node in detail.select(".page-content p, .cp p, article p, .editor p")))
            merged = _clean_text(f"{title} {body}")
            if not _contains_any(merged, person_terms):
                continue
            if require_taiwan_keyword and not _contains_any(merged, TAIWAN_KEYWORDS):
                continue
            hits.append(
                EventHit(
                    source="mofa.gov.tw",
                    url=full_url,
                    title=title or full_url,
                    published_date=published.isoformat() if published else None,
                    excerpt=body[:EXCERPT_MAX_LEN],
                )
            )
        if oldest_on_page and oldest_on_page < start:
            break
    return hits


def discover_president(
    client: httpx.Client,
    insecure_client: httpx.Client,
    person_terms: list[str],
    start: date,
    end: date,
    max_pages: int = 30,
    require_taiwan_keyword: bool = True,
) -> list[EventHit]:
    hits: list[EventHit] = []
    for detailno in range(1, max_pages + 1):
        page_suffix = "" if detailno == 1 else f"?detailno={detailno}"
        url = f"https://www.president.gov.tw/Page/35{page_suffix}"
        try:
            resp = _safe_get(client, insecure_client, url=url)
        except Exception:
            break
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".newsList .item, .newsList .listItem, li")
        if not cards:
            continue

        oldest_on_page: date | None = None
        for card in cards:
            anchor = card.select_one("a.moreBtn[href*='/NEWS/'], a[href*='/NEWS/'][title]")
            if not anchor:
                continue
            href = anchor.get("href", "").strip()
            if not href:
                continue
            full_url = href if href.startswith("http") else f"https://www.president.gov.tw/{href.lstrip('/')}"
            title = _clean_text(anchor.get("title", "") or anchor.get_text(" ", strip=True))
            date_node = card.select_one(".date")
            date_text = _clean_text(date_node.get_text(" ", strip=True) if date_node else "")
            published = _parse_ymd(date_text.replace(".", "-").replace("/", "-"))
            if published and (oldest_on_page is None or published < oldest_on_page):
                oldest_on_page = published
            if not _in_range(published, start, end):
                continue

            try:
                detail_resp = _safe_get(client, insecure_client, url=full_url)
            except Exception:
                continue
            detail = BeautifulSoup(detail_resp.text, "html.parser")
            body = _clean_text(" ".join(node.get_text(" ", strip=True) for node in detail.select(".article p, .con p, article p, .news p")))
            merged = _clean_text(f"{title} {body}")
            if not _contains_any(merged, person_terms):
                continue
            if require_taiwan_keyword and not _contains_any(merged, TAIWAN_KEYWORDS):
                continue
            hits.append(
                EventHit(
                    source="president.gov.tw",
                    url=full_url,
                    title=title or full_url,
                    published_date=published.isoformat() if published else None,
                    excerpt=body[:EXCERPT_MAX_LEN],
                )
            )
        if oldest_on_page and oldest_on_page < start:
            break
    return hits


def dedupe_hits(hits: list[EventHit]) -> list[EventHit]:
    seen: set[str] = set()
    output: list[EventHit] = []
    for hit in sorted(hits, key=lambda x: (x.published_date or "", x.source, x.url), reverse=True):
        key = hit.url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        output.append(hit)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover person+Taiwan events from CNA / MOFA / President Office directly (no Google News).")
    parser.add_argument("--person", required=True, help="Primary person keyword, e.g. 'JD Vance'")
    parser.add_argument("--aliases", default="范斯,Vance", help="Comma-separated aliases (include Chinese)")
    parser.add_argument("--year", type=int, required=False)
    parser.add_argument("--months", default="4,3,2,1", help="Comma-separated months")
    parser.add_argument("--max-pages", type=int, default=40, help="Max listing pages for MOFA/President")
    parser.add_argument("--lookback-days", type=int, default=0,
                        help="Use a rolling N-day window instead of --year/--months (0 = use year/months)")
    parser.add_argument("--start-date", default="", help="Fixed range start date, YYYY-MM-DD")
    parser.add_argument("--end-date", default="", help="Fixed range end date, YYYY-MM-DD, inclusive")
    parser.add_argument("--no-wnewslist", action="store_true", help="Disable CNA WNewsList API layer")
    parser.add_argument("--no-google-rss", action="store_true", help="Disable Google News RSS layer")
    parser.add_argument("--news-only", action="store_true", help="Only run broad Google News media-report discovery")
    parser.add_argument("--slice-days", type=int, default=10, help="Google News date-slice size in days")
    args = parser.parse_args()

    fixed_start = _parse_ymd(str(args.start_date).strip()) if str(args.start_date or "").strip() else None
    fixed_end = _parse_ymd(str(args.end_date).strip()) if str(args.end_date or "").strip() else None
    if fixed_start or fixed_end:
        end = (fixed_end + timedelta(days=1)) if fixed_end else date.today() + timedelta(days=1)
        start = fixed_start or (end - timedelta(days=max(1, args.lookback_days or 30)))
        if end <= start:
            raise SystemExit("--end-date must be on or after --start-date")
    elif args.lookback_days > 0:
        end = date.today() + timedelta(days=1)
        start = end - timedelta(days=args.lookback_days)
    else:
        if not args.year:
            raise SystemExit("Must provide --year, --lookback-days, or --start-date/--end-date")
        months = [int(item.strip()) for item in str(args.months).split(",") if item.strip()]
        months = [m for m in months if 1 <= m <= 12]
        if not months:
            raise SystemExit("No valid months.")
        start, end = _month_bounds(args.year, months)

    person_terms = [args.person.strip()] + [item.strip() for item in str(args.aliases).split(",") if item.strip()]
    person_terms = list(dict.fromkeys(person_terms))

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        with httpx.Client(headers=headers, follow_redirects=True, verify=False) as insecure_client:
            if args.news_only:
                cna_hits = []
                mofa_hits = []
                president_hits = []
            else:
                cna_hits = discover_cna(
                    client, insecure_client,
                    person_terms=person_terms,
                    start=start, end=end,
                    use_wnewslist=not args.no_wnewslist,
                    use_google_rss=False,
                )
                mofa_hits = discover_mofa(client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=args.max_pages)
                president_hits = discover_president(client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=args.max_pages)
            news_hits = [] if args.no_google_rss else _discover_google_news_report_hits(
                client,
                person_terms=person_terms,
                start=start,
                end=end,
                slice_days=args.slice_days,
            )

    all_hits = dedupe_hits(cna_hits + mofa_hits + president_hits + news_hits)
    payload = {
        "person_terms": person_terms,
        "range_start": start.isoformat(),
        "range_end_exclusive": end.isoformat(),
        "counts": {
            "cna": len(cna_hits),
            "mofa": len(mofa_hits),
            "president": len(president_hits),
            "google_news": len(news_hits),
            "total": len(all_hits),
        },
        "hits": [hit.__dict__ for hit in all_hits],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
