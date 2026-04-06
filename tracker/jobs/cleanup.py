from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from tracker.db import session_scope
from tracker.models import Statement, StatementMention, StatementParticipant, StatementSource


def run_cleanup() -> dict:
    merged_groups = 0
    statements_deleted = 0
    participants_added = 0
    sources_moved = 0
    mentions_moved = 0

    with session_scope() as session:
        statements = session.execute(
            select(Statement).order_by(
                Statement.date_published.asc().nullsfirst(),
                Statement.title.asc(),
                Statement.id.asc(),
            )
        ).scalars().all()

        grouped: dict[tuple[object, ...], list[Statement]] = defaultdict(list)
        for statement in statements:
            key = (
                statement.title,
                statement.date_published,
                statement.statement_type,
                statement.source_url,
            )
            grouped[key].append(statement)

        for group in grouped.values():
            if len(group) <= 1:
                continue
            primary = min(group, key=lambda item: item.id)
            duplicates = [item for item in group if item.id != primary.id]
            merged_groups += 1

            participant_ids = set()
            if primary.person_id:
                participant_ids.add(primary.person_id)
            for participant in session.execute(
                select(StatementParticipant).where(StatementParticipant.statement_id == primary.id)
            ).scalars():
                participant_ids.add(participant.person_id)

            for duplicate in duplicates:
                if duplicate.person_id:
                    participant_ids.add(duplicate.person_id)
                for participant in session.execute(
                    select(StatementParticipant).where(StatementParticipant.statement_id == duplicate.id)
                ).scalars():
                    participant_ids.add(participant.person_id)

                for source in session.execute(
                    select(StatementSource).where(StatementSource.statement_id == duplicate.id)
                ).scalars():
                    existing_source = session.execute(
                        select(StatementSource).where(
                            StatementSource.statement_id == primary.id,
                            StatementSource.source_url == source.source_url,
                        )
                    ).scalar_one_or_none()
                    if existing_source:
                        session.delete(source)
                    else:
                        source.statement_id = primary.id
                        sources_moved += 1

                for mention in session.execute(
                    select(StatementMention).where(StatementMention.statement_id == duplicate.id)
                ).scalars():
                    mention.statement_id = primary.id
                    mentions_moved += 1

            existing_primary_participants = {
                participant.person_id
                for participant in session.execute(
                    select(StatementParticipant).where(StatementParticipant.statement_id == primary.id)
                ).scalars()
            }
            for person_id in sorted(participant_ids):
                if person_id in existing_primary_participants:
                    continue
                session.add(
                    StatementParticipant(
                        statement_id=primary.id,
                        person_id=person_id,
                        source_url=primary.source_url,
                        source_type=primary.source_type,
                    )
                )
                participants_added += 1

            for duplicate in duplicates:
                session.delete(duplicate)
                statements_deleted += 1

    return {
        "status": "success",
        "job_name": "cleanup",
        "merged_groups": merged_groups,
        "records_deleted": statements_deleted,
        "participants_added": participants_added,
        "sources_moved": sources_moved,
        "mentions_moved": mentions_moved,
    }
