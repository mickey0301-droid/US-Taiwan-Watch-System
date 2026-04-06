from __future__ import annotations

from datetime import datetime

from tracker.db import session_scope
from tracker.models import SyncRun
from tracker.services.officials_service import OfficialsService
from tracker.services.statements_service import StatementsService


EVENT_GROUPS = [
    {
        "slug": "bipartisan_senators_vaccine_visit_2021",
        "title": "美國跨黨派參議員訪台宣布捐贈疫苗",
        "excerpt": "2021 年 6 月，美國跨黨派參議員 Tammy Duckworth、Dan Sullivan 與 Chris Coons 訪台，宣布美國將向台灣捐贈 COVID-19 疫苗，並重申對台支持。",
        "date_published": "2021-06-06T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Tammy Duckworth", "chinese_aliases": ["達克沃絲"]},
            {"name": "Dan Sullivan", "chinese_aliases": ["蘇利文"]},
            {"name": "Chris Coons", "chinese_aliases": ["昆斯"]},
        ],
        "sources": [
            {"source_url": "https://www.president.gov.tw/News/26761", "source_type": "official", "source_title": "總統接見美國跨黨派參議員訪團", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": True},
            {"source_url": "https://www.cna.com.tw/news/firstnews/202106065004.aspx", "source_type": "media", "source_title": "美跨黨派參議員訪台 宣布捐贈75萬劑疫苗", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": False},
        ],
    },
    {
        "slug": "pelosi_taiwan_visit_2022",
        "title": "Nancy Pelosi 訪台並發表涉台聲明",
        "excerpt": "2022 年 8 月，美國聯邦眾議院議長 Nancy Pelosi 訪問台灣，與台灣領導人會晤，重申美國國會對台支持與台海和平穩定的重要性。",
        "date_published": "2022-08-02T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Nancy Pelosi", "chinese_aliases": ["裴洛西"]},
        ],
        "sources": [
            {"source_url": "https://www.president.gov.tw/News/27811", "source_type": "official", "source_title": "總統接見美國聯邦眾議院議長裴洛西訪團", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": True},
            {"source_url": "https://www.cna.com.tw/news/aipl/202208020438.aspx", "source_type": "media", "source_title": "裴洛西訪台", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": False},
        ],
    },
    {
        "slug": "eric_holcomb_visit_2022",
        "title": "Eric Holcomb 訪台推動州層級合作",
        "excerpt": "2022 年 8 月，印第安納州州長 Eric Holcomb 訪台，與台灣方面就經貿、教育及供應鏈合作交換意見。",
        "date_published": "2022-08-21T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Eric Holcomb", "chinese_aliases": ["侯康安"]},
        ],
        "sources": [
            {"source_url": "https://www.president.gov.tw/News/27863", "source_type": "official", "source_title": "總統接見美國印第安納州州長侯康安訪團", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": True},
            {"source_url": "https://www.cna.com.tw/news/aipl/202208210183.aspx", "source_type": "media", "source_title": "美國印第安納州州長侯康安訪台", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": False},
        ],
    },
    {
        "slug": "antony_blinken_taiwan_status_2022",
        "title": "Antony Blinken 重申台海和平穩定與反對單方面改變現狀",
        "excerpt": "2022 年，美國國務卿 Antony Blinken 多次公開表示台海和平穩定攸關國際社會，並反對任何單方面改變現狀的行為。",
        "date_published": "2022-05-26T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Antony Blinken", "chinese_aliases": ["布林肯"]},
        ],
        "sources": [
            {"source_url": "https://www.cna.com.tw/news/aopl/202205260018.aspx", "source_type": "media", "source_title": "布林肯談台海和平穩定", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": False},
        ],
    },
    {
        "slug": "mccaul_gallagher_taiwan_visit_2023",
        "title": "Michael McCaul 與 Mike Gallagher 等國會議員訪台",
        "excerpt": "2023 年 4 月，美國聯邦眾議院外交委員會主席 Michael McCaul 率跨黨派議員團訪台，同行成員包括 Mike Gallagher 等人，聚焦台海安全與軍售議題。",
        "date_published": "2023-04-06T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Michael McCaul", "chinese_aliases": ["麥考爾"]},
            {"name": "Mike Gallagher", "chinese_aliases": ["蓋拉格"]},
        ],
        "sources": [
            {"source_url": "https://www.president.gov.tw/News/28426", "source_type": "official", "source_title": "總統接見美國聯邦眾議院外交委員會主席訪團", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": True},
            {"source_url": "https://www.cna.com.tw/news/aipl/202304060306.aspx", "source_type": "media", "source_title": "美眾院外委會主席麥考爾率團訪台", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": False},
        ],
    },
    {
        "slug": "rob_wittman_house_visit_2023",
        "title": "Rob Wittman 率美國聯邦眾議員訪團訪台",
        "excerpt": "2023 年 8 月，美國聯邦眾議院軍事委員會副主席 Rob Wittman 率 Carlos Gimenez、Jen Kiggans、Alex Mooney 及 Michael Cloud 訪台，聚焦台美安全合作與印太區域穩定。",
        "date_published": "2023-08-31T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Rob Wittman", "chinese_aliases": ["魏特曼"]},
            {"name": "Carlos Gimenez", "chinese_aliases": ["席曼尼茲"]},
            {"name": "Jen Kiggans", "chinese_aliases": ["季耿絲"]},
            {"name": "Alex Mooney", "chinese_aliases": ["穆尼"]},
            {"name": "Michael Cloud", "chinese_aliases": ["克勞德"]},
        ],
        "sources": [
            {"source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=95&s=115377&sms=73", "source_type": "official", "source_title": "外交部歡迎美國聯邦眾議院軍事委員會副主席魏特曼等五位國會議員訪問台灣", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": True},
        ],
    },
    {
        "slug": "laura_rosenberger_taiwan_visit_2023",
        "title": "Laura Rosenberger 訪台談 AIT 與台美合作",
        "excerpt": "2023 年，美國在台協會主席 Laura Rosenberger 訪台，與台灣官員會晤，討論台美安全、經濟與民主合作。",
        "date_published": "2023-09-14T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Laura Rosenberger", "chinese_aliases": ["羅森柏格"]},
        ],
        "sources": [
            {"source_url": "https://www.cna.com.tw/news/aipl/202309140327.aspx", "source_type": "media", "source_title": "AIT主席羅森柏格訪台", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": False},
        ],
    },
    {
        "slug": "gallagher_house_visit_2024",
        "title": "Mike Gallagher 與 Lisa McClain 等眾議員訪台",
        "excerpt": "2024 年 2 月，美國聯邦眾議員 Mike Gallagher、Lisa McClain、Jake Ellzey 與 Seth Moulton 等訪台，討論台海安全、對台支持與區域嚇阻。",
        "date_published": "2024-02-22T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Mike Gallagher", "chinese_aliases": ["蓋拉格"]},
            {"name": "Lisa McClain", "chinese_aliases": ["麥克萊恩"]},
            {"name": "Jake Ellzey", "chinese_aliases": ["艾爾齊"]},
            {"name": "Seth Moulton", "chinese_aliases": ["莫爾頓"]},
        ],
        "sources": [
            {"source_url": "https://www.mofa.gov.tw/News_Content.aspx?n=95&s=116611&sms=73", "source_type": "official", "source_title": "外交部歡迎美國聯邦眾議員訪團訪問台灣", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": True},
        ],
    },
    {
        "slug": "blinken_sullivan_cross_strait_statements_2024",
        "title": "Antony Blinken 與 Jake Sullivan 重申台海和平穩定",
        "excerpt": "2024 年 2 月，美國國務卿 Antony Blinken 在慕尼黑安全會議與中國外長王毅會談時，重申台海和平穩定的重要性；外交部並提及 Jake Sullivan 先前也在曼谷作出相同表述。",
        "date_published": "2024-02-17T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Antony Blinken", "chinese_aliases": ["布林肯"]},
            {"name": "Jake Sullivan", "chinese_aliases": ["蘇利文"]},
        ],
        "sources": [
            {"source_url": "https://en.mofa.gov.tw/News_Content.aspx?n=1328&s=116595&sms=273", "source_type": "official", "source_title": "MOFA response to Blinken-Wang meeting and reference to Jake Sullivan statement", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": True},
        ],
    },
    {
        "slug": "biden_taiwan_aid_package_2024",
        "title": "Joe Biden 簽署含台灣援助內容法案",
        "excerpt": "2024 年，美國總統 Joe Biden 簽署包含援助台灣與印太安全內容的法案，強調嚇阻與維護區域穩定。",
        "date_published": "2024-04-24T00:00:00",
        "statement_type": "law_signing",
        "participants": [
            {"name": "Joe Biden", "chinese_aliases": ["拜登"]},
        ],
        "sources": [
            {"source_url": "https://www.cna.com.tw/news/aopl/202404240021.aspx", "source_type": "media", "source_title": "拜登簽署含援台法案", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": False},
        ],
    },
    {
        "slug": "merkley_van_hollen_visit_2024",
        "title": "Jeff Merkley 與 Chris Van Hollen 訪台",
        "excerpt": "2024 年 5 月，美國聯邦參議員 Jeff Merkley 與 Chris Van Hollen 訪台，與台灣就安全、經貿與區域穩定交換意見。",
        "date_published": "2024-05-28T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Jeff Merkley", "chinese_aliases": ["墨克利"]},
            {"name": "Chris Van Hollen", "chinese_aliases": ["范霍倫"]},
        ],
        "sources": [
            {"source_url": "https://www.president.gov.tw/News/39064", "source_type": "official", "source_title": "總統接見美國聯邦參議員墨克利及范霍倫訪團", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": True},
        ],
    },
    {
        "slug": "kurt_campbell_taiwan_statement_2024",
        "title": "Kurt Campbell 談台海和平與美台合作",
        "excerpt": "2024 年，美國副國務卿 Kurt Campbell 公開談及台海和平穩定的重要性，以及美國持續深化與台灣合作的政策方向。",
        "date_published": "2024-05-31T00:00:00",
        "statement_type": "statement",
        "participants": [
            {"name": "Kurt Campbell", "chinese_aliases": ["康貝爾"]},
        ],
        "sources": [
            {"source_url": "https://www.cna.com.tw/news/aipl/202405310034.aspx", "source_type": "media", "source_title": "康貝爾談台海和平與美台合作", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": False},
        ],
    },
    {
        "slug": "strickland_democratic_delegation_visit_2024",
        "title": "Marilyn Strickland 率美國民主黨眾議員訪團訪台",
        "excerpt": "2024 年 8 月，美國聯邦眾議員 Marilyn Strickland 率 Julia Brownley、Jill Tokuda 與 Jasmine Crockett 訪台，談台美安全與拜登路線延續。",
        "date_published": "2024-08-15T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Marilyn Strickland", "chinese_aliases": ["史垂克蘭"]},
            {"name": "Julia Brownley", "chinese_aliases": ["布朗莉"]},
            {"name": "Jill Tokuda", "chinese_aliases": ["德田"]},
            {"name": "Jasmine Crockett", "chinese_aliases": ["克勞基特"]},
        ],
        "sources": [
            {"source_url": "https://www.cna.com.tw/news/aipl/202408150338.aspx", "source_type": "media", "source_title": "美民主黨議員訪團：賀錦麗對台政策可望延續拜登路線", "parser_identity": "manual_taiwan_source_seed_v1", "is_primary_source": False},
        ],
    },
]


