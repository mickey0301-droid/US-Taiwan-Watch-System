from __future__ import annotations

import argparse
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, text

from tracker import models  # noqa: F401
from tracker.config import _normalize_database_url  # type: ignore[attr-defined]
from tracker.db import Base


def _coerce_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value
    return value


def _build_target_url(raw: str) -> str:
    normalized = _normalize_database_url(raw.strip())
    if not normalized.startswith("postgresql+psycopg://"):
        raise ValueError("Target URL must be postgresql+psycopg://...")
    return normalized


def _truncate_target(connection, tables: list[str]) -> None:
    if not tables:
        return
    quoted = ", ".join(f"\"{name}\"" for name in tables)
    connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


def _copy_table(source_conn, target_conn, table: Table, chunk_size: int = 1000) -> int:
    total = 0
    result = source_conn.exec_driver_sql(f'SELECT * FROM "{table.name}"')
    while True:
        rows = result.fetchmany(chunk_size)
        if not rows:
            break
        payload = []
        for row in rows:
            mapping = {}
            for column in table.columns:
                mapping[column.name] = _coerce_value(row._mapping.get(column.name))
            payload.append(mapping)
        if payload:
            target_conn.execute(table.insert(), payload)
            total += len(payload)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate local SQLite tracker DB to PostgreSQL.")
    parser.add_argument("--source-sqlite", default="data/tracker.db", help="Path to source SQLite file")
    parser.add_argument("--target-url", default="", help="PostgreSQL URL, fallback to TRACKER_DATABASE_URL env")
    parser.add_argument("--truncate-target", action="store_true", help="Truncate target tables before copy")
    args = parser.parse_args()

    source_path = Path(args.source_sqlite).resolve()
    if not source_path.exists():
        raise SystemExit(f"Source sqlite not found: {source_path}")

    raw_target = args.target_url or os.getenv("TRACKER_DATABASE_URL", "")
    if not raw_target:
        raise SystemExit("Missing target URL. Use --target-url or set TRACKER_DATABASE_URL.")
    target_url = _build_target_url(raw_target)
    source_url = f"sqlite:///{source_path.as_posix()}"

    source_engine = create_engine(source_url, future=True)
    target_engine = create_engine(target_url, future=True)

    Base.metadata.create_all(bind=target_engine)
    metadata = MetaData()
    metadata.reflect(bind=target_engine)
    table_order = [table.name for table in Base.metadata.sorted_tables]
    tables = [metadata.tables[name] for name in table_order if name in metadata.tables]

    with source_engine.connect() as source_conn, target_engine.begin() as target_conn:
        if args.truncate_target:
            _truncate_target(target_conn, [table.name for table in reversed(tables)])
        copied: dict[str, int] = {}
        for table in tables:
            copied[table.name] = _copy_table(source_conn, target_conn, table)
            print(f"{table.name}: {copied[table.name]}")

    print("Done.")


if __name__ == "__main__":
    main()
