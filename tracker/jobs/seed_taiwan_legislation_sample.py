from __future__ import annotations

from datetime import date

from tracker.db import session_scope
from tracker.services.legislation_service import LegislationService


SAMPLE_LEGISLATION = [
    {
        "bill_slug": "s1216_taiwan_allies_fund_act_2025",
        "bill_number": "S.1216",
        "title": "Taiwan Allies Fund Act",
        "legislation_type": "bill",
        "level": "federal",
        "jurisdiction_name": "United States",
        "chamber": "senate",
        "summary": "A bill to support Taiwan's international space, and for other purposes.",
        "status_text": "Placed on Senate Legislative Calendar",
        "introduced_date": date(2025, 3, 31),
        "last_action_date": date(2026, 2, 10),
        "source_url": "https://www.congress.gov/index.php/bill/119th-congress/senate-bill/1216/titles",
        "source_type": "official",
        "parser_identity": "manual_taiwan_legislation_seed_v1",
        "relevance_score": 1.0,
        "raw_payload": {"seeded_from": "manual_taiwan_legislation_2025"},
        "sources": [
            {
                "source_url": "https://www.congress.gov/index.php/bill/119th-congress/senate-bill/1216/titles",
                "source_type": "official",
                "source_title": "Congress.gov | S.1216 Taiwan Allies Fund Act",
                "parser_identity": "manual_taiwan_legislation_seed_v1",
            }
        ],
        "sponsors": [
            {"full_name": "Chris Van Hollen", "role": "sponsor", "source_url": "https://www.congress.gov/index.php/bill/119th-congress/senate-bill/1216/titles", "source_type": "official"},
            {"full_name": "John Curtis", "role": "cosponsor", "source_url": "https://www.congress.gov/index.php/bill/119th-congress/senate-bill/1216/titles", "source_type": "official"},
            {"full_name": "Andy Kim", "role": "cosponsor", "source_url": "https://www.congress.gov/index.php/bill/119th-congress/senate-bill/1216/titles", "source_type": "official"},
            {"full_name": "Michael Bennet", "role": "cosponsor", "source_url": "https://www.congress.gov/index.php/bill/119th-congress/senate-bill/1216/titles", "source_type": "official"},
        ],
    },
    {
        "bill_slug": "blue_skies_for_taiwan_act_2026",
        "bill_number": "Blue Skies for Taiwan Act of 2026",
        "title": "Blue Skies for Taiwan Act of 2026",
        "legislation_type": "bill",
        "level": "federal",
        "jurisdiction_name": "United States",
        "chamber": "senate",
        "summary": "Bipartisan legislation to strengthen U.S.-Taiwan drone cooperation and secure supply chains.",
        "status_text": "Introduced",
        "introduced_date": date(2026, 4, 1),
        "last_action_date": date(2026, 4, 1),
        "source_url": "https://www.cruz.senate.gov/newsroom/press-releases/sens-cruz-merkley-curtis-kim-introduce-bipartisan-bill-to-strengthen-ustaiwan-drone-cooperation",
        "source_type": "official",
        "parser_identity": "manual_taiwan_legislation_seed_v1",
        "relevance_score": 1.0,
        "raw_payload": {"seeded_from": "manual_taiwan_legislation_2026"},
        "sources": [
            {
                "source_url": "https://www.cruz.senate.gov/newsroom/press-releases/sens-cruz-merkley-curtis-kim-introduce-bipartisan-bill-to-strengthen-ustaiwan-drone-cooperation",
                "source_type": "official",
                "source_title": "Cruz press release | Blue Skies for Taiwan Act of 2026",
                "parser_identity": "manual_taiwan_legislation_seed_v1",
            }
        ],
        "sponsors": [
            {"full_name": "Ted Cruz", "chinese_name": "克魯茲", "role": "sponsor", "source_url": "https://www.cruz.senate.gov/newsroom/press-releases/sens-cruz-merkley-curtis-kim-introduce-bipartisan-bill-to-strengthen-ustaiwan-drone-cooperation", "source_type": "official"},
            {"full_name": "Jeff Merkley", "role": "cosponsor", "source_url": "https://www.cruz.senate.gov/newsroom/press-releases/sens-cruz-merkley-curtis-kim-introduce-bipartisan-bill-to-strengthen-ustaiwan-drone-cooperation", "source_type": "official"},
            {"full_name": "John Curtis", "role": "cosponsor", "source_url": "https://www.cruz.senate.gov/newsroom/press-releases/sens-cruz-merkley-curtis-kim-introduce-bipartisan-bill-to-strengthen-ustaiwan-drone-cooperation", "source_type": "official"},
            {"full_name": "Andy Kim", "role": "cosponsor", "source_url": "https://www.cruz.senate.gov/newsroom/press-releases/sens-cruz-merkley-curtis-kim-introduce-bipartisan-bill-to-strengthen-ustaiwan-drone-cooperation", "source_type": "official"},
        ],
    },
    {
        "bill_slug": "taiwan_first_presidential_elections_resolution_2026",
        "bill_number": "Taiwan first presidential elections resolution",
        "title": "Resolution commemorating the 30th anniversary of Taiwan's first presidential elections",
        "legislation_type": "resolution",
        "level": "federal",
        "jurisdiction_name": "United States",
        "chamber": "senate",
        "summary": "Bipartisan Senate resolution commemorating Taiwan's first direct presidential elections.",
        "status_text": "Introduced",
        "introduced_date": date(2026, 3, 23),
        "last_action_date": date(2026, 3, 23),
        "source_url": "https://www.kaine.senate.gov/press-releases/duckworth-curtis-kaine-lead-bipartisan-senate-resolution-commemorating-30th-anniversary-of-taiwans-first-presidential-elections",
        "source_type": "official",
        "parser_identity": "manual_taiwan_legislation_seed_v1",
        "relevance_score": 1.0,
        "raw_payload": {"seeded_from": "manual_taiwan_legislation_2026"},
        "sources": [
            {
                "source_url": "https://www.kaine.senate.gov/press-releases/duckworth-curtis-kaine-lead-bipartisan-senate-resolution-commemorating-30th-anniversary-of-taiwans-first-presidential-elections",
                "source_type": "official",
                "source_title": "Kaine press release | Taiwan presidential elections resolution",
                "parser_identity": "manual_taiwan_legislation_seed_v1",
            }
        ],
        "sponsors": [
            {"full_name": "Tammy Duckworth", "role": "sponsor", "source_url": "https://www.kaine.senate.gov/press-releases/duckworth-curtis-kaine-lead-bipartisan-senate-resolution-commemorating-30th-anniversary-of-taiwans-first-presidential-elections", "source_type": "official"},
            {"full_name": "John Curtis", "role": "cosponsor", "source_url": "https://www.kaine.senate.gov/press-releases/duckworth-curtis-kaine-lead-bipartisan-senate-resolution-commemorating-30th-anniversary-of-taiwans-first-presidential-elections", "source_type": "official"},
            {"full_name": "Tim Kaine", "role": "cosponsor", "source_url": "https://www.kaine.senate.gov/press-releases/duckworth-curtis-kaine-lead-bipartisan-senate-resolution-commemorating-30th-anniversary-of-taiwans-first-presidential-elections", "source_type": "official"},
        ],
    },
    {
        "bill_slug": "sjres101_taiwan_fms_disapproval_2026",
        "bill_number": "S.J.Res.101",
        "title": "A joint resolution providing for congressional disapproval of the proposed foreign military sales to Taiwan of certain defense articles and services.",
        "legislation_type": "joint resolution",
        "level": "federal",
        "jurisdiction_name": "United States",
        "chamber": "senate",
        "summary": "Joint resolution concerning proposed foreign military sales to Taiwan.",
        "status_text": "Introduced",
        "introduced_date": date(2026, 1, 5),
        "last_action_date": date(2026, 1, 5),
        "source_url": "https://www.congress.gov/bill/119th-congress/senate-joint-resolution/101/all-actions-without-amendments",
        "source_type": "official",
        "parser_identity": "manual_taiwan_legislation_seed_v1",
        "relevance_score": 1.0,
        "raw_payload": {"seeded_from": "manual_taiwan_legislation_2026"},
        "sources": [
            {
                "source_url": "https://www.congress.gov/bill/119th-congress/senate-joint-resolution/101/all-actions-without-amendments",
                "source_type": "official",
                "source_title": "Congress.gov | S.J.Res.101",
                "parser_identity": "manual_taiwan_legislation_seed_v1",
            }
        ],
        "sponsors": [
            {"full_name": "Rand Paul", "role": "sponsor", "source_url": "https://www.congress.gov/bill/119th-congress/senate-joint-resolution/101/all-actions-without-amendments", "source_type": "official"},
        ],
    },
    {
        "bill_slug": "indiana_friendly_taiwan_resolution_2026",
        "bill_number": "Indiana friendly Taiwan resolution",
        "title": "Indiana General Assembly friendly Taiwan resolution",
        "legislation_type": "resolution",
        "level": "state",
        "jurisdiction_name": "Indiana",
        "chamber": "legislature",
        "summary": "Indiana state legislature passed a friendly Taiwan resolution supporting Taiwan and a U.S.-Taiwan tax agreement.",
        "status_text": "Passed",
        "introduced_date": date(2026, 2, 23),
        "last_action_date": date(2026, 2, 23),
        "source_url": "https://www.cna.com.tw/news/aipl/202602250073.aspx",
        "source_type": "media",
        "parser_identity": "manual_taiwan_legislation_seed_v1",
        "relevance_score": 1.0,
        "raw_payload": {"seeded_from": "manual_taiwan_legislation_2026"},
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602250073.aspx",
                "source_type": "media",
                "source_title": "CNA | Indiana legislature passes friendly Taiwan resolution",
                "parser_identity": "manual_taiwan_legislation_seed_v1",
            }
        ],
        "sponsors": [],
    },
    {
        "bill_slug": "iowa_friendly_taiwan_resolution_2026",
        "bill_number": "Iowa friendly Taiwan resolution",
        "title": "Iowa General Assembly friendly Taiwan resolution",
        "legislation_type": "resolution",
        "level": "state",
        "jurisdiction_name": "Iowa",
        "chamber": "legislature",
        "summary": "Iowa legislature passed a friendly Taiwan resolution backing Taiwan's international participation and a U.S.-Taiwan tax agreement.",
        "status_text": "Passed",
        "introduced_date": date(2026, 2, 25),
        "last_action_date": date(2026, 2, 25),
        "source_url": "https://www.cna.com.tw/news/aipl/202602260025.aspx",
        "source_type": "media",
        "parser_identity": "manual_taiwan_legislation_seed_v1",
        "relevance_score": 1.0,
        "raw_payload": {"seeded_from": "manual_taiwan_legislation_2026"},
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202602260025.aspx",
                "source_type": "media",
                "source_title": "CNA | Iowa legislature passes friendly Taiwan resolution",
                "parser_identity": "manual_taiwan_legislation_seed_v1",
            }
        ],
        "sponsors": [],
    },
]


def run_seed_taiwan_legislation_sample() -> dict:
    with session_scope() as session:
        service = LegislationService(session)
        created = 0
        updated = 0
        for payload in SAMPLE_LEGISLATION:
            _, was_created = service.upsert_legislation(payload)
            if was_created:
                created += 1
            else:
                updated += 1
        return {
            "job_name": "seed_taiwan_legislation_sample",
            "status": "success",
            "records_found": len(SAMPLE_LEGISLATION),
            "records_created": created,
            "records_updated": updated,
            "errors": [],
        }
