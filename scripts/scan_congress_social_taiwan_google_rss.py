from __future__ import annotations

import argparse
import json
import re
from datetime import date
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import Appointment, Office, Person


RSS_ENDPOINT = "https://news.google.com/rss/search"
SOCIAL_DOMAINS = ("x.com", "twitter.com", "facebook.com", "instagram.com", "youtube.com", "youtu.be")
UA = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"


def _month_bounds(year: int, months: list[int]) -> tuple[date, date]:
    first_month = min(months)
    last_month = max(months)
    start = date(year, first_month, 1)
    if last_month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, last_month + 1, 1)
    return start, end


def _build_date_filter(start: date, end: date) -> str:
    return f"after:{start.isoformat()} before:{end.isoformat()}"


def _extract_profile_query_tokens(social_profiles: dict | None) -> list[str]:
    if not isinstance(social_profiles, dict):
        return []
    tokens: list[str] = []
    for value in social_profiles.values():
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = str(item or "").strip()
            if not text:
                continue
            parsed = urlparse(text if text.startswith("http") else f"https://{text}")
            host = parsed.netloc.lower()
            path = parsed.path.strip("/")
            if not host or not path:
                continue
            base = path.split("/", 1)[0]
            if host.endswith("x.com") or host.endswith("twitter.com"):
                tokens.append(f"site:x.com/{base}")
            elif host.endswith("facebook.com"):
                tokens.append(f"site:facebook.com/{base}")
            elif host.endswith("instagram.com"):
                tokens.append(f"site:instagram.com/{base}")
            elif host.endswith("youtube.com"):
                tokens.append(f"site:youtube.com/{base}")
    return list(dict.fromkeys(tokens))


def _build_queries(full_name: str, social_profiles: dict | None, start: date, end: date) -> list[str]:
    date_part = _build_date_filter(start, end)
    profile_tokens = _extract_profile_query_tokens(social_profiles)
    if profile_tokens:
        return [f"Taiwan {token} {date_part}" for token in profile_tokens]
    return [
        f"\"{full_name}\" Taiwan (site:x.com OR site:facebook.com OR site:instagram.com OR site:youtube.com) {date_part}",
    ]


def _parse_rss_items(xml_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source_node = item.find("source")
        source_title = (source_node.text or "").strip() if source_node is not None and source_node.text else ""
        source_url = (source_node.attrib.get("url", "") if source_node is not None else "").strip()
        description = (item.findtext("description") or "").strip()
        items.append(
            {
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "source_title": source_title,
                "source_url": source_url,
                "description": description,
            }
        )
    return items


def _is_social_item(item: dict[str, str]) -> bool:
    blobs = " ".join([item.get("source_url", ""), item.get("source_title", ""), item.get("title", ""), item.get("description", ""), item.get("link", "")]).lower()
    return any(domain in blobs for domain in SOCIAL_DOMAINS)


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan congress members' social-media Taiwan mentions via Google RSS.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--months", default="4,3,2,1")
    parser.add_argument("--chamber", choices=["all", "senate", "house"], default="all")
    parser.add_argument("--only-current", action="store_true", default=True)
    parser.add_argument("--parser-identity", default="wikipedia_congress_list_v1")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--max-people", type=int, default=None)
    parser.add_argument("--hl", default="zh-TW")
    parser.add_argument("--gl", default="TW")
    parser.add_argument("--ceid", default="TW:zh-Hant")
    parser.add_argument("--output", default="data/raw/congress_social_taiwan_rss_scan.json")
    args = parser.parse_args()

    months = [int(item.strip()) for item in str(args.months).split(",") if item.strip()]
    months = [m for m in months if 1 <= m <= 12]
    if not months:
        raise SystemExit("No valid months.")
    start, end = _month_bounds(args.year, months)

    with session_scope() as session:
        stmt = (
            select(
                Person.id,
                Person.full_name,
                Person.social_profiles,
                Office.chamber,
                Appointment.status,
                Appointment.parser_identity,
                Appointment.is_current,
            )
            .join(Appointment, Appointment.person_id == Person.id)
            .join(Office, Office.id == Appointment.office_id)
            .where(Office.level == "federal", Office.branch == "legislative")
        )
        if args.parser_identity:
            stmt = stmt.where(Appointment.parser_identity == args.parser_identity)
        if args.chamber != "all":
            stmt = stmt.where(Office.chamber == args.chamber)
        if args.only_current:
            stmt = stmt.where(Appointment.status == "current", Appointment.is_current.is_(True))
        rows = session.execute(stmt).all()

    people_map: dict[int, dict[str, object]] = {}
    for person_id, full_name, social_profiles, chamber, status, parser_identity, is_current in rows:
        if person_id not in people_map:
            people_map[person_id] = {
                "person_id": person_id,
                "full_name": full_name,
                "social_profiles": social_profiles or {},
                "chamber": chamber,
                "status": status,
                "parser_identity": parser_identity,
                "is_current": bool(is_current),
            }

    people = sorted(people_map.values(), key=lambda x: str(x["full_name"]).lower())
    if args.start_index > 0:
        people = people[args.start_index :]
    if args.max_people is not None and args.max_people >= 0:
        people = people[: args.max_people]

    results: list[dict[str, object]] = []
    total_hits = 0

    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=30.0) as client:
        for index, person in enumerate(people, start=1):
            full_name = str(person["full_name"])
            queries = _build_queries(full_name, person.get("social_profiles"), start, end)
            person_hits: list[dict[str, str]] = []
            seen_links: set[str] = set()
            for query in queries:
                rss_url = (
                    f"{RSS_ENDPOINT}?q={quote_plus(query)}"
                    f"&hl={quote_plus(args.hl)}&gl={quote_plus(args.gl)}&ceid={quote_plus(args.ceid)}"
                )
                try:
                    response = client.get(rss_url)
                    response.raise_for_status()
                except Exception:
                    continue
                try:
                    items = _parse_rss_items(response.text)
                except Exception:
                    continue
                for item in items:
                    if not _is_social_item(item):
                        continue
                    link = item.get("link", "")
                    if not link or link in seen_links:
                        continue
                    seen_links.add(link)
                    person_hits.append(
                        {
                            "query": query,
                            "title": _normalize_whitespace(item.get("title", "")),
                            "source_title": _normalize_whitespace(item.get("source_title", "")),
                            "source_url": item.get("source_url", ""),
                            "link": link,
                            "pub_date": item.get("pub_date", ""),
                        }
                    )

            total_hits += len(person_hits)
            results.append(
                {
                    "person_id": person["person_id"],
                    "full_name": full_name,
                    "chamber": person["chamber"],
                    "hit_count": len(person_hits),
                    "has_taiwan_social_mentions": len(person_hits) > 0,
                    "hits": person_hits[:20],
                }
            )
            print(
                json.dumps(
                    {
                        "progress": index,
                        "person": full_name,
                        "queries": len(queries),
                        "hits": len(person_hits),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    summary = {
        "year": args.year,
        "months": sorted(months),
        "chamber": args.chamber,
        "people": len(results),
        "people_with_hits": sum(1 for item in results if item["hit_count"] > 0),
        "total_hits": total_hits,
        "results": results,
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "status": "done",
                "output": args.output,
                "people": summary["people"],
                "people_with_hits": summary["people_with_hits"],
                "total_hits": summary["total_hits"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
