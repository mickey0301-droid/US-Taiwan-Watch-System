from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup


DEFAULT_INPUT = "data/raw/state_legislature_links.json"
DEFAULT_OUTPUT = "data/raw/state_government_officials.json"
DEFAULT_USER_AGENT = "USTaiwanWatchBot/1.0 (https://github.com/us-taiwan-watch; contact@example.com)"
OFFICEHOLDER_HEADER_KEYWORDS = [
    "incumbent",
    "current officeholder",
    "current holder",
    "executive director",
    "director",
    "commissioner",
    "secretary",
    "superintendent",
    "administrator",
    "chief",
    "chair",
    "chairperson",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract state government officeholders from Wikipedia.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Input JSON (from discover_state_legislature_links_wikipedia).")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path.")
    parser.add_argument("--offset", type=int, default=0, help="Offset into government link list.")
    parser.add_argument("--limit", type=int, default=20, help="Max number of pages to process.")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = Path.cwd() / input_path
    data = json.loads(input_path.read_text(encoding="utf-8"))
    government_links = [item for item in data if item.get("category") == "government"]
    batch = government_links[args.offset : args.offset + args.limit]
    results = []
    for item in batch:
        result = extract_officeholder(item)
        if result:
            results.append(result)
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(results)} records to {output_path}")


def extract_officeholder(item: dict[str, Any]) -> dict[str, Any] | None:
    url = item.get("url")
    if not url:
        return None
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    infobox = soup.select_one("table.infobox")
    if not infobox:
        return None
    incumbent = None
    incumbent_url = None
    for row in infobox.select("tr"):
        header = row.find("th")
        if not header:
            continue
        header_text = header.get_text(" ", strip=True).lower()
        if not _is_officeholder_header(header_text):
            continue
        cell = row.find("td")
        if not cell:
            continue
        anchor = _select_person_anchor(cell.select("a[href]"))
        incumbent = cell.get_text(" ", strip=True)
        if anchor:
            anchor_text = anchor.get_text(" ", strip=True)
            incumbent = anchor_text or incumbent
            incumbent_url = "https://en.wikipedia.org" + anchor.get("href", "")
        break

    if not incumbent:
        incumbent, incumbent_url = _fallback_find_officeholder(infobox)
    if not incumbent:
        incumbent, incumbent_url = _fallback_find_officeholder_in_paragraphs(soup)
    incumbent = _clean_person_name(incumbent)
    if not _is_plausible_person_name(incumbent):
        return None
    if not incumbent:
        return None
    return {
        "office_label": item.get("label"),
        "office_url": url,
        "office_group": item.get("group"),
        "state": item.get("state"),
        "incumbent": incumbent,
        "incumbent_url": incumbent_url,
        "source_seed_url": item.get("source_seed_url"),
    }


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


def _fallback_find_officeholder(infobox: Any) -> tuple[str | None, str | None]:
    for row in infobox.select("tr"):
        text = row.get_text(" ", strip=True)
        lower_text = text.lower()
        if not _is_officeholder_header(lower_text):
            continue
        anchor = _select_person_anchor(row.select("a[href]"))
        if anchor:
            name = anchor.get_text(" ", strip=True)
            href = anchor.get("href", "")
            return name, "https://en.wikipedia.org" + href
        cleaned = text
        for keyword in OFFICEHOLDER_HEADER_KEYWORDS:
            pattern = re.compile(rf"^{re.escape(keyword)}\s*[:\-]?\s*", re.IGNORECASE)
            cleaned = pattern.sub("", cleaned).strip()
        cleaned = cleaned.replace("Incumbent", "").strip()
        if " since " in cleaned:
            cleaned = cleaned.split(" since ", 1)[0].strip()
        return cleaned or None, None
    return None, None


def _fallback_find_officeholder_in_paragraphs(soup: Any) -> tuple[str | None, str | None]:
    for paragraph in soup.select("p")[:8]:
        text = " ".join(paragraph.get_text(" ", strip=True).split())
        lower = text.lower()
        if not any(token in lower for token in ["director", "commissioner", "superintendent", "administrator", "headed by", "led by"]):
            continue
        # Prefer linked names in sentences that mention leadership roles.
        anchor = _select_person_anchor(paragraph.select("a[href]"))
        if anchor:
            return anchor.get_text(" ", strip=True), "https://en.wikipedia.org" + anchor.get("href", "")
        # Handle plain-text forms like "Jennifer Toth ... ADOT Director ..."
        match = re.search(r"\b([A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){1,3})\b.*\b(director|commissioner|superintendent|administrator)\b", text)
        if match:
            return match.group(1), None
        match = re.search(r"\b(?:headed by|led by)\s+(?:[A-Z][a-zA-Z.'-]+\s+){0,2}([A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){1,3})", text)
        if match:
            return match.group(1), None
    return None, None


def _select_person_anchor(anchors: list[Any]) -> Any | None:
    for anchor in anchors:
        href = anchor.get("href", "")
        if not href or href.startswith("#"):
            continue
        name = _clean_person_name(anchor.get_text(" ", strip=True))
        if _is_plausible_person_name(name):
            return anchor
    return None


def _clean_person_name(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = re.sub(r"\[[^\]]+\]", "", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.replace("citation needed", "").strip(" ,;:-")
    cleaned = re.sub(r"^(agency executives?|agency executive|executive|official)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(colonel|general|adm\.?|rear admiral|captain|dr\.?)\s+", "", cleaned, flags=re.IGNORECASE)
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[0].strip()
    cleaned = re.split(r"\b(?:director|deputy director|commissioner|secretary|superintendent|administrator|chief)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0].strip(" ,;:-")
    extracted = _extract_first_person_name(cleaned)
    return extracted or None


def _extract_first_person_name(text: str) -> str | None:
    if not text:
        return None
    match = re.search(r"\b([A-Z][a-zA-Z.'-]+(?:\s+[A-Z](?:\.)?)?(?:\s+[A-Z][a-zA-Z.'-]+){1,3})\b", text)
    if not match:
        return None
    return match.group(1).strip()


def _is_plausible_person_name(name: str | None) -> bool:
    if not name:
        return False
    if name.strip().lower() in {"citation needed", "unknown"}:
        return False
    if len(name.split()) < 2:
        return False
    lowered = name.lower()
    bad_tokens = [
        "phoenix",
        "arizona",
        "street",
        "avenue",
        "headquarters",
        "department",
        "board",
        "commission",
        "justice",
        "court",
    ]
    if any(token in lowered for token in bad_tokens):
        return False
    if re.search(r"\d", name):
        return False
    return True


def _is_officeholder_header(header_text: str) -> bool:
    normalized = (header_text or "").strip().lower()
    if not normalized:
        return False
    if "•" in normalized:
        return False
    blocked = [
        "secretary of state",
        "headquarters",
        "website",
        "formed",
        "jurisdiction",
        "budget",
    ]
    if any(token in normalized for token in blocked):
        return False
    return any(keyword in normalized for keyword in OFFICEHOLDER_HEADER_KEYWORDS)


if __name__ == "__main__":
    main()
