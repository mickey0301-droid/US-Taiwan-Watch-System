from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from tracker.collectors.federal_department_main_wikipedia import FEDERAL_DEPARTMENT_WIKIPEDIA_PAGES
from tracker.collectors.federal_department_units_wikipedia import (
    FEDERAL_DEPARTMENT_UNIT_PAGES,
    FederalDepartmentUnitsWikipediaCollector,
)
from tracker.logging_utils import get_logger


logger = get_logger(__name__)


RELATED_UNIT_KEYWORDS = (
    "bureau",
    "office",
    "administration",
    "agency",
    "command",
    "council",
    "board",
    "commission",
    "center",
    "centre",
    "institute",
)

EXCLUDED_LINK_KEYWORDS = (
    "department of ",
    "cabinet of the united states",
    "list of ",
    "history of ",
    "outline of ",
    "timeline of ",
    "organization of ",
    "seal of ",
    "flag of ",
    "inspector general",
    "general counsel",
    "civil service",
    "chief executive officer",
    "foreign aid",
    "office of strategic services",
    "sounding board",
    "student federal service",
)


class FederalDepartmentLinkedUnitsWikipediaCollector(FederalDepartmentUnitsWikipediaCollector):
    collector_name = "federal_department_linked_units_wikipedia"
    source_name = "Wikipedia linked federal department units"
    source_url = "https://en.wikipedia.org/wiki/Cabinet_of_the_United_States"
    parser_identity = "wikipedia_federal_department_linked_units_v1"

    def fetch(self) -> list[dict[str, str]]:
        existing_urls = {item["url"] for item in FEDERAL_DEPARTMENT_UNIT_PAGES}
        discovered: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for department in FEDERAL_DEPARTMENT_WIKIPEDIA_PAGES:
            page_url = department["url"]
            try:
                response = httpx.get(
                    page_url,
                    timeout=25.0,
                    follow_redirects=True,
                    trust_env=False,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    },
                )
                response.raise_for_status()
            except Exception:
                logger.exception("Failed to fetch department page for linked-unit discovery: %s", page_url)
                continue

            soup = BeautifulSoup(response.text, "lxml")
            content = soup.select_one("#mw-content-text .mw-parser-output") or soup
            anchors = content.select("a[href^='/wiki/']")
            for anchor in anchors:
                href = anchor.get("href", "")
                if not href or ":" in href.split("/wiki/")[-1]:
                    continue
                link_text = " ".join(anchor.get_text(" ", strip=True).split())
                normalized_text = link_text.lower()
                normalized_href = href.lower()
                if not link_text:
                    continue
                if any(keyword in normalized_text or keyword in normalized_href for keyword in EXCLUDED_LINK_KEYWORDS):
                    continue
                if not any(keyword in normalized_text for keyword in RELATED_UNIT_KEYWORDS):
                    continue

                full_url = urljoin(page_url, href.strip())
                if full_url in existing_urls or full_url in seen_urls or full_url == page_url:
                    continue

                discovered.append(
                    {
                        "department_name": department["department_name"],
                        "subdepartment_name": "Wikipedia linked units",
                        "unit_name": link_text,
                        "url": full_url,
                        "default_role_title": f"Lead official of {link_text}",
                    }
                )
                seen_urls.add(full_url)

        return discovered
