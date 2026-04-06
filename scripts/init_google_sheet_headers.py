from __future__ import annotations

import json

from tracker.services.google_sheets_service import GoogleSheetsConfigurationError, GoogleSheetsService


PEOPLE_HEADERS = [
    "person_id",
    "display_name_en",
    "display_name_zh",
    "full_name",
    "given_name",
    "family_name",
    "status",
    "level",
    "branch",
    "office_title",
    "department",
    "subdepartment",
    "unit",
    "jurisdiction",
    "party",
    "district",
    "committees",
    "official_page",
    "wikipedia_page",
    "portrait_url",
    "x_accounts",
    "facebook_accounts",
    "instagram_accounts",
    "date_of_birth",
    "place_of_birth",
    "education",
    "past_experience",
    "notes",
    "updated_at",
]

EVENTS_HEADERS = [
    "event_id",
    "event_date",
    "year",
    "month",
    "title",
    "summary",
    "event_type",
    "taiwan_keywords",
    "participants_en",
    "participants_zh",
    "participant_ids",
    "primary_source_type",
    "official_sources",
    "media_sources",
    "social_sources",
    "cspan_sources",
    "wikipedia_sources",
    "review_status",
    "source_count",
    "notes",
    "updated_at",
]

LEGISLATION_HEADERS = [
    "legislation_id",
    "scope",
    "session_label",
    "session_year",
    "jurisdiction",
    "bill_number",
    "title",
    "summary",
    "status",
    "chamber",
    "date",
    "sponsors_en",
    "sponsors_zh",
    "sponsor_ids",
    "official_page",
    "seed_source",
    "topic_tags",
    "additional_topics",
    "notes",
    "updated_at",
]


def main() -> None:
    service = GoogleSheetsService()
    try:
        result = {
            "status": "success",
            "sheet_id": service.settings.google_sheet_id,
            "worksheets": [
                service.ensure_header_row("People", PEOPLE_HEADERS),
                service.ensure_header_row("Events", EVENTS_HEADERS),
                service.ensure_header_row("Legislation", LEGISLATION_HEADERS),
            ],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except GoogleSheetsConfigurationError as exc:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
