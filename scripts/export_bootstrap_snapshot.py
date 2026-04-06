from __future__ import annotations

import argparse
import gzip
import sqlite3
from pathlib import Path


def export_bootstrap_snapshot(source_db: Path, target_snapshot: Path) -> None:
    if not source_db.exists():
        raise FileNotFoundError(f"Source database not found: {source_db}")

    target_snapshot.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_db) as connection:
        dump_sql = "\n".join(connection.iterdump())

    with gzip.open(target_snapshot, "wt", encoding="utf-8", newline="\n") as handle:
        handle.write(dump_sql)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a gzipped SQLite bootstrap snapshot for cloud restores.")
    parser.add_argument(
        "--source-db",
        default=str(Path("data") / "tracker.db"),
        help="Path to the local SQLite database.",
    )
    parser.add_argument(
        "--target",
        default=str(Path("config") / "bootstrap_tracker.sql.gz"),
        help="Output path for the gzipped SQL snapshot.",
    )
    args = parser.parse_args()

    export_bootstrap_snapshot(Path(args.source_db), Path(args.target))
    print(f"Exported bootstrap snapshot to {args.target}")


if __name__ == "__main__":
    main()
