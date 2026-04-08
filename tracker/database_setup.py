from __future__ import annotations

from dataclasses import dataclass
import gzip
import logging
import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from sqlalchemy.exc import SQLAlchemyError

from tracker import models  # noqa: F401
from tracker.db import Base, engine
from scripts.init_db import ensure_sqlite_columns

logger = logging.getLogger(__name__)


@dataclass
class DatabaseReadyResult:
    ok: bool
    message: str | None = None
    safe_database_url: str | None = None


def ensure_database_ready() -> DatabaseReadyResult:
    safe_database_url = _safe_database_url(str(engine.url))
    try:
        _restore_sqlite_from_bootstrap_snapshot_if_needed()
        Base.metadata.create_all(bind=engine)
        ensure_sqlite_columns()
        return DatabaseReadyResult(ok=True, safe_database_url=safe_database_url)
    except (SQLAlchemyError, sqlite3.Error, OSError, Exception) as exc:
        return DatabaseReadyResult(
            ok=False,
            message=f"{type(exc).__name__}: {exc}",
            safe_database_url=safe_database_url,
        )


def _safe_database_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    if parsed.scheme.startswith("sqlite"):
        return database_url
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    safe_query = ""
    if query:
        allowed = {key: value for key, value in query.items() if key.lower() in {"sslmode", "host", "port"}}
        if allowed:
            safe_query = "?" + "&".join(f"{key}={value}" for key, value in allowed.items())
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    database_name = parsed.path.lstrip("/")
    return f"{parsed.scheme}://{host}{port}/{database_name}{safe_query}"


def _restore_sqlite_from_bootstrap_snapshot_if_needed() -> None:
    database_url = str(engine.url)
    if not database_url.startswith("sqlite:///"):
        return

    target_path = Path(database_url.removeprefix("sqlite:///"))
    snapshot_path = Path(__file__).resolve().parent.parent / "config" / "bootstrap_tracker.sql.gz"
    if not snapshot_path.exists():
        return

    # Safety rule: only bootstrap when DB file is missing or truly empty.
    # Never overwrite a non-empty DB during deploy/startup.
    if target_path.exists() and _sqlite_data_score(target_path) > 0:
        return

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()

    with gzip.open(snapshot_path, "rt", encoding="utf-8") as source:
        dump_sql = source.read()

    try:
        with sqlite3.connect(target_path) as connection:
            connection.executescript(dump_sql)
            connection.commit()
    except sqlite3.Error as exc:
        # In some cloud environments the bundled bootstrap SQL may fail to apply
        # (sqlite version mismatch / partial script incompatibility). Fall back to
        # an empty sqlite file so Base.metadata.create_all() can still initialize.
        logger.warning("Bootstrap snapshot restore failed for %s: %s", target_path, exc)
        try:
            if target_path.exists():
                target_path.unlink()
            sqlite3.connect(target_path).close()
        except sqlite3.Error:
            pass


def _sqlite_data_score(path: Path) -> int:
    if not path.exists() or path.is_dir():
        return -1
    try:
        with sqlite3.connect(path) as connection:
            table_names = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            score = 0
            for table_name in ("persons", "statements", "legislation", "trackers"):
                if table_name not in table_names:
                    continue
                row = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
                score += int(row[0] or 0) if row else 0
            return score
    except sqlite3.Error:
        return -1