def _resolve_person_ids(officials_service: OfficialsService, participants: list[dict]) -> list[int]:
    person_ids: list[int] = []
    for participant in participants:
        person = officials_service.find_person(participant["name"])
        if not person:
            person, _ = officials_service.upsert_person(
                {
                    "full_name": participant["name"],
                    "source_url": "https://www.cna.com.tw/",
                    "source_type": "media",
                    "seed_source_type": "media",
                    "profile_status": "seeded",
                    "verification_status": "unverified",
                    "raw_payload": {"manual_seed": True, "seed_context": "taiwan_2021_2024_event_seed"},
                }
            )
        for alias in participant.get("chinese_aliases", []):
            officials_service.ensure_alias(person.id, alias, source_url="https://www.cna.com.tw/", source_type="media", alias_type="chinese_name")
        if person.id not in person_ids:
            person_ids.append(person.id)
    return person_ids


def run_seed_taiwan_2021_2024_sample_events() -> dict:
    with session_scope() as session:
        officials_service = OfficialsService(session)
        statements_service = StatementsService(session)
        sync_run = SyncRun(
            job_name="seed_taiwan_2021_2024_sample_events",
            job_type="statement_seed",
            source_name="president_mofa_cna_manual_seed_2021_2024",
        )
        session.add(sync_run)
        session.flush()

        events_processed = 0
        created_count = 0
        updated_count = 0
        sources_processed = 0

        for event in EVENT_GROUPS:
            participant_ids = _resolve_person_ids(officials_service, event["participants"])
            lead_person_id = participant_ids[0] if participant_ids else None
            for source in event["sources"]:
                _, created = statements_service.ingest_statement(
                    {
                        "person_id": lead_person_id,
                        "participant_ids": participant_ids,
                        "title": event["title"],
                        "source_title": source["source_title"],
                        "date_published": datetime.fromisoformat(event["date_published"]),
                        "source_url": source["source_url"],
                        "source_type": source["source_type"],
                        "statement_type": event["statement_type"],
                        "excerpt": event["excerpt"],
                        "full_text": event["excerpt"],
                        "raw_text": event["excerpt"],
                        "is_primary_source": source["is_primary_source"],
                        "parser_identity": source["parser_identity"],
                        "raw_payload": {
                            "event_slug": event["slug"],
                            "seeded_from": "manual_taiwan_2021_2024_sources",
                            "participant_names": [item["name"] for item in event["participants"]],
                        },
                    }
                )
                sources_processed += 1
                if created:
                    created_count += 1
                else:
                    updated_count += 1
            events_processed += 1

        sync_run.ended_at = datetime.utcnow()
        sync_run.status = "success"
        sync_run.records_found = events_processed
        sync_run.records_created = created_count
        sync_run.records_updated = updated_count
        sync_run.meta = {"sources_processed": sources_processed}
        return {
            "status": "success",
            "job_name": "seed_taiwan_2021_2024_sample_events",
            "events_processed": events_processed,
            "records_created": created_count,
            "records_updated": updated_count,
            "sources_processed": sources_processed,
        }
