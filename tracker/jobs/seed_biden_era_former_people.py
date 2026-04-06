from __future__ import annotations

from datetime import datetime

from tracker.db import session_scope
from tracker.models import SyncRun
from tracker.services.officials_service import OfficialsService


FORMER_FEDERAL_EXECUTIVES = [
    {"name": "Antony Blinken", "role_title": "Secretary of State", "office_name": "Secretary of State", "aliases": ["布林肯"]},
    {"name": "Jake Sullivan", "role_title": "National Security Advisor", "office_name": "National Security Council", "aliases": ["蘇利文"]},
    {"name": "Wendy Sherman", "role_title": "Deputy Secretary of State", "office_name": "Deputy Secretary of State", "aliases": ["雪蔓"]},
    {"name": "Kurt Campbell", "role_title": "Deputy Secretary of State", "office_name": "Deputy Secretary of State", "aliases": ["康貝爾"]},
    {"name": "Daniel Kritenbrink", "role_title": "Assistant Secretary of State for East Asian and Pacific Affairs", "office_name": "Assistant Secretary of State for East Asian and Pacific Affairs", "aliases": ["康達"]},
    {"name": "Laura Rosenberger", "role_title": "Chair of the American Institute in Taiwan", "office_name": "American Institute in Taiwan", "aliases": ["羅森柏格"]},
    {"name": "Ely Ratner", "role_title": "Assistant Secretary of Defense for Indo-Pacific Security Affairs", "office_name": "Assistant Secretary of Defense for Indo-Pacific Security Affairs", "aliases": ["瑞特納"]},
    {"name": "Lloyd Austin", "role_title": "Secretary of Defense", "office_name": "Secretary of Defense", "aliases": ["奧斯汀"]},
    {"name": "Gina Raimondo", "role_title": "Secretary of Commerce", "office_name": "Secretary of Commerce", "aliases": ["雷蒙多"]},
    {"name": "Katherine Tai", "role_title": "United States Trade Representative", "office_name": "Office of the United States Trade Representative", "aliases": ["戴琪"]},
    {"name": "Rahm Emanuel", "role_title": "Ambassador to Japan", "office_name": "United States Ambassador to Japan", "aliases": ["艾曼紐"]},
    {"name": "Nicholas Burns", "role_title": "Ambassador to China", "office_name": "United States Ambassador to China", "aliases": ["伯恩斯"]},
    {"name": "Bonnie Jenkins", "role_title": "Under Secretary of State for Arms Control and International Security Affairs", "office_name": "Under Secretary of State for Arms Control and International Security Affairs", "aliases": ["詹金斯"]},
    {"name": "Jessica Lewis", "role_title": "Assistant Secretary of State for Political-Military Affairs", "office_name": "Assistant Secretary of State for Political-Military Affairs", "aliases": ["路易斯"]},
    {"name": "Mira Rapp-Hooper", "role_title": "National Security Council Senior Director for East Asia and Oceania", "office_name": "National Security Council", "aliases": ["胡米拉"]},
    {"name": "Celeste Wallander", "role_title": "Assistant Secretary of Defense for International Security Affairs", "office_name": "Assistant Secretary of Defense for International Security Affairs", "aliases": ["華蘭德"]},
    {"name": "John Kirby", "role_title": "National Security Council Coordinator for Strategic Communications", "office_name": "National Security Council", "aliases": ["柯比"]},
]

FORMER_CONGRESS_LEGISLATORS = [
    {"name": "Mike Gallagher", "role_title": "Representative", "office_name": "U.S. House of Representatives", "chamber": "house", "state": "Wisconsin", "aliases": ["蓋拉格"]},
    {"name": "Mitt Romney", "role_title": "Senator", "office_name": "United States Senator", "chamber": "senate", "state": "Utah", "aliases": ["羅姆尼"]},
    {"name": "Ben Cardin", "role_title": "Senator", "office_name": "United States Senator", "chamber": "senate", "state": "Maryland", "aliases": ["卡丁"]},
    {"name": "Bob Menendez", "role_title": "Senator", "office_name": "United States Senator", "chamber": "senate", "state": "New Jersey", "aliases": ["梅南德茲"]},
    {"name": "Joe Manchin", "role_title": "Senator", "office_name": "United States Senator", "chamber": "senate", "state": "West Virginia", "aliases": ["曼欽"]},
    {"name": "Kyrsten Sinema", "role_title": "Senator", "office_name": "United States Senator", "chamber": "senate", "state": "Arizona", "aliases": ["希內瑪"]},
    {"name": "Debbie Lesko", "role_title": "Representative", "office_name": "U.S. House of Representatives", "chamber": "house", "state": "Arizona", "aliases": ["萊斯可"]},
    {"name": "Dan Kildee", "role_title": "Representative", "office_name": "U.S. House of Representatives", "chamber": "house", "state": "Michigan", "aliases": ["克爾帝"]},
    {"name": "Cathy McMorris Rodgers", "role_title": "Representative", "office_name": "U.S. House of Representatives", "chamber": "house", "state": "Washington", "aliases": ["麥莫里斯羅傑斯"]},
    {"name": "Kay Granger", "role_title": "Representative", "office_name": "U.S. House of Representatives", "chamber": "house", "state": "Texas", "aliases": ["格蘭傑"]},
    {"name": "Patrick McHenry", "role_title": "Representative", "office_name": "U.S. House of Representatives", "chamber": "house", "state": "North Carolina", "aliases": ["麥亨利"]},
    {"name": "Michael Burgess", "role_title": "Representative", "office_name": "U.S. House of Representatives", "chamber": "house", "state": "Texas", "aliases": ["勃格斯"]},
    {"name": "Derek Kilmer", "role_title": "Representative", "office_name": "U.S. House of Representatives", "chamber": "house", "state": "Washington", "aliases": ["基爾默"]},
    {"name": "Earl Blumenauer", "role_title": "Representative", "office_name": "U.S. House of Representatives", "chamber": "house", "state": "Oregon", "aliases": ["布魯梅諾"]},
]


