from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup


DEFAULT_SEED_URL = "https://en.wikipedia.org/wiki/Massachusetts_Senate"
DEFAULT_USER_AGENT = "USTaiwanWatchBot/1.0 (https://github.com/us-taiwan-watch; contact@example.com)"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover state legislature and government links from a Wikipedia state senate page."
    )
    parser.add_argument("--seed-url", default=DEFAULT_SEED_URL, help="Wikipedia state senate page URL to parse.")
    parser.add_argument(
        "--output",
        default="data/raw/state_legislature_links.json",
        help="Output JSON path (relative to repo root by default).",
    )
    args = parser.parse_args()

    links = discover_state_legislature_links(args.seed_url)
    government_links = discover_state_government_links(args.seed_url)
    links.extend(government_links)
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(links)} links to {output_path}")


def discover_state_legislature_links(seed_url: str) -> list[dict[str, Any]]:
    html = fetch_html(seed_url)
    soup = BeautifulSoup(html, "html.parser")
    navboxes = soup.select("div.navbox")
    target = None
    for nav in navboxes:
        title = nav.get_text(" ", strip=True)
        if "Legislatures of the United States" in title:
            target = nav
            break
    if not target:
        return []

    state_row = None
    for row in target.select("tr"):
        group = row.select_one(".navbox-group")
        if not group:
            continue
        if group.get_text(" ", strip=True) == "State legislatures":
            state_row = row
            break
    if not state_row:
        return []

    links: list[dict[str, Any]] = []
    current_state: str | None = None
    for anchor in state_row.select("a[href^='/wiki/']"):
        label = anchor.get_text(" ", strip=True)
        if not label:
            continue
        if label == "State legislatures":
            continue
        href = anchor.get("href", "")
        if not href:
            continue
        url = "https://en.wikipedia.org" + href
        chamber = None
        normalized = label.strip()
        if normalized in {"H", "House"}:
            chamber = "house"
        elif normalized in {"S", "Senate"}:
            chamber = "senate"
        elif normalized:
            current_state = normalized
        links.append(
            {
                "label": label,
                "state": current_state,
                "url": url,
                "chamber": chamber,
                "source_seed_url": seed_url,
            }
        )
    return dedupe_links(links)


def discover_state_government_links(seed_url: str) -> list[dict[str, Any]]:
    html = fetch_html(seed_url)
    soup = BeautifulSoup(html, "html.parser")
    navboxes = soup.select("div.navbox")
    target = None
    for nav in navboxes:
        title = nav.get_text(" ", strip=True)
        if "Government of" in title:
            target = nav
            break
    if not target:
        return []

    links: list[dict[str, Any]] = []
    for row in target.select("tr"):
        group = row.select_one(".navbox-group")
        if not group:
            continue
        group_label = group.get_text(" ", strip=True)
        if group_label not in {"Executive", "Judicial", "Independent agencies", "Law"}:
            continue
        for anchor in row.select("a[href^='/wiki/']"):
            label = anchor.get_text(" ", strip=True)
            if not label or label == "List":
                continue
            href = anchor.get("href", "")
            if not href:
                continue
            url = "https://en.wikipedia.org" + href
            links.append(
                {
                    "label": label,
                    "state": None,
                    "url": url,
                    "chamber": None,
                    "category": "government",
                    "group": group_label,
                    "source_seed_url": seed_url,
                }
            )
    return dedupe_links(links)


def fetch_html(url: str) -> str:
    response = httpx.get(
        url,
        timeout=30.0,
        follow_redirects=True,
        trust_env=False,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "From": "contact@example.com",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    response.raise_for_status()
    return response.text


def dedupe_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in links:
        key = item["url"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


if __name__ == "__main__":
    main()
