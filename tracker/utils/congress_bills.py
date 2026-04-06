from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


_BILL_MAPPINGS = [
    ("HCONRES", "house-concurrent-resolution"),
    ("SCONRES", "senate-concurrent-resolution"),
    ("HJRES", "house-joint-resolution"),
    ("SJRES", "senate-joint-resolution"),
    ("HRES", "house-resolution"),
    ("SRES", "senate-resolution"),
    ("HR", "house-bill"),
    ("S", "senate-bill"),
]


def normalize_bill_number(bill_number: str | None) -> str | None:
    if not bill_number:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(bill_number).upper())
    return cleaned or None


def parse_bill_number_parts(bill_number: str | None) -> tuple[str, int] | None:
    cleaned = normalize_bill_number(bill_number)
    if not cleaned:
        return None
    for prefix, _ in _BILL_MAPPINGS:
        if cleaned.startswith(prefix):
            number = cleaned[len(prefix) :]
            if number.isdigit():
                return prefix, int(number)
    return None


def congress_bill_url(congress: int | str | None, bill_number: str | None) -> str | None:
    if congress is None or not bill_number:
        return None
    try:
        congress_num = int(float(congress))
    except (TypeError, ValueError):
        return None

    cleaned = normalize_bill_number(bill_number)
    if not cleaned:
        return None

    for prefix, slug in _BILL_MAPPINGS:
        if cleaned.startswith(prefix):
            number = cleaned[len(prefix) :]
            if number.isdigit():
                return f"https://www.congress.gov/bill/{congress_num}th-congress/{slug}/{int(number)}"
    return None


def congress_from_url(url: str | None) -> int | None:
    if not url:
        return None
    match = re.search(r"/bill/(\d+)th-congress/", url) or re.search(r"/congress/bills/(\d+)/", url)
    if match:
        return int(match.group(1))
    return None


def canonical_congress_bill_page(url: str | None) -> str | None:
    if not url:
        return None
    normalized_url = url.replace("https://www.congress.gov/index.php/bill/", "https://www.congress.gov/bill/")
    match = re.search(r"(https://www\.congress\.gov/bill/\d+th-congress/[^/]+/\d+)", normalized_url)
    if match:
        return match.group(1)
    return None


def congress_bill_tab_url(url: str | None, tab: str) -> str | None:
    base = canonical_congress_bill_page(url)
    if not base:
        return None
    parsed = urlsplit(base)
    path = parsed.path.rstrip("/") + f"/{tab.strip('/')}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