def run_seed_biden_era_former_people() -> dict:
    with session_scope() as session:
        service = OfficialsService(session)
        sync_run = SyncRun(
            job_name="seed_biden_era_former_people",
            job_type="people_seed",
            source_name="biden_era_former_people_manual_seed",
        )
        session.add(sync_run)
        session.flush()

        usa = service.get_or_create_jurisdiction("United States", "country", code="US")
        created = 0
        updated = 0
        found = 0

        for item in FORMER_FEDERAL_EXECUTIVES:
            found += 1
            office = service.get_or_create_office(
                item["office_name"],
                "federal",
                "executive",
                None,
                usa.id,
                "https://en.wikipedia.org/wiki/First_cabinet_of_Joe_Biden",
                "wikipedia",
            )
            person, was_created = service.upsert_person(
                {
                    "full_name": item["name"],
                    "source_url": "https://en.wikipedia.org/wiki/First_cabinet_of_Joe_Biden",
                    "source_type": "wikipedia",
                    "seed_source_type": "wikipedia",
                    "profile_status": "seeded",
                    "verification_status": "unverified",
                    "raw_payload": {"seed_context": "biden_era_former_people", "group": "former_federal_executive"},
                }
            )
            created += 1 if was_created else 0
            updated += 0 if was_created else 1
            for alias in item.get("aliases", []):
                service.ensure_alias(person.id, alias, "https://en.wikipedia.org/wiki/First_cabinet_of_Joe_Biden", "wikipedia", alias_type="chinese_name")
            if service.upsert_appointment(
                person,
                office,
                usa.id,
                {
                    "role_title": item["role_title"],
                    "status": "former",
                    "source_url": "https://en.wikipedia.org/wiki/First_cabinet_of_Joe_Biden",
                    "source_type": "wikipedia",
                    "parser_identity": "biden_era_former_people_v1",
                    "is_current": False,
                    "raw_payload": {"seed_context": "biden_era_former_people", "group": "former_federal_executive"},
                },
            ):
                created += 1

        for item in FORMER_CONGRESS_LEGISLATORS:
            found += 1
            state = service.get_or_create_jurisdiction(item["state"], "state", code=item["state"], parent_id=usa.id)
            office = service.get_or_create_office(
                item["office_name"],
                "federal",
                "legislative",
                item["chamber"],
                state.id,
                "https://en.wikipedia.org/wiki/118th_United_States_Congress",
                "wikipedia",
            )
            person, was_created = service.upsert_person(
                {
                    "full_name": item["name"],
                    "source_url": "https://en.wikipedia.org/wiki/118th_United_States_Congress",
                    "source_type": "wikipedia",
                    "seed_source_type": "wikipedia",
                    "profile_status": "seeded",
                    "verification_status": "unverified",
                    "raw_payload": {"seed_context": "biden_era_former_people", "group": "former_congress_legislator"},
                }
            )
            created += 1 if was_created else 0
            updated += 0 if was_created else 1
            for alias in item.get("aliases", []):
                service.ensure_alias(person.id, alias, "https://en.wikipedia.org/wiki/118th_United_States_Congress", "wikipedia", alias_type="chinese_name")
            if service.upsert_appointment(
                person,
                office,
                state.id,
                {
                    "role_title": item["role_title"],
                    "status": "former",
                    "source_url": "https://en.wikipedia.org/wiki/118th_United_States_Congress",
                    "source_type": "wikipedia",
                    "parser_identity": "biden_era_former_people_v1",
                    "is_current": False,
                    "raw_payload": {"seed_context": "biden_era_former_people", "group": "former_congress_legislator"},
                },
            ):
                created += 1

        sync_run.ended_at = datetime.utcnow()
        sync_run.status = "success"
        sync_run.records_found = found
        sync_run.records_created = created
        sync_run.records_updated = updated
        return {
            "status": "success",
            "job_name": "seed_biden_era_former_people",
            "records_found": found,
            "records_created": created,
            "records_updated": updated,
        }
