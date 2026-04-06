from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse

from sqlalchemy.exc import SQLAlchemyError

from tracker import models  # noqa: F401
from tracker.db import Base, engine
from scripts.init_db import ensure_sqlite_columns


@dataclass
class DatabaseReadyResult:
    ok: bool
    message: str | None = None
    safe_database_url: str | None = None


def ensure_database_ready() -> DatabaseReadyResult:
    safe_database_url = _safe_database_url(str(engine.url))
    try:
        Base.metadata.create_all(bind=engine)
        ensure_sqlite_columns()
        return DatabaseReadyResult(ok=True, safe_database_url=safe_database_url)
    except SQLAlchemyError as exc:
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
