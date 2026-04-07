from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from tracker.config import get_settings


logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()


def _engine_kwargs_for(database_url: str) -> dict:
    engine_kwargs = {"future": True, "echo": False}
    if database_url.startswith("sqlite"):
        sqlite_path = database_url.removeprefix("sqlite:///")
        if sqlite_path and sqlite_path != ":memory:":
            Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        engine_kwargs["connect_args"] = {"timeout": 30}
    return engine_kwargs


def _fallback_sqlite_url() -> str:
    fallback_path = (Path(__file__).resolve().parent.parent / "data" / "tracker.db").resolve()
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{fallback_path.as_posix()}"


effective_database_url = settings.database_url
try:
    engine = create_engine(effective_database_url, **_engine_kwargs_for(effective_database_url))
except Exception as exc:  # pragma: no cover - protects cloud startup misconfig
    fallback_url = _fallback_sqlite_url()
    logger.warning(
        "Primary database init failed for %s; fallback to bundled sqlite %s (%s: %s)",
        effective_database_url,
        fallback_url,
        type(exc).__name__,
        exc,
    )
    effective_database_url = fallback_url
    engine = create_engine(effective_database_url, **_engine_kwargs_for(effective_database_url))

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


if effective_database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Session:
    return SessionLocal()
