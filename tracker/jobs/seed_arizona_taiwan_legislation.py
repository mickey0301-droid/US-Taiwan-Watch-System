from __future__ import annotations

from datetime import date

from tracker.db import session_scope
from tracker.services.legislation_service import LegislationService


ARIZONA_TAIWAN_LEGISLATION = [
    {
        "bill_slug": "arizona_scr1029_taiwan_partnership_2021",
        "bill_number": "SCR 1029",
        "title": "Supporting Taiwan's international participation and a closer partnership between the United States and Taiwan in trade and exchanges of officials",
        "legislation_type": "concurrent resolution",
        "level": "state",
        "jurisdiction_name": "Arizona",
        "chamber": "senate",
        "summary": "Arizona Senate Concurrent Resolution supporting Taiwan's international participation, a closer United States-Taiwan trade partnership, and future official exchanges.",
        "status_text": "Introduced",
        "introduced_date": date(2021, 2, 1),
        "last_action_date": date(2021, 2, 1),
        "source_url": "https://www.azleg.gov/legtext/55leg/1r/bills/scr1029p.htm",
        "source_type": "official",
        "parser_identity": "arizona_taiwan_legislation_official_v1",
        "relevance_score": 1.0,
        "raw_payload": {
            "seeded_from": "arizona_taiwan_legislation_official_seed_v1",
            "search_origin": "https://www.azleg.gov/bills/",
            "session": "Fifty-fifth Legislature First Regular Session",
            "reference_title": "United States; Taiwan; partnership",
        },
        "sources": [
            {
                "source_url": "https://www.azleg.gov/legtext/55leg/1r/bills/scr1029p.htm",
                "source_type": "official",
                "source_title": "Arizona Legislature | SCR 1029",
                "parser_identity": "arizona_taiwan_legislation_official_v1",
            }
        ],
        "sponsors": [
            {
                "full_name": "Nancy Barto",
                "role": "sponsor",
                "role_title": "State Senator",
                "source_url": "https://www.azleg.gov/legtext/55leg/1r/bills/scr1029p.htm",
                "source_type": "official",
            }
        ],
    },
    {
        "bill_slug": "arizona_scr1021_taiwan_partnership_2022",
        "bill_number": "SCR 1021",
        "title": "Supporting Taiwan's international participation and a closer partnership between the United States and Taiwan in trade and exchanges of officials",
        "legislation_type": "concurrent resolution",
        "level": "state",
        "jurisdiction_name": "Arizona",
        "chamber": "senate",
        "summary": "Arizona Senate Concurrent Resolution supporting Taiwan's international participation, a closer United States-Taiwan trade partnership, and continued Arizona-Taiwan exchanges.",
        "status_text": "Senate Engrossed",
        "introduced_date": date(2022, 2, 1),
        "last_action_date": date(2022, 2, 1),
        "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/scr1021p.htm",
        "source_type": "official",
        "parser_identity": "arizona_taiwan_legislation_official_v1",
        "relevance_score": 1.0,
        "raw_payload": {
            "seeded_from": "arizona_taiwan_legislation_official_seed_v1",
            "search_origin": "https://www.azleg.gov/bills/",
            "session": "Fifty-fifth Legislature Second Regular Session",
            "reference_title": "United States; Taiwan partnership",
        },
        "sources": [
            {
                "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/scr1021p.htm",
                "source_type": "official",
                "source_title": "Arizona Legislature | SCR 1021",
                "parser_identity": "arizona_taiwan_legislation_official_v1",
            }
        ],
        "sponsors": [
            {"full_name": "Sine Kerr", "role": "sponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "Nancy Barto", "role": "cosponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "Sonny Borrelli", "role": "cosponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "David Gowan", "role": "cosponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "Vince Leach", "role": "cosponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "Warren Petersen", "role": "cosponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "Kelly Townsend", "role": "cosponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "Frank Carroll", "role": "cosponsor", "role_title": "State Representative", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "David Cook", "role": "cosponsor", "role_title": "State Representative", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/scr1021p.htm", "source_type": "official"},
        ],
    },
    {
        "bill_slug": "arizona_hcr2010_taiwan_partnership_2022",
        "bill_number": "HCR 2010",
        "title": "Supporting Taiwan's international participation and a closer partnership between the United States and Taiwan in trade and exchanges of officials",
        "legislation_type": "concurrent resolution",
        "level": "state",
        "jurisdiction_name": "Arizona",
        "chamber": "house",
        "summary": "Arizona House Concurrent Resolution supporting Taiwan's international participation and a closer United States-Taiwan partnership in trade and official exchanges.",
        "status_text": "Introduced",
        "introduced_date": date(2022, 2, 1),
        "last_action_date": date(2022, 2, 1),
        "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/hcr2010p.pdf",
        "source_type": "official",
        "parser_identity": "arizona_taiwan_legislation_official_v1",
        "relevance_score": 1.0,
        "raw_payload": {
            "seeded_from": "arizona_taiwan_legislation_official_seed_v1",
            "search_origin": "https://www.azleg.gov/bills/",
            "session": "Fifty-fifth Legislature Second Regular Session",
            "reference_title": "United States; Taiwan; partnership",
        },
        "sources": [
            {
                "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/hcr2010p.pdf",
                "source_type": "official",
                "source_title": "Arizona Legislature | HCR 2010",
                "parser_identity": "arizona_taiwan_legislation_official_v1",
            }
        ],
        "sponsors": [
            {"full_name": "Marcelino Quiñonez", "role": "sponsor", "role_title": "State Representative", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/hcr2010p.pdf", "source_type": "official"},
            {"full_name": "César Chávez", "role": "cosponsor", "role_title": "State Representative", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/hcr2010p.pdf", "source_type": "official"},
            {"full_name": "Raquel Terán", "role": "cosponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/hcr2010p.pdf", "source_type": "official"},
            {"full_name": "Steve Wilmeth", "role": "cosponsor", "role_title": "State Representative", "source_url": "https://www.azleg.gov/legtext/55leg/2r/bills/hcr2010p.pdf", "source_type": "official"},
        ],
    },
    {
        "bill_slug": "arizona_scr1021_taiwan_trade_partnership_2023",
        "bill_number": "SCR 1021",
        "title": "Supporting Taiwan's international participation and a closer partnership between the United States and Taiwan in trade and exchanges of officials",
        "legislation_type": "concurrent resolution",
        "level": "state",
        "jurisdiction_name": "Arizona",
        "chamber": "senate",
        "summary": "Arizona Senate Concurrent Resolution supporting Taiwan's international participation, a United States-Taiwan bilateral trade agreement, and continued Arizona-Taiwan cooperation.",
        "status_text": "Introduced",
        "introduced_date": date(2023, 2, 1),
        "last_action_date": date(2023, 2, 1),
        "source_url": "https://www.azleg.gov/legtext/56leg/1r/bills/scr1021p.htm",
        "source_type": "official",
        "parser_identity": "arizona_taiwan_legislation_official_v1",
        "relevance_score": 1.0,
        "raw_payload": {
            "seeded_from": "arizona_taiwan_legislation_official_seed_v1",
            "search_origin": "https://www.azleg.gov/bills/",
            "session": "Fifty-sixth Legislature First Regular Session",
            "reference_title": "United States; Taiwan; trade partnership",
        },
        "sources": [
            {
                "source_url": "https://www.azleg.gov/legtext/56leg/1r/bills/scr1021p.htm",
                "source_type": "official",
                "source_title": "Arizona Legislature | SCR 1021 (2023)",
                "parser_identity": "arizona_taiwan_legislation_official_v1",
            }
        ],
        "sponsors": [
            {"full_name": "John Kavanagh", "role": "sponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/56leg/1r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "David Gowan", "role": "cosponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/56leg/1r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "Warren Petersen", "role": "cosponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/56leg/1r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "Frank Carroll", "role": "cosponsor", "role_title": "State Senator", "source_url": "https://www.azleg.gov/legtext/56leg/1r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "Teresa Martinez", "role": "cosponsor", "role_title": "State Representative", "source_url": "https://www.azleg.gov/legtext/56leg/1r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "Leo Biasiucci", "role": "cosponsor", "role_title": "State Representative", "source_url": "https://www.azleg.gov/legtext/56leg/1r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "David Livingston", "role": "cosponsor", "role_title": "State Representative", "source_url": "https://www.azleg.gov/legtext/56leg/1r/bills/scr1021p.htm", "source_type": "official"},
            {"full_name": "Justin Wilmeth", "role": "cosponsor", "role_title": "State Representative", "source_url": "https://www.azleg.gov/legtext/56leg/1r/bills/scr1021p.htm", "source_type": "official"},
        ],
    },
]


def run_seed_arizona_taiwan_legislation() -> dict:
    with session_scope() as session:
        service = LegislationService(session)
        created = 0
        updated = 0
        for payload in ARIZONA_TAIWAN_LEGISLATION:
            _, was_created = service.upsert_legislation(payload)
            if was_created:
                created += 1
            else:
                updated += 1
        return {
            "job_name": "seed_arizona_taiwan_legislation",
            "status": "success",
            "records_found": len(ARIZONA_TAIWAN_LEGISLATION),
            "records_created": created,
            "records_updated": updated,
            "collection_year": "2021-2023",
            "errors": [],
        }
