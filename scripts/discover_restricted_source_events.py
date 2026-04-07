from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup


USER_AGENT = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"
TAIWAN_KEYWORDS = ("台灣", "臺灣", "台海", "taiwan", "taipei")


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


def discover_cna(
    client: httpx.Client,
    insecure_client: httpx.Client,
    person_terms: list[str],
    start: date,
    end: date,
    limit: int = 80,
) -> list[EventHit]:
    urls: list[str] = []
    # Try all aliases instead of only the first term. CNA search relevance can
    # differ significantly between English and Chinese keywords.
    for search_term in list(dict.fromkeys([term.strip() for term in person_terms if term and term.strip()])):
        url = f"https://www.cna.com.tw/search/hysearchws.aspx?q={quote_plus(search_term)}"
        try:
            resp = _safe_get(client, insecure_client, url=url)
        except Exception:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            text = script.get_text(strip=True)
            if "ItemList" not in text:
                continue
            for match in re.findall(r"https://www\.cna\.com\.tw/news/[a-z0-9]+/\d+\.aspx", text, flags=re.I):
                if match not in urls:
                    urls.append(match)
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
        body = _clean_text(" ".join(node.get_text(" ", strip=True) for node in article.select("article p, .paragraph p, .paragraph")))
        merged = _clean_text(f"{title} {body}")
        if not (_contains_any(merged, person_terms) and _contains_any(merged, TAIWAN_KEYWORDS)):
            continue

        published = None
        date_match = re.search(r"/(\d{8})\.aspx(?:$|[?#])", link, flags=re.I)
        if date_match:
            try:
                published = datetime.strptime(date_match.group(1), "%Y%m%d").date()
            except ValueError:
                published = None
        # CNA links should always include YYYYMMDD in URL; skip malformed links
        if not published:
            continue
        if not _in_range(published, start, end):
            continue
        hits.append(
            EventHit(
                source="cna.com.tw",
                url=link,
                title=title or link,
                published_date=published.isoformat() if published else None,
                excerpt=body[:220],
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
            if not (_contains_any(merged, person_terms) and _contains_any(merged, TAIWAN_KEYWORDS)):
                continue
            hits.append(
                EventHit(
                    source="mofa.gov.tw",
                    url=full_url,
                    title=title or full_url,
                    published_date=published.isoformat() if published else None,
                    excerpt=body[:220],
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
            if not (_contains_any(merged, person_terms) and _contains_any(merged, TAIWAN_KEYWORDS)):
                continue
            hits.append(
                EventHit(
                    source="president.gov.tw",
                    url=full_url,
                    title=title or full_url,
                    published_date=published.isoformat() if published else None,
                    excerpt=body[:220],
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
    parser.add_argument("--person", required=True, help="Primary person keyword, e.g. Donald Trump")
    parser.add_argument("--aliases", default="川普,Trump,特朗普", help="Comma-separated aliases")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--months", default="4,3,2,1", help="Comma-separated months")
    parser.add_argument("--max-pages", type=int, default=40, help="Max listing pages for MOFA/President")
    args = parser.parse_args()

    months = [int(item.strip()) for item in str(args.months).split(",") if item.strip()]
    months = [m for m in months if 1 <= m <= 12]
    if not months:
        raise SystemExit("No valid months.")

    person_terms = [args.person.strip()] + [item.strip() for item in str(args.aliases).split(",") if item.strip()]
    person_terms = list(dict.fromkeys(person_terms))

    start, end = _month_bounds(args.year, months)
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        with httpx.Client(headers=headers, follow_redirects=True, verify=False) as insecure_client:
            cna_hits = discover_cna(client, insecure_client, person_terms=person_terms, start=start, end=end)
            mofa_hits = discover_mofa(client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=args.max_pages)
            president_hits = discover_president(client, insecure_client, person_terms=person_terms, start=start, end=end, max_pages=args.max_pages)

    all_hits = dedupe_hits(cna_hits + mofa_hits + president_hits)
    payload = {
        "person_terms": person_terms,
        "year": args.year,
        "months": sorted(months),
        "range_start": start.isoformat(),
        "range_end_exclusive": end.isoformat(),
        "counts": {
            "cna": len(cna_hits),
            "mofa": len(mofa_hits),
            "president": len(president_hits),
            "total": len(all_hits),
        },
        "hits": [hit.__dict__ for hit in all_hits],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
