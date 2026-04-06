from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"


class AppSettings(BaseModel):
    app_name: str = "US Taiwan Watch"
    default_language: str = "zh-TW"
    supported_languages: list[str] = Field(default_factory=lambda: ["zh-TW", "en"])
    database_url: str = Field(default_factory=lambda: f"sqlite:///{(BASE_DIR / 'data' / 'tracker.db').as_posix()}")
    congress_api_key: str | None = None
    google_service_account_file: str | None = None
    google_service_account_json: str | None = None
    google_sheet_id: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    congress_current_number: int = 119
    log_level: str = "INFO"
    timezone: str = "Asia/Taipei"
    raw_data_dir: str = Field(default_factory=lambda: str(DATA_DIR / "raw"))
    processed_data_dir: str = Field(default_factory=lambda: str(DATA_DIR / "processed"))
    snapshots_dir: str = Field(default_factory=lambda: str(DATA_DIR / "snapshots"))
    snapshot_raw_responses: bool = True
    scheduler_enabled: bool = True
    scheduler_timezone: str = "Asia/Taipei"
    historical_seed_cutoff_year: int = 1990
    scheduler_jobs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    notifications: dict[str, Any] = Field(default_factory=dict)
    source_priority: list[str] = Field(default_factory=list)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _streamlit_secret(name: str) -> str | None:
    try:
        import streamlit as st
    except Exception:
        return None
    try:
        value = st.secrets.get(name)
    except Exception:
        return None
    return str(value) if value not in (None, "") else None


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    raw = _load_yaml(CONFIG_DIR / "settings.yaml")
    database_url = os.getenv("TRACKER_DATABASE_URL") or _streamlit_secret("TRACKER_DATABASE_URL")
    if database_url:
        raw["database_url"] = database_url
    congress_api_key = os.getenv("CONGRESS_API_KEY") or _streamlit_secret("CONGRESS_API_KEY")
    if congress_api_key:
        raw["congress_api_key"] = congress_api_key.removeprefix("API:").strip()
    google_service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or _streamlit_secret("GOOGLE_SERVICE_ACCOUNT_FILE")
    if google_service_account_file:
        raw["google_service_account_file"] = google_service_account_file
    google_service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or _streamlit_secret("GOOGLE_SERVICE_ACCOUNT_JSON")
    if google_service_account_json:
        raw["google_service_account_json"] = google_service_account_json
    google_sheet_id = os.getenv("GOOGLE_SHEET_ID") or _streamlit_secret("GOOGLE_SHEET_ID")
    if google_sheet_id:
        raw["google_sheet_id"] = google_sheet_id
    openai_api_key = os.getenv("OPENAI_API_KEY") or _streamlit_secret("OPENAI_API_KEY")
    if openai_api_key:
        raw["openai_api_key"] = openai_api_key
    openai_model = os.getenv("OPENAI_MODEL") or _streamlit_secret("OPENAI_MODEL")
    if openai_model:
        raw["openai_model"] = openai_model
    settings = AppSettings(**raw)
    if settings.database_url.startswith("sqlite:///") and not settings.database_url.startswith("sqlite:////"):
        sqlite_path = settings.database_url.removeprefix("sqlite:///")
        settings.database_url = f"sqlite:///{(BASE_DIR / sqlite_path).resolve().as_posix()}"
    for field_name in ("raw_data_dir", "processed_data_dir", "snapshots_dir"):
        value = getattr(settings, field_name)
        path = Path(value)
        if not path.is_absolute():
            setattr(settings, field_name, str((BASE_DIR / path).resolve()))
    return settings


@lru_cache(maxsize=1)
def get_keywords() -> dict[str, Any]:
    return _load_yaml(CONFIG_DIR / "keywords.yaml")


@lru_cache(maxsize=1)
def get_source_registry() -> dict[str, Any]:
    return _load_yaml(CONFIG_DIR / "source_registry.yaml")
