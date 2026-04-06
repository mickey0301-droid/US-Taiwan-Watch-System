from __future__ import annotations

from sqlalchemy import inspect, text

from tracker import models  # noqa: F401
from tracker.db import Base, engine
from tracker.logging_utils import configure_logging


SQLITE_COLUMN_MIGRATIONS = {
    "persons": {
        "date_of_birth": "DATE",
        "place_of_birth": "VARCHAR(255)",
        "ethnicity": "VARCHAR(255)",
        "religion": "VARCHAR(255)",
        "education": "TEXT",
        "career_history": "TEXT",
        "bio": "TEXT",
        "seed_source_type": "VARCHAR(50)",
        "profile_status": "VARCHAR(50) DEFAULT 'seeded'",
        "canonical_official_url": "VARCHAR(1024)",
        "portrait_url": "VARCHAR(1024)",
        "portrait_source_url": "VARCHAR(1024)",
        "portrait_source_type": "VARCHAR(50)",
        "social_profiles": "JSON",
        "parser_identity": "VARCHAR(255)",
        "verification_status": "VARCHAR(50) DEFAULT 'unverified'",
        "raw_payload": "JSON",
    },
    "statements": {
        "canonical_event_key": "VARCHAR(255)",
        "event_source_preference": "VARCHAR(50)",
        "is_primary_source": "BOOLEAN DEFAULT 1 NOT NULL",
        "matched_keywords": "JSON",
        "raw_payload": "JSON",
    },
    "statement_sources": {
        "source_title": "VARCHAR(500)",
        "parser_identity": "VARCHAR(255)",
        "is_primary": "BOOLEAN DEFAULT 0 NOT NULL",
        "raw_payload": "JSON",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    },
}


SQLITE_TABLE_CREATION = {
    "statement_participants": """
        CREATE TABLE statement_participants (
            id INTEGER NOT NULL PRIMARY KEY,
            statement_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            role VARCHAR(100),
            source_url VARCHAR(1024),
            source_type VARCHAR(50),
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE(statement_id, person_id),
            FOREIGN KEY(statement_id) REFERENCES statements (id),
            FOREIGN KEY(person_id) REFERENCES persons (id)
        )
    """,
    "legislation": """
        CREATE TABLE legislation (
            id INTEGER NOT NULL PRIMARY KEY,
            title VARCHAR(500) NOT NULL,
            bill_number VARCHAR(100),
            bill_slug VARCHAR(255) NOT NULL,
            legislation_type VARCHAR(100),
            level VARCHAR(50) NOT NULL,
            jurisdiction_name VARCHAR(255),
            jurisdiction_id INTEGER,
            chamber VARCHAR(50),
            summary TEXT,
            status_text VARCHAR(255),
            introduced_date DATE,
            last_action_date DATE,
            source_url VARCHAR(1024) NOT NULL,
            source_type VARCHAR(50) NOT NULL,
            parser_identity VARCHAR(255),
            relevance_score FLOAT,
            is_taiwan_related BOOLEAN NOT NULL DEFAULT 1,
            raw_payload JSON,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE(bill_slug),
            FOREIGN KEY(jurisdiction_id) REFERENCES jurisdictions (id)
        )
    """,
    "legislation_sponsors": """
        CREATE TABLE legislation_sponsors (
            id INTEGER NOT NULL PRIMARY KEY,
            legislation_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            role VARCHAR(100) NOT NULL,
            source_url VARCHAR(1024),
            source_type VARCHAR(50),
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE(legislation_id, person_id, role),
            FOREIGN KEY(legislation_id) REFERENCES legislation (id),
            FOREIGN KEY(person_id) REFERENCES persons (id)
        )
    """,
    "legislation_sources": """
        CREATE TABLE legislation_sources (
            id INTEGER NOT NULL PRIMARY KEY,
            legislation_id INTEGER NOT NULL,
            source_url VARCHAR(1024) NOT NULL,
            source_type VARCHAR(50) NOT NULL,
            source_title VARCHAR(500),
            parser_identity VARCHAR(255),
            collected_at DATETIME NOT NULL,
            raw_payload JSON,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            UNIQUE(legislation_id, source_url),
            FOREIGN KEY(legislation_id) REFERENCES legislation (id)
        )
    """,
}


def ensure_sqlite_columns() -> None:
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    with engine.begin() as connection:
        existing_tables = set(inspector.get_table_names())
        for table_name, ddl in SQLITE_TABLE_CREATION.items():
            if table_name not in existing_tables:
                connection.execute(text(ddl))
        for table_name, columns in SQLITE_COLUMN_MIGRATIONS.items():
            if table_name not in inspector.get_table_names():
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, ddl in columns.items():
                if column_name in existing_columns:
                    continue
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))


def main() -> None:
    configure_logging()
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_columns()
    print("Database initialized.")


if __name__ == "__main__":
    main()
