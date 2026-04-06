# US Taiwan Watch

US Taiwan Watch is a local-first Python + Streamlit monitoring system for tracking Taiwan-related statements by U.S. public officials. Version 1 focuses on a durable SQLite-backed foundation, historical record retention, and modular collectors that can later expand to county and city officials.

## Current scaffold

- Modular Python project under `tracker/`
- SQLite + SQLAlchemy schema for officials, appointments, trackers, statements, sync runs, and notifications
- Streamlit shell app with Traditional Chinese default UI and English toggle
- APScheduler job registry
- Notification abstraction with a working webhook notifier
- CLI scripts for DB initialization and one-off job execution
- First working collector: U.S. Senate official directory sync
- Placeholder collector modules for House, governors, and state legislatures

## Install

1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Optionally copy `.env.example` to `.env`.

## Run

Initialize the database:

```bash
python -m scripts.init_db
```

Start the Streamlit app:

```bash
streamlit run app.py
```

Run the officials sync once:

```bash
python -m scripts.run_sync_once sync_officials
```

Run tracker-based statement collection:

```bash
python -m scripts.run_sync_once sync_trackers
```

Run the Senate collector directly:

```bash
python -m tracker.collectors.congress_senate
```

## Scheduler

The job registry is in `tracker/scheduler.py`, and intervals are configured in `config/settings.yaml`.

Configured jobs:

- `sync_officials`
- `sync_trackers`
- `sync_media`
- `cleanup`

The current phase includes APScheduler integration and a sample registry. A dedicated background runner can be added next so scheduled updates run outside Streamlit.

## Tracker and statement flow

1. Run `sync_officials` to populate people.
2. You can also open `Officials` and import a Wikipedia list page to seed current or former people in bulk.
3. Create a tracker for one person and add one or more target lines in `type|name|url` format.
4. Use `Run this tracker now` in the UI or run `python -m scripts.run_sync_once sync_trackers`.
5. Review detected items in `Statements Review Queue`.
6. Open `Person Detail` to see office history, recent statements, media reports, and last tracker sync status.

Supported target types now include:

- `official_website`
- `press_release_page`
- `rss_feed`
- `hearing_page`
- `cspan_search_target`
- `social_page`
- `activity_page`
- `media_search_target`
- `activity_media_target`

`cspan_search_target` is designed for C-SPAN search result pages or person/video pages. Records collected from C-SPAN are stored as secondary sources and can attach to an existing event alongside official or media sources.

Wikipedia import is treated as a seed identity source only. It helps the system learn who to track, but official websites, official feeds, and other primary sources remain the preferred basis for enriched profile data such as portrait, canonical website, and statement collection.

## Federal legislator sources

- Primary official source: Congress.gov API when `CONGRESS_API_KEY` is configured
- Official web fallback currently in use: House directory and other official pages
- Secondary enrichment: GovTrack search links are stored as auxiliary references and do not override official profile fields

## Configuration

Editable files:

- `config/settings.yaml`
- `config/keywords.yaml`
- `config/source_registry.yaml`

Current environment override:

- `TRACKER_DATABASE_URL`
- `CONGRESS_API_KEY`
- `GOOGLE_SERVICE_ACCOUNT_FILE`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_SHEET_ID`

## Google Sheet integration

The system can be connected to a Google Sheet for `People`, `Events`, and `Legislation` tabs.

Required environment variables:

- `GOOGLE_SERVICE_ACCOUNT_FILE` or `GOOGLE_SERVICE_ACCOUNT_JSON`
- `GOOGLE_SHEET_ID`

Example PowerShell session:

```powershell
$env:GOOGLE_SERVICE_ACCOUNT_FILE="C:\Users\pitch\secrets\google-service-account.json"
$env:GOOGLE_SHEET_ID="1L0du5spYv6P_8t54QV0Z-mvZd4s3IhcPT_-hiPRzx2k"
```

For GitHub Actions or Streamlit deployment, prefer `GOOGLE_SERVICE_ACCOUNT_JSON` so you can paste the full service account JSON into a secret instead of relying on a local file path.

## GitHub and Streamlit deployment notes

- This project should be published as its own Git repository, not as part of a larger home-directory repository.
- Do not commit:
  - local SQLite databases
  - Google service account JSON files
  - `.env`
  - `.streamlit/secrets.toml`
- Use:
  - `.streamlit/config.toml` for Streamlit app settings
  - `.streamlit/secrets.toml` on the deployment target for secrets
  - `runtime.txt` to pin the Python version for Streamlit deployment

Recommended Streamlit secrets:

```toml
CONGRESS_API_KEY = "..."
GOOGLE_SHEET_ID = "..."
GOOGLE_SERVICE_ACCOUNT_JSON = """{ ... full JSON ... }"""
```

After configuring those values and installing dependencies, test the connection with:

```powershell
python -m scripts.test_google_sheet_connection
```

The service account email must be explicitly shared on the Google Sheet with `Editor` access.

Initialize the standard headers for the `People`, `Events`, and `Legislation` tabs with:

```powershell
python -m scripts.init_google_sheet_headers
```

Export the current system people data into the `People` worksheet with:

```powershell
python -m scripts.sync_google_sheet_people
```

Export the current event data into the `Events` worksheet with:

```powershell
python -m scripts.sync_google_sheet_events
```

Export the current legislation data into the `Legislation` worksheet with:

```powershell
python -m scripts.sync_google_sheet_legislation
```

## Schema design

Historical tracking tables:

- `persons`
- `aliases`
- `jurisdictions`
- `offices`
- `appointments`
- `trackers`
- `tracker_targets`
- `statements`
- `statement_mentions`
- `statement_sources`
- `sync_runs`
- `notifications_log`

Core fields such as `first_seen_at`, `last_seen_at`, `is_current`, `source_url`, `source_type`, and parser metadata are included to support incremental sync and later historical comparison logic.

## Add a new collector

1. Create a module under `tracker/collectors/`.
2. Implement `fetch()`, `parse()`, and `sync()`.
3. Normalize output into `person`, `jurisdiction`, `office`, `appointment`, and optional `aliases`.
4. Reuse `OfficialsService` for upsert logic.
5. Register the runnable job in `tracker/scheduler.py`.
6. Add source metadata to `config/source_registry.yaml`.

## Add a new notification provider

1. Create a notifier under `tracker/notifications/`.
2. Implement `BaseNotifier.send()`.
3. Add provider config to `config/settings.yaml`.
4. Wire logging to `notifications_log` in the future notification service layer.

The webhook notifier is the first working provider. Email is scaffolded as a stub.

## TODO for next priorities

- Implement state-legislature registry / plugin structure
- Add notification triggers for new statements and office changes
- Add office-change detection alerts
- Improve generic HTML target parsing with source-specific parsers
- Add manual statement editing and bulk review workflows
- Add richer social-source adapters instead of relying only on generic HTML/RSS fetches
- Add list-type detection and office classification for imported Wikipedia pages
- Add county and city extension points without changing core schema
- TODO: plug in AI relevance classification and later summarization after the rule-based filter
