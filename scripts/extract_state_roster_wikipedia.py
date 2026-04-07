from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from scripts.extract_state_government_officials_wikipedia import extract_officeholder


DEFAULT_LINKS = "data/raw/state_legislature_links.json"
DEFAULT_USER_AGENT = "USTaiwanWatchBot/1.0 (https://github.com/us-taiwan-watch; contact@example.com)"
MASSACHUSETTS_HOUSE_TARGET = 160
STATE_ROSTER_OVERRIDES: dict[str, dict[str, Any]] = {
    "district of columbia": {
        "government_url": "https://en.wikipedia.org/wiki/Government_of_the_District_of_Columbia",
        "senate_url": "https://en.wikipedia.org/wiki/Council_of_the_District_of_Columbia",
        "house_url": "https://en.wikipedia.org/wiki/Council_of_the_District_of_Columbia",
        "unicameral": True,
    },
    "nebraska": {
        "senate_url": "https://en.wikipedia.org/wiki/Nebraska_Legislature",
        "house_url": "https://en.wikipedia.org/wiki/Nebraska_Legislature",
        "unicameral": True,
    },
    "guam": {
        "senate_url": "https://en.wikipedia.org/wiki/Legislature_of_Guam",
        "house_url": "https://en.wikipedia.org/wiki/Legislature_of_Guam",
        "unicameral": True,
    },
    "puerto rico": {
        "senate_url": "https://en.wikipedia.org/wiki/Senate_of_Puerto_Rico",
        "house_url": "https://en.wikipedia.org/wiki/House_of_Representatives_of_Puerto_Rico",
        "government_url": "https://en.wikipedia.org/wiki/Legislative_Assembly_of_Puerto_Rico",
    },
    "minnesota": {
        "house_url": "https://en.wikipedia.org/wiki/Minnesota_House_of_Representatives",
    },
    "oregon": {
        "senate_url": "https://en.wikipedia.org/wiki/Oregon_State_Senate",
    },
}
STATE_GOVERNMENT_LINK_RULES: dict[str, dict[str, set[str]]] = {
    "arizona": {
        "allow_keywords": {
            "department",
            "board",
            "commission",
            "authority",
            "agency",
            "office",
            "court",
        },
        "deny_contains": {
            "redistricting",
            "supreme court",
            "court of appeals",
            "superior court",
            "courts of arizona",
        },
        "deny_exact": {
            "corporation commissioner",
        },
    }
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract a state roster (officials + legislators) from Wikipedia.")
    parser.add_argument("--state", required=True, help="State name (e.g., Massachusetts).")
    parser.add_argument("--links", default=DEFAULT_LINKS, help="Links JSON from discover_state_legislature_links_wikipedia.")
    parser.add_argument("--senate-url", default="", help="Override senate URL.")
    parser.add_argument("--house-url", default="", help="Override house URL.")
    parser.add_argument(
        "--government-url",
        default="",
        help="Override government page URL (e.g., https://en.wikipedia.org/wiki/Government_of_Arizona).",
    )
    parser.add_argument("--debug", action="store_true", help="Print debug info.")
    parser.add_argument("--output", default="", help="Output JSON path.")
    args = parser.parse_args()
    state_key = args.state.strip().lower()
    override = STATE_ROSTER_OVERRIDES.get(state_key, {})

    links_path = Path(args.links)
    if not links_path.is_absolute():
        links_path = Path.cwd() / links_path
    links = json.loads(links_path.read_text(encoding="utf-8"))

    senate_url = args.senate_url or override.get("senate_url") or find_state_link(links, args.state, "senate")
    house_url = args.house_url or override.get("house_url") or find_state_link(links, args.state, "house")
    if not senate_url:
        senate_url = guess_state_url(args.state, "Senate")
    if not house_url:
        house_url = guess_state_url(args.state, "House_of_Representatives")
    if not senate_url or not house_url:
        raise SystemExit("Missing senate/house URL. Provide --senate-url/--house-url if lookup fails.")

    government_seed_urls = build_government_seed_urls(
        args.state,
        senate_url,
        args.government_url or override.get("government_url", ""),
    )
    government_links = [
        item
        for item in links
        if item.get("category") == "government" and (item.get("source_seed_url") in government_seed_urls)
    ]
    if not government_links:
        for government_seed_url in government_seed_urls:
            government_links = discover_government_links_from_page(government_seed_url)
            if government_links:
                break
    if args.debug:
        print(f"[debug] government_seed_urls={government_seed_urls}")
        print(f"[debug] government_links={len(government_links)}")
    officials = []
    for item in government_links:
        result = extract_officeholder(item)
        if result:
            officials.append(result)
    if args.debug:
        print(f"[debug] officials_from_government_links={len(officials)}")
    if not officials:
        for government_seed_url in government_seed_urls:
            department_links = discover_department_links_from_government_page(government_seed_url, args.state)
            if args.debug:
                print(f"[debug] department_links_from_{government_seed_url}={len(department_links)}")
            if not department_links:
                continue
            for item in department_links:
                result = extract_officeholder(item)
                if result:
                    officials.append(result)
            if args.debug:
                print(f"[debug] officials_after_department_pages={len(officials)}")
            if officials:
                break
    if not officials:
        for government_seed_url in government_seed_urls:
            seeded = extract_officials_from_government_page(government_seed_url, args.state)
            if args.debug:
                print(f"[debug] seeded_from_{government_seed_url}={len(seeded)}")
            if seeded:
                officials = seeded
                break

    if args.state.lower() == "arizona":
        senate_members = extract_arizona_senate_members(senate_url)
        house_members = extract_arizona_house_members(house_url)
    else:
        senate_members = extract_members_from_navbox(
            senate_url, [f"Members of the {args.state} Senate", f"{args.state} Senate"]
        )
        if len(senate_members) < 20:
            senate_members = extract_members_from_table(senate_url, ["Senator", "Name"])
        if len(senate_members) < 10:
            senate_members = extract_members_from_table_loose(
                senate_url, ["Senator", "Representative", "Councilmember", "Member", "Delegate", "Name"]
            )

        house_members = extract_members_from_navbox(
            house_url,
            [
                f"Members of the {args.state} House of Representatives",
                f"{args.state} House of Representatives",
            ],
        )
        if len(house_members) < 50:
            house_members = extract_members_from_table(house_url, ["Representative", "Name", "Member"])
        if len(house_members) < 10:
            house_members = extract_members_from_table_loose(
                house_url, ["Representative", "Councilmember", "Member", "Delegate", "Name"]
            )

    if override.get("unicameral"):
        senate_members = senate_members or house_members
        if state_key == "guam" and not senate_members:
            senate_members = extract_guam_current_legislature_members(senate_url)
        house_members = []
    elif state_key == "puerto rico":
        if not senate_members:
            senate_members = extract_latest_term_members(
                senate_url,
                r"^(\d{1,2})(?:st|nd|rd|th)\s+Senate of Puerto Rico$",
                ["Senator", "Name", "Member"],
            )
        if not house_members:
            house_members = extract_latest_term_members(
                house_url,
                r"^(\d{1,2})(?:st|nd|rd|th)\s+House of Representatives of Puerto Rico$",
                ["Representative", "Name", "Member"],
            )

    output = {
        "state": args.state,
        "senate_url": senate_url,
        "house_url": house_url,
        "officials": officials,
        "senate_members": annotate_legislative_members(senate_members, "senate", "Senator"),
        "house_members": annotate_legislative_members(house_members, "house", "Representative"),
    }

    output_path = Path(args.output or f"data/raw/{args.state.lower().replace(' ', '_')}_state_roster.json")
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Saved {args.state} roster to {output_path} "
        f"(officials={len(officials)}, senate={len(senate_members)}, house={len(house_members)})"
    )


