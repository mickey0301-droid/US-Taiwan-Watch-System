from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session


_TABLES_WITH_ID_SEQUENCES = {
    "aliases",
    "appointments",
    "jurisdictions",
    "legislation",
    "legislation_sources",
    "legislation_sponsors",
    "offices",
    "persons",
    "statement_participants",
    "statement_sources",
    "statements",
}


def sync_postgres_id_sequences(session: Session, table_names: Iterable[str]) -> None:
    """Move PostgreSQL id sequences past imported rows.

    SQLite imports and manual backfills can leave PostgreSQL sequences behind
    the current max(id). When that happens, the next insert tries to reuse an
    existing primary key. This helper is intentionally a no-op outside
    PostgreSQL and only accepts table names from a fixed allowlist.
    """
    bind = session.get_bind()
    if bind.dialect.name not in {"postgresql", "postgres"}:
        return

    for table_name in dict.fromkeys(str(name) for name in table_names):
        if table_name not in _TABLES_WITH_ID_SEQUENCES:
            continue
        session.execute(
            text(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table_name}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table_name}), 0) + 1,
                    false
                )
                """
            )
        )
