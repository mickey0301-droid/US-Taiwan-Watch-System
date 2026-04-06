from __future__ import annotations

from tracker import models  # noqa: F401
from tracker.db import Base, engine
from scripts.init_db import ensure_sqlite_columns


def ensure_database_ready() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_columns()