def find_state_link(links: list[dict[str, Any]], state: str, chamber: str) -> str:
    for item in links:
        if item.get("state") == state and item.get("chamber") == chamber:
            return item.get("url") or ""
    return ""


def guess_state_url(state: str, suffix: str) -> str:
    slug = state.replace(" ", "_")
    return f"https://en.wikipedia.org/wiki/{slug}_{suffix}"


def build_government_seed_urls(state: str, senate_url: str, override_url: str) -> list[str]:
    state_slug = state.replace(" ", "_")
    candidates = [
        override_url.strip(),
        f"https://en.wikipedia.org/wiki/Government_of_{state_slug}",
        senate_url.strip(),
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


def extract_members_from_navbox(url: str, title_candidates: list[str]) -> list[dict[str, Any]]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    navboxes = [
        n for n in soup.select("div.navbox")
        if any(title in n.get_text(" ", strip=True) for title in title_candidates)
    ]
    if not navboxes:
        return []
    nav = navboxes[0]
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    list_items = nav.select("li")
    for li in list_items:
        list_text = li.get_text(" ", strip=True)
        district = extract_district_from_text(list_text)
        person_anchors = [a for a in li.select("a[href^='/wiki/']") if is_person_anchor(a)]
        if person_anchors:
            for anchor in person_anchors:
                text = anchor.get_text(" ", strip=True)
                href = anchor.get("href", "")
                item = {
                    "name": text,
                    "wikipedia_url": "https://en.wikipedia.org" + href,
                    "has_wikipedia_page": True,
                    "district": district,
                }
                key = member_key(item)
                if key in seen:
                    continue
                seen.add(key)
                members.append(item)
            continue
        plain_name = extract_plain_member_name(list_text)
        if not plain_name:
            continue
        item = {
            "name": plain_name,
            "source_url": url,
            "source_type": "wikipedia_list_text",
            "has_wikipedia_page": False,
            "district": district,
        }
        key = member_key(item)
        if key in seen:
            continue
        seen.add(key)
        members.append(item)
    return members


def select_person_anchor(anchors: list[Any]) -> Any | None:
    for anchor in reversed(anchors):
        text = anchor.get_text(" ", strip=True)
        href = anchor.get("href", "")
        if text in {"v", "t", "e"}:
            continue
        if len(text.split()) < 2:
            continue
        lowered = text.lower()
        if any(word in lowered for word in ["general", "court", "district", "senate", "house", "speaker", "leader", "whip"]):
            continue
        if "district" in href.lower():
            continue
        return anchor
    return None


def is_person_anchor(anchor: Any) -> bool:
    text = anchor.get_text(" ", strip=True)
    href = anchor.get("href", "")
    if not text or not href.startswith("/wiki/"):
        return False
    if text in {"v", "t", "e"}:
        return False
    if len(text.split()) < 2:
        return False
    lowered = text.lower()
    if any(
        word in lowered
        for word in [
            "general court",
            "district",
            "senate",
            "house of representatives",
            "list of",
            "election",
            "party",
            "republican",
            "democratic",
            "independent",
            "speaker",
            "leader",
            "whip",
        ]
    ):
        return False
    if "district" in href.lower():
        return False
    return True


def extract_members_from_table(url: str, name_headers: list[str]) -> list[dict[str, Any]]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for table in soup.select("table.wikitable"):
        header_row = table.find("tr")
        if not header_row:
            continue
        headers = [cell.get_text(" ", strip=True) for cell in header_row.find_all("th")]
        header_text = " ".join(h.lower() for h in headers)
        if "district" in header_text and any(h.lower() in header_text for h in name_headers):
            candidates.append((table, headers))
    if not candidates:
        return []
    table, headers = candidates[0]
    header_index = {header.lower(): idx for idx, header in enumerate(headers)}
    name_idx = None
    for key in [h.lower() for h in name_headers]:
        for header, idx in header_index.items():
            if key in header:
                name_idx = idx
                break
        if name_idx is not None:
            break
    if name_idx is None:
        return []
    district_idx = None
    for header, idx in header_index.items():
        if "district" in header:
            district_idx = idx
            break
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in iter_table_rows(table, len(headers)):
        if name_idx >= len(row):
            continue
        item = extract_person_from_row(row, name_idx)
        if not item:
            continue
        if district_idx is not None and district_idx < len(row):
            item["district"] = extract_district_from_row(row, district_idx)
        key = member_key(item)
        if key in seen:
            continue
        seen.add(key)
        members.append(item)
    return members


def extract_members_from_table_loose(url: str, name_headers: list[str]) -> list[dict[str, Any]]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    best: list[dict[str, Any]] = []
    for table in soup.select("table.wikitable"):
        header_row = table.find("tr")
        if not header_row:
            continue
        headers = [cell.get_text(" ", strip=True) for cell in header_row.find_all("th")]
        if not headers:
            continue
        header_text = " ".join(h.lower() for h in headers)
        name_idx = None
        for idx, header in enumerate(headers):
            lower_header = header.lower()
            if any(token.lower() in lower_header for token in name_headers):
                name_idx = idx
                break
        if name_idx is None:
            continue
        members: list[dict[str, Any]] = []
        seen: set[str] = set()
        district_idx = None
        for idx, header in enumerate(headers):
            if "district" in header.lower() or "ward" in header.lower():
                district_idx = idx
                break
        for row in iter_table_rows(table, len(headers)):
            if name_idx >= len(row):
                continue
            item = extract_person_from_row(row, name_idx)
            if not item:
                continue
            if district_idx is not None and district_idx < len(row):
                item["district"] = extract_district_from_row(row, district_idx)
            key = member_key(item)
            if key in seen:
                continue
            seen.add(key)
            members.append(item)
        if len(members) > len(best):
            best = members
    return best


def extract_guam_current_legislature_members(legislature_url: str) -> list[dict[str, Any]]:
    html = fetch_html(legislature_url)
    soup = BeautifulSoup(html, "html.parser")
    latest_ordinal = -1
    latest_url = ""
    for anchor in soup.select("a[href^='/wiki/']"):
        label = anchor.get_text(" ", strip=True)
        match = re.match(r"^(\d{1,2})(?:st|nd|rd|th)\s+Guam Legislature$", label)
        if not match:
            continue
        ordinal = int(match.group(1))
        if ordinal > latest_ordinal:
            latest_ordinal = ordinal
            latest_url = "https://en.wikipedia.org" + anchor.get("href", "")
    if not latest_url:
        return []
    return extract_members_from_table_loose(latest_url, ["Senator", "Member", "Name"])


def extract_latest_term_members(base_url: str, title_pattern: str, name_headers: list[str]) -> list[dict[str, Any]]:
    html = fetch_html(base_url)
    soup = BeautifulSoup(html, "html.parser")
    latest_ordinal = -1
    latest_url = ""
    compiled = re.compile(title_pattern)
    for anchor in soup.select("a[href^='/wiki/']"):
        label = anchor.get_text(" ", strip=True)
        match = compiled.match(label)
        if not match:
            continue
        ordinal = int(match.group(1))
        if ordinal > latest_ordinal:
            latest_ordinal = ordinal
            latest_url = "https://en.wikipedia.org" + anchor.get("href", "")
    if not latest_url:
        return []
    return extract_members_from_table_loose(latest_url, name_headers)


def extract_person_from_row(row: list[dict[str, str]], name_idx: int) -> dict[str, Any] | None:
    indices = [name_idx]
    if name_idx + 1 < len(row):
        indices.append(name_idx + 1)
    for idx in indices:
        item = extract_person_from_cell(row[idx])
        if item:
            return item
    return None


def extract_district_from_row(row: list[dict[str, str]], district_idx: int) -> str:
    base = normalize_district_text(row[district_idx].get("text", ""))
    if district_idx + 1 < len(row):
        suffix = normalize_district_text(row[district_idx + 1].get("text", ""))
        if suffix and len(suffix) <= 2 and suffix.isalpha():
            return f"{base}{suffix}"
    return base


def discover_government_links_from_page(seed_url: str) -> list[dict[str, Any]]:
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
        return discover_government_links_from_sections(soup, seed_url)
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
    return links


def discover_government_links_from_sections(soup: BeautifulSoup, seed_url: str) -> list[dict[str, Any]]:
    heading_group_map = {
        "Executive": "Executive",
        "Cabinet": "Executive",
        "Judiciary": "Judicial",
        "Independent agencies": "Independent agencies",
        "Law": "Law",
    }
    links: list[dict[str, Any]] = []
    for section_heading, group_label in heading_group_map.items():
        header_tag = find_section_header_tag(soup, section_heading)
        if not header_tag:
            continue
        for anchor in collect_section_wiki_anchors(header_tag):
            label = anchor.get_text(" ", strip=True)
            href = anchor.get("href", "")
            if not label or not href:
                continue
            if label in {"List", "Arizona", "State of Arizona"}:
                continue
            links.append(
                {
                    "label": label,
                    "state": None,
                    "url": "https://en.wikipedia.org" + href,
                    "chamber": None,
                    "category": "government",
                    "group": group_label,
                    "source_seed_url": seed_url,
                }
            )
    return dedupe_member_dicts_by_url(links)


def discover_department_links_from_government_page(seed_url: str, state: str) -> list[dict[str, Any]]:
    if "wikipedia.org/wiki/" not in seed_url:
        return []
    html = fetch_html(seed_url)
    soup = BeautifulSoup(html, "html.parser")
    state_rules = STATE_GOVERNMENT_LINK_RULES.get(state.strip().lower(), {})
    allow_keywords = state_rules.get(
        "allow_keywords",
        {"department", "office", "commission", "board", "authority", "agency", "court"},
    )
    deny_contains = state_rules.get("deny_contains", set())
    deny_exact = state_rules.get("deny_exact", set())
    links: list[dict[str, Any]] = []
    for link_anchor in soup.select("a[href*='/wiki/']"):
        label = link_anchor.get_text(" ", strip=True)
        href = link_anchor.get("href", "")
        if not label or not href:
            continue
        lowered = label.lower()
        if lowered in deny_exact:
            continue
        if any(token in lowered for token in deny_contains):
            continue
        if not any(
            token in lowered
            for token in allow_keywords
        ):
            continue
        if lowered in {"court", "office", "department", "commission", "board"}:
            continue
        links.append(
            {
                "label": label,
                "state": state,
                "url": "https://en.wikipedia.org" + href,
                "chamber": None,
                "category": "government",
                "group": "Executive",
                "source_seed_url": seed_url,
            }
        )
    return dedupe_member_dicts_by_url(links)


def dedupe_member_dicts_by_url(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        url = item.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(item)
    return deduped


def extract_officials_from_government_page(seed_url: str, state: str) -> list[dict[str, Any]]:
    if "wikipedia.org/wiki/" not in seed_url:
        return []
    html = fetch_html(seed_url)
    soup = BeautifulSoup(html, "html.parser")
    executive_header = find_section_header_tag(soup, "Executive")
    if not executive_header:
        return []
    section_anchors = collect_section_wiki_anchors(executive_header)
    if not section_anchors:
        return []

    results: list[dict[str, Any]] = []
    for idx, office_anchor in enumerate(section_anchors):
        office_label = office_anchor.get_text(" ", strip=True)
        if not looks_like_office_label(office_label):
            continue
        person_anchor = None
        for j in range(idx - 1, max(-1, idx - 7), -1):
            candidate = section_anchors[j]
            candidate_name = candidate.get_text(" ", strip=True)
            if looks_like_person_name(candidate_name):
                person_anchor = candidate
                break
        if not person_anchor:
            continue
        person_name = person_anchor.get_text(" ", strip=True)
        results.append(
            {
                "office_label": office_label,
                "office_url": "https://en.wikipedia.org" + office_anchor.get("href", ""),
                "office_group": "Executive",
                "state": state,
                "incumbent": person_name,
                "incumbent_url": "https://en.wikipedia.org" + person_anchor.get("href", ""),
                "source_seed_url": seed_url,
            }
        )
    return dedupe_official_records(results)


def dedupe_official_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in records:
        key = ((item.get("office_label") or "").strip(), (item.get("incumbent") or "").strip())
        if not key[0] or not key[1]:
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def collect_section_wiki_anchors(header_tag: Any) -> list[Any]:
    anchors: list[Any] = []
    node = header_tag.find_next_sibling()
    stop_at_h3 = getattr(header_tag, "name", "") == "h3"
    while node:
        node_name = getattr(node, "name", "")
        if node_name == "h2" or (stop_at_h3 and node_name == "h3"):
            break
        if hasattr(node, "select"):
            anchors.extend(node.select("a[href*='/wiki/']"))
        node = node.find_next_sibling()
    return anchors


def find_section_header_tag(soup: BeautifulSoup, section_title: str) -> Any | None:
    normalized_target = section_title.replace("_", " ").strip().lower()
    for headline in soup.select(".mw-headline"):
        label = headline.get_text(" ", strip=True).replace("_", " ").strip().lower()
        if label == normalized_target:
            return headline.find_parent(["h2", "h3"])
    id_target = section_title.replace(" ", "_")
    anchor = soup.find(id=id_target)
    if anchor:
        if getattr(anchor, "name", "") in {"h2", "h3"}:
            return anchor
        return anchor.find_parent(["h2", "h3"])
    return None


def looks_like_person_name(text: str) -> bool:
    cleaned = extract_plain_member_name(text or "")
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if looks_like_office_label(cleaned):
        return False
    if any(token in lowered for token in ["department", "agency", "commission", "court"]):
        return False
    return True


def looks_like_office_label(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    office_tokens = [
        "governor",
        "secretary",
        "attorney general",
        "treasurer",
        "superintendent",
        "inspector",
        "commissioner",
        "chief justice",
        "chief judge",
        "judge",
        "justice",
        "director",
        "administrator",
        "chair",
    ]
    return any(token in lowered for token in office_tokens)


def supplement_massachusetts_house_members(
    house_members: list[dict[str, Any]],
    target_count: int = MASSACHUSETTS_HOUSE_TARGET,
) -> list[dict[str, Any]]:
    if len(house_members) >= target_count:
        return house_members
    supplemental = extract_massachusetts_house_members_from_general_court()
    if not supplemental:
        return house_members

    merged = list(house_members)
    seen = {member_key(item) for item in merged}
    for item in supplemental:
        key = member_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= target_count:
            break
    return merged


def extract_massachusetts_house_members_from_general_court() -> list[dict[str, Any]]:
    # The wiki navbox currently has 160 entries but only 156 unique members.
    # Pulling from MA General Court fills missing unique names when available.
    candidate_urls = [
        "https://malegislature.gov/Legislators/House",
        "https://malegislature.gov/Legislators/Members/House",
    ]
    for url in candidate_urls:
        try:
            html = fetch_html(url)
        except Exception:
            continue
        soup = BeautifulSoup(html, "html.parser")
        members: list[dict[str, Any]] = []
        seen_profile_urls: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if "/Legislators/Profile/" not in href:
                continue
            name = anchor.get_text(" ", strip=True)
            if len(name.split()) < 2:
                continue
            profile_url = urljoin(url, href)
            if profile_url in seen_profile_urls:
                continue
            seen_profile_urls.add(profile_url)
            members.append(
                {
                    "name": name,
                    "source_url": profile_url,
                    "source_type": "state_legislature",
                }
            )
        if len(members) >= 120:
            return members
    return []


def member_key(item: dict[str, Any]) -> str:
    wikipedia_url = (item.get("wikipedia_url") or "").strip()
    source_url = (item.get("source_url") or "").strip()
    name = (item.get("name") or "").strip().lower()
    district = normalize_district_text(item.get("district") or "").lower()
    return wikipedia_url or source_url or f"{name}|{district}"


def extract_arizona_house_members(url: str) -> list[dict[str, Any]]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    best_members: list[dict[str, Any]] = []
    for candidate in soup.select("table.wikitable"):
        header_row = candidate.find("tr")
        if not header_row:
            continue
        headers = [cell.get_text(" ", strip=True) for cell in header_row.find_all("th")]
        header_text = " ".join(h.lower() for h in headers)
        if "district" not in header_text or "name" not in header_text:
            continue
        members = extract_members_from_named_table(candidate, headers, "name")
        if len(members) > len(best_members):
            best_members = members
    return best_members


def extract_arizona_senate_members(url: str) -> list[dict[str, Any]]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    best_members: list[dict[str, Any]] = []
    for candidate in soup.select("table.wikitable"):
        header_row = candidate.find("tr")
        if not header_row:
            continue
        headers = [cell.get_text(" ", strip=True) for cell in header_row.find_all("th")]
        header_text = " ".join(h.lower() for h in headers)
        if "district" not in header_text or "senator" not in header_text:
            continue
        members = extract_members_from_named_table(candidate, headers, "senator")
        if len(members) > len(best_members):
            best_members = members
    return best_members


def extract_members_from_named_table(table: Any, headers: list[str], header_keyword: str) -> list[dict[str, Any]]:
    header_index = {header.lower(): idx for idx, header in enumerate(headers)}
    name_idx = None
    for header, idx in header_index.items():
        if header_keyword in header:
            name_idx = idx
            break
    if name_idx is None:
        return []
    district_idx = None
    for header, idx in header_index.items():
        if "district" in header:
            district_idx = idx
            break
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in iter_table_rows(table, len(headers)):
        cell = row[name_idx]
        item = extract_person_from_cell(cell)
        if not item:
            continue
        if district_idx is not None and district_idx < len(row):
            item["district"] = normalize_district_text(row[district_idx].get("text", ""))
        key = member_key(item)
        if key in seen:
            continue
        seen.add(key)
        members.append(item)
    return members


def iter_table_rows(table: Any, column_count: int) -> list[list[dict[str, str]]]:
    rows = []
    span_map: list[dict[str, Any] | None] = [None] * column_count
    table_rows = table.find_all("tr")
    header_row = table.find("tr")
    started = False
    for row in table_rows:
        if row == header_row:
            started = True
            continue
        if not started:
            continue
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        cell_iter = iter(cells)
        row_cells: list[dict[str, str]] = []
        for col in range(column_count):
            if span_map[col]:
                cell_data = span_map[col]["cell"]
                span_map[col]["remaining"] -= 1
                if span_map[col]["remaining"] <= 0:
                    span_map[col] = None
                row_cells.append(cell_data)
                continue
            try:
                cell = next(cell_iter)
            except StopIteration:
                row_cells.append({"text": "", "href": ""})
                continue
            cell_text = cell.get_text(" ", strip=True)
            anchor = select_best_person_anchor(cell)
            if anchor:
                anchor_text = anchor.get_text(" ", strip=True)
                href = anchor.get("href", "")
                cell_data = {"text": anchor_text or cell_text, "href": href}
            else:
                cell_data = {"text": cell_text, "href": ""}
            rowspan = int(cell.get("rowspan", "1") or 1)
            if rowspan > 1:
                span_map[col] = {"remaining": rowspan - 1, "cell": cell_data}
            row_cells.append(cell_data)
        rows.append(row_cells)
    return rows


def select_best_person_anchor(cell: Any) -> Any | None:
    for anchor in cell.select("a[href^='/wiki/']"):
        label = anchor.get_text(" ", strip=True)
        cleaned = extract_plain_member_name(label)
        if not cleaned:
            continue
        return anchor
    return None


def extract_person_from_cell(cell: Any) -> dict[str, Any] | None:
    if isinstance(cell, dict):
        name = (cell.get("text") or "").strip()
        href = cell.get("href") or ""
        if href.startswith("/wiki/"):
            cleaned = extract_plain_member_name(name)
            if cleaned:
                return {"name": cleaned, "wikipedia_url": "https://en.wikipedia.org" + href, "has_wikipedia_page": True}
            return None
        cleaned = extract_plain_member_name(name)
        if cleaned:
            return {"name": cleaned, "source_type": "wikipedia_list_text", "has_wikipedia_page": False}
        return None
    anchors = [a for a in cell.select("a") if (a.get("href") or "").startswith("/wiki/")]
    for anchor in anchors:
        name = anchor.get_text(" ", strip=True)
        href = anchor.get("href", "")
        cleaned = extract_plain_member_name(name)
        if not cleaned:
            continue
        return {"name": cleaned, "wikipedia_url": "https://en.wikipedia.org" + href, "has_wikipedia_page": True}
    text = cell.get("text") if isinstance(cell, dict) else ""
    cleaned = extract_plain_member_name(text or "")
    if cleaned:
        return {"name": cleaned, "source_type": "wikipedia_list_text", "has_wikipedia_page": False}
    return None


def annotate_legislative_members(
    members: list[dict[str, Any]],
    chamber: str,
    role_title: str,
) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for item in members:
        record = dict(item)
        record.setdefault("chamber", chamber)
        record.setdefault("role_title", role_title)
        if record.get("has_wikipedia_page") is None:
            record["has_wikipedia_page"] = bool(record.get("wikipedia_url"))
        annotated.append(record)
    return annotated


def extract_district_from_text(text: str) -> str | None:
    cleaned = re.sub(r"\[[^\]]+\]", "", text or "").strip()
    if not cleaned:
        return None
    match = re.search(r"(\d{1,3}(?:st|nd|rd|th)\s+[A-Za-z][A-Za-z\s\-'.]+)$", cleaned)
    if match:
        return normalize_district_text(match.group(1))
    if ":" in cleaned:
        left = cleaned.split(":", 1)[0].strip()
        if "district" in left.lower() or re.search(r"\d{1,3}(st|nd|rd|th)", left.lower()):
            return normalize_district_text(left)
    return None


def extract_plain_member_name(text: str) -> str | None:
    cleaned = re.sub(r"\[[^\]]+\]", "", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^[^A-Za-z]+", "", cleaned).strip()
    if not cleaned:
        return None
    if ":" in cleaned:
        left, right = cleaned.split(":", 1)
        if "district" in left.lower() or re.search(r"\d{1,3}(st|nd|rd|th)", left.lower()):
            cleaned = right.strip()
    cleaned = re.sub(r"\((?:D|R|I|DFL|GOP|Democratic|Republican)[^)]*\)$", "", cleaned, flags=re.IGNORECASE).strip()
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[0].strip()
    if len(cleaned.split()) < 2:
        return None
    lowered = cleaned.lower()
    blocked_tokens = [
        "district",
        "senate",
        "house of representatives",
        "speaker",
        "majority leader",
        "minority leader",
        "general court",
        "legislature",
        "vacant",
        "democratic",
        "republican",
        "independent",
        "libertarian",
        "green",
        "party",
    ]
    if any(token in lowered for token in blocked_tokens):
        return None
    if re.search(r"\(\s*\d+\s*\)$", cleaned):
        return None
    return cleaned


def normalize_district_text(text: str) -> str:
    cleaned = re.sub(r"\[[^\]]+\]", "", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


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


if __name__ == "__main__":
    main()
