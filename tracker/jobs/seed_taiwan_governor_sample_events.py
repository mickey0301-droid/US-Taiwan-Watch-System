from __future__ import annotations

from datetime import datetime

from tracker.db import session_scope
from tracker.models import SyncRun
from tracker.services.officials_service import OfficialsService
from tracker.services.statements_service import StatementsService


EVENT_GROUPS = [
    {
        "slug": "greg_abbott_taiwan_office_2024",
        "title": "Greg Abbott 宣布成立德州在台辦事處",
        "excerpt": "美國德州州長 Greg Abbott 於 2024 年 7 月在台北宣布成立德州在台辦事處，並簽署經濟發展合作意向書，強調德州與台灣站在同一陣線，將協助深化雙方經貿與科技合作。",
        "date_published": "2024-07-07T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Greg Abbott", "chinese_aliases": ["艾波特"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202407070069.aspx",
                "source_type": "media",
                "source_title": "德州州長：與台灣站在同一陣線 成立在台辦事處",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "glenn_youngkin_visit_taiwan_2023",
        "title": "Glenn Youngkin 首次亞洲行首站選擇台灣",
        "excerpt": "美國維吉尼亞州州長 Glenn Youngkin 於 2023 年 4 月率團訪台，將台灣作為其首次亞洲行首站，並聚焦經濟發展機會、共同優先事項、國家安全與雙邊經貿合作。",
        "date_published": "2023-04-24T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Glenn Youngkin", "chinese_aliases": ["楊金", "楊京"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/aipl/202304240015.aspx",
                "source_type": "media",
                "source_title": "美國維吉尼亞州長來訪 首次亞洲行首站選擇台灣",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "eric_holcomb_visit_taiwan_2022",
        "title": "Eric Holcomb 訪台並深化印第安納州與台灣合作",
        "excerpt": "美國印第安納州州長 Eric Holcomb 於 2022 年 8 月率團訪台，將晉見總統並拜訪半導體廠商，並簽署多項促進台灣與印第安納州經貿、科技及產學合作的備忘錄。",
        "date_published": "2022-08-22T00:00:00",
        "statement_type": "visit",
        "participants": [
            {"name": "Eric Holcomb", "chinese_aliases": ["侯康安"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/ahel/202208225001.aspx",
                "source_type": "media",
                "source_title": "美國印第安納州長侯康安訪台 將見蔡總統拜訪半導體廠商",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
        ],
    },
    {
        "slug": "greg_abbott_texas_investment_taiwan_2025",
        "title": "Greg Abbott 與台灣推進德州投資合作",
        "excerpt": "2025 年 5 月的報導指出，台灣正深化對德州投資，並提到台灣經濟部長去年曾與德州州長 Greg Abbott 在台灣簽署德州與台灣經濟發展意向聲明，雙方將在半導體、電動車、能源韌性及創新科技等領域加強合作。",
        "date_published": "2025-05-25T00:00:00",
        "statement_type": "investment_cooperation",
        "participants": [
            {"name": "Greg Abbott", "chinese_aliases": ["艾波特"]},
        ],
        "sources": [
            {
                "source_url": "https://www.cna.com.tw/news/afe/202505250191.aspx",
                "source_type": "media",
                "source_title": "郭智輝：美國若想再偉大須考慮台灣 德州為投資重點",
                "parser_identity": "manual_taiwan_source_seed_v1",
                "is_primary_source": False,
            },
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
                    "parser_identity": "manual_taiwan_governor_seed_v1",
                    "raw_payload": {"manual_seed": True, "seed_context": "taiwan_governor_event_seed"},
                }
            )
        for alias in participant.get("chinese_aliases", []):
            officials_service.ensure_alias(
                person.id,
                alias,
                source_url="https://www.cna.com.tw/",
                source_type="media",
                alias_type="chinese_name",
            )
        if person.id not in person_ids:
            person_ids.append(person.id)
    return person_ids


def run_seed_taiwan_governor_sample_events() -> dict:
    with session_scope() as session:
        officials_service = OfficialsService(session)
        statements_service = StatementsService(session)
        sync_run = SyncRun(
            job_name="seed_taiwan_governor_sample_events",
            job_type="statement_seed",
            source_name="governor_taiwan_manual_seed",
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
                            "seeded_from": "manual_taiwan_governor_sources",
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
            "job_name": "seed_taiwan_governor_sample_events",
            "events_processed": events_processed,
            "records_created": created_count,
            "records_updated": updated_count,
            "sources_processed": sources_processed,
        }
