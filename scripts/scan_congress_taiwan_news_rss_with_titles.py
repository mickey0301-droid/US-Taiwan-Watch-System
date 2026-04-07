from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import Appointment, Office, Person


RSS_ENDPOINT = "https://news.google.com/rss/search"
UA = "Mozilla/5.0 (compatible; UTWBot/1.0; +https://github.com/mickey0301-droid/US-Taiwan-Watch-System)"
TAIWAN_TERMS = ("taiwan", "台灣", "臺灣", "taipei", "中華民國")


def _month_bounds(year: int, months: list[int]) -> tuple[date, date]:
    first = min(months)
    last = max(months)
    start = date(year, first, 1)
    end = date(year + 1, 1, 1) if last == 12 else date(year, last + 1, 1)
    return start, end


def _date_filter(start: date, end: date) -> str:
    return f"after:{start.isoformat()} before:{end.isoformat()}"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _contains_taiwan(text: str) -> bool:
    lowered = (text or "").casefold()
    return any(term in lowered for term in TAIWAN_TERMS)


def _build_query(name: str, chamber: str, start: date, end: date) -> str:
    date_part = _date_filter(start, end)
    if chamber == "senate":
        title_part = '("U.S. Senator" OR Senator)'
    else:
        title_part = '("U.S. Representative" OR Congressman OR Congresswoman OR "House member")'
    return f'"{name}" Taiwan {title_part} {date_part}'


def _parse_rss(xml_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    out: list[dict[str, str]] = []
    for item in root.findall("./channel/item"):
        source = item.find("source")
        out.append(
            {
                "title": _normalize(item.findtext("title") or ""),
                "link": _normalize(item.findtext("link") or ""),
                "pub_date": _normalize(item.findtext("pubDate") or ""),
                "source_title": _normalize(source.text or "") if source is not None and source.text else "",
                "source_url": _normalize(source.attrib.get("url", "")) if source is not None else "",
                "description": _normalize(item.findtext("description") or ""),
            }
        )
    return out


def _fetch_rss(client: httpx.Client, query: str, hl: str, gl: str, ceid: str) -> str | None:
    url = f"{RSS_ENDPOINT}?q={quote_plus(query)}&hl={quote_plus(hl)}&gl={quote_plus(gl)}&ceid={quote_plus(ceid)}"
    for attempt in range(3):
        try:
            resp = client.get(url, timeout=30)
            if resp.status_code == 503:
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.text
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan Taiwan news mentions for current Congress members using title-hinted Google RSS queries.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--months", default="4,3,2,1")
    parser.add_argument("--max-people", type=int, default=60)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--hl", default="en-US")
    parser.add_argument("--gl", default="US")
    parser.add_argument("--ceid", default="US:en")
    parser.add_argument("--output", default="data/raw/congress_taiwan_news_rss_with_titles.json")
    args = parser.parse_args()

    months = [int(x.strip()) for x in str(args.months).split(",") if x.strip()]
    months = [m for m in months if 1 <= m <= 12]
    if not months:
        raise SystemExit("No valid months.")
    start, end = _month_bounds(args.year, months)

    with session_scope() as session:
        rows = session.execute(
            select(Person.id, Person.full_name, Office.chamber)
            .join(Appointment, Appointment.person_id == Person.id)
            .join(Office, Office.id == Appointment.office_id)
            .where(
                Office.level == "federal",
                Office.branch == "legislative",
                Office.chamber.in_(("senate", "house")),
                Appointment.status == "current",
                Appointment.is_current.is_(True),
                Appointment.parser_identity == "wikipedia_congress_list_v1",
            )
        ).all()

    people_map: dict[int, tuple[str, str]] = {}
    for pid, name, chamber in rows:
        if pid not in people_map:
            people_map[pid] = (name, chamber)
    people = sorted([(pid, v[0], v[1]) for pid, v in people_map.items()], key=lambda x: (x[1] or "").lower())
    if args.start_index > 0:
        people = people[args.start_index :]
    if args.max_people is not None and args.max_people >= 0:
        people = people[: args.max_people]

    results: list[dict[str, object]] = []
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True) as client:
        for idx, (pid, name, chamber) in enumerate(people, start=1):
            query = _build_query(name, chamber, start, end)
            xml_text = _fetch_rss(client, query, args.hl, args.gl, args.ceid)
            hits: list[dict[str, str]] = []
            if xml_text:
                try:
                    for item in _parse_rss(xml_text):
                        merged = f"{item['title']} {item['description']}"
                        if not _contains_taiwan(merged):
                            continue
                        hits.append(item)
                except Exception:
                    pass

            results.append(
                {
                    "person_id": pid,
                    "name": name,
                    "chamber": chamber,
                    "query": query,
                    "hit_count": len(hits),
                    "hits": hits[:15],
                }
            )
            print(json.dumps({"progress": idx, "name": name, "chamber": chamber, "hits": len(hits)}, ensure_ascii=False), flush=True)
            time.sleep(0.2)

    summary = {
        "year": args.year,
        "months": sorted(months),
        "people": len(results),
        "people_with_hits": sum(1 for row in results if row["hit_count"] > 0),
        "total_hits": sum(int(row["hit_count"]) for row in results),
        "results": results,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps({"status": "done", "output": args.output, "people": summary["people"], "people_with_hits": summary["people_with_hits"], "total_hits": summary["total_hits"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
