# Troubleshooting Log

This file is a running log of major issues encountered during development, what caused them, and how they were resolved or worked around.

Last updated: 2026-04-06

## 1. 系統錯誤

### 1.1 PowerShell path errors with spaces

- Symptom:
  - `cd Desktop\US Taiwan Watch` failed because PowerShell treated `Taiwan` as a separate argument.
- Cause:
  - The project folder contains spaces.
- Fix:
  - Use quotes around the full path.
- Example:
  - `cd "C:\Users\pitch\Desktop\US Taiwan Watch\taiwan_officials_tracker"`

### 1.2 Running Streamlit from the wrong folder

- Symptom:
  - `streamlit run app.py` said `File does not exist: app.py`
  - `pip install -r requirements.txt` said the file did not exist.
- Cause:
  - Commands were run from `C:\Users\pitch\Desktop\US Taiwan Watch` instead of the project folder.
- Fix:
  - Run commands from:
    - `C:\Users\pitch\Desktop\US Taiwan Watch\taiwan_officials_tracker`

### 1.3 SQLite `database is locked`

- Symptom:
  - Sync jobs failed with `sqlite3.OperationalError: database is locked`.
- Cause:
  - Streamlit and background sync jobs were reading or writing the same SQLite database at the same time.
- Fix:
  - Close old Streamlit and Python processes before large syncs.
  - Prefer running large official or Wikipedia imports one job at a time.
  - Use single-state and single-department sync jobs instead of full-batch jobs when possible.
- Current practice:
  - For large state legislature imports, run one or a few states at a time.

### 1.4 `DetachedInstanceError` in Streamlit person page

- Symptom:
  - `Instance <Person ...> is not bound to a Session`
- Cause:
  - SQLAlchemy object attributes were being accessed after the session had expired.
- Fix:
  - Set `expire_on_commit=False` in DB session configuration.
  - Convert ORM objects into plain data within session scope before rendering in the UI.

### 1.5 Streamlit navigation state errors

- Symptom:
  - `st.session_state.nav_page cannot be modified after the widget with key nav_page is instantiated`
- Cause:
  - Navigation callbacks were mutating widget-linked session state during the same rerun cycle.
- Fix:
  - Removed the old `nav_page` pattern.
  - Switched navigation to query-parameter-based links for person pages.
  - Decoupled sidebar widget state from internal page state.
- Result:
  - Event participants can now link to person pages without mutating widget state mid-run.

### 1.6 Person page jumping back to Donald Trump

- Symptom:
  - Clicking a participant link opened the person page but it snapped back to Trump.
- Cause:
  - Query params were being overwritten during normal person-page rerenders.
- Fix:
  - Added stable query-param parsing.
  - Stopped rewriting the selected person into the URL during ordinary page rendering.

### 1.7 Streamlit image API mismatch

- Symptom:
  - `ImageMixin.image() got an unexpected keyword argument 'use_container_width'`
- Cause:
  - The installed Streamlit version expects `use_column_width` or no width argument.
- Fix:
  - Removed `use_container_width` from portrait rendering.
  - Portraits now render at natural size unless explicitly changed.

### 1.8 Verification scripts pointed at the wrong database file

- Symptom:
  - Verification queries failed with:
    - `no such table: appointments`
    - `no such column: o.name`
- Cause:
  - The project has an empty root-level `tracker.db`, while the real app database is:
    - `data\tracker.db`
  - Verification SQL also used `o.name` instead of the actual `offices.office_name`.
- Fix:
  - Use:
    - `C:\Users\pitch\Desktop\US Taiwan Watch\taiwan_officials_tracker\data\tracker.db`
  - Query `offices.office_name` instead of `offices.name`.

## 2. 資料品質問題

### 2.1 Fake person names imported as officials

- Symptom:
  - Entries like `Alabama 1st`, `State 3rd`, or `District 7th` were imported as people.
- Cause:
  - Some collectors were pulling district labels instead of member names.
- Fix:
  - Tightened collector parsing logic.
  - Added common person-name validation in `OfficialsService.upsert_person`.
  - Added validation logs to `sync_run.meta`.
  - Cleaned bad records already written to the database.

### 2.2 House parser grabbed districts instead of names

- Symptom:
  - Fake people such as `Alabama 1st` appeared in federal legislator lists.
- Cause:
  - The House parser extracted text from the wrong column.
- Fix:
  - Reworked the parser to read the member-name link text directly.
  - Deleted existing bad records and re-ran the sync.

### 2.3 Wikipedia `Born` field polluted `出生地`

- Symptom:
  - A person’s full name appeared at the beginning of `出生地`.
- Cause:
  - Wikipedia `Born` text often begins with the full legal name before the date and birthplace.
- Fix:
  - Extract the leading name into `全名`.
  - Keep `生日` and `出生地` separate.

### 2.4 Past experience displayed as one block of text

- Symptom:
  - `過去經歷` appeared as a paragraph instead of a readable work history.
- Cause:
  - Background enrichment stored experience as free text.
- Fix:
  - Split previous experience into list items.
  - Prefer lines that include time markers such as years or date ranges.

### 2.5 Event duplication across multiple participants

- Symptom:
  - The same Taiwan-related event appeared multiple times, once per participant.
- Cause:
  - The old dedupe key effectively treated person-linked copies as separate statements.
- Fix:
  - Introduced event-level storage with shared participants.
  - Added `statement_participants`.
  - Removed `person_id` from event-level dedupe logic.
  - Merged existing duplicated event records.

### 2.6 Media reports missing from person pages

- Symptom:
  - A news report mentioned a person, but the person’s `媒體報導` section did not show it.
- Cause:
  - The page only looked at the event’s representative source, and some manually seeded Taiwan events had `relevance_score = 0`.
- Fix:
  - Person-page source tabs now inspect all attached event sources.
  - Taiwan-event detection also accepts seeded Taiwan events and direct `Taiwan` or `台灣` text matches.

### 2.7 Non-U.S. participants incorrectly attached to events

- Symptom:
  - Reports mentioning Taiwanese or other non-U.S. figures caused them to be added as event participants.
- Cause:
  - Name matching originally attached any matched person in the system.
- Fix:
  - Only U.S. officials and legislators are auto-attached as event participants.
  - New auto-seeded people must pass U.S.-official context checks.
  - Existing incorrect participants were removed.

### 2.8 Official page vs Wikipedia vs media confusion

- Symptom:
  - The UI sometimes treated Wikipedia or media as if they were official sources.
- Cause:
  - Source labels and source-priority logic were not strict enough.
- Fix:
  - `官方頁面` now means U.S. government pages only.
  - `維基百科` is always shown separately.
  - `媒體` is always shown separately.
  - Source priority was unified across person pages, events, and legislation.

## 3. 來源網站限制

### 3.1 Official sites returning `403`, `500`, geoblocking, or timeouts

- Symptom:
  - Some official state and federal sites failed with:
    - `403 Forbidden`
    - `500`
    - geolocation denial pages
    - timeouts
- Examples:
  - `senate.gov`
  - `nysenate.gov`
  - `assembly.state.ny.us`
  - `mass.gov`
  - Tennessee sites returning geolocation denial
- Fix / workaround:
  - Prefer official APIs or JSON feeds when available.
  - Accept official content returned with non-ideal status in special cases if HTML content is still usable.
  - Use state-by-state sync rather than full-batch sync.
  - Keep Wikipedia only as a fallback or supplemental source, not the primary source.

### 3.2 Proxy and certificate issues in the environment

- Symptom:
  - Requests sometimes failed with:
    - proxy connection errors to `127.0.0.1:9`
    - certificate verification failures
- Cause:
  - Environment-level proxy or SSL configuration was inconsistent for certain tools.
- Fix / workaround:
  - Prefer `httpx` with `trust_env=False`.
  - For some official state sites, use `verify=False` when necessary to inspect accessible content.
  - Use PowerShell or local snapshots to inspect structure when direct network access is unstable.

### 3.2.a Google Sheets OAuth blocked by bad local proxy variables

- Symptom:
  - Google Sheets test failed against `oauth2.googleapis.com` with a proxy error to `127.0.0.1:9`.
- Cause:
  - This terminal had:
    - `HTTP_PROXY=http://127.0.0.1:9`
    - `HTTPS_PROXY=http://127.0.0.1:9`
    - `ALL_PROXY=http://127.0.0.1:9`
- Fix / workaround:
  - Clear those environment variables before running Google Sheets sync or test commands:
    - `Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue`
    - `Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue`
    - `Remove-Item Env:ALL_PROXY -ErrorAction SilentlyContinue`
- Result:
  - Google Sheet connection test succeeded and confirmed the `People`, `Events`, and `Legislation` tabs exist.

### 3.3 North Carolina General Assembly blocked by Cloudflare

- Symptom:
  - `https://www.ncleg.gov/Members/MemberList/S`
  - `https://www.ncleg.gov/Members/MemberList/H`
  both returned `403`.
- Cause:
  - Cloudflare challenge page, not usable legislator content.
- Resolution:
  - Do not parse the blocked HTML.
  - Leave North Carolina out of the official-source sync queue for now.

### 3.4 Georgia General Assembly is JS app shell only

- Symptom:
  - `https://www.legis.ga.gov/members/senate`
  - `https://www.legis.ga.gov/members/house`
  returned only a JS app shell with no member list in HTML.
- Cause:
  - Frontend is SPA-only; roster data is not server-rendered.
- Resolution:
  - Requires discovering the underlying official API endpoint before adding a parser.
  - Not yet added to official sync.

### 3.5 Tennessee General Assembly geolocation denial

- Symptom:
  - Official member pages loaded a `Connection denied by Geolocation` page.
- Cause:
  - Geographic access restriction on the official site.
- Resolution:
  - Treat as a hard official-site blocker in this environment.
  - Do not use Tennessee as an official-source sync target from this machine for now.

### 3.6 South Dakota Legislature uses a SPA, but exposes an official CSV

- Symptom:
  - Legislator pages are a Vue SPA and direct roster URLs render `Loading...`.
- Cause:
  - Member listing is front-end-driven.
- Resolution:
  - Official JS bundle exposed a downloadable session-member CSV route.
  - Working official source:
    - `https://sdlegislature.gov/api/SessionMembers/71.csv`
  - Use the CSV as the official roster source instead of scraping the SPA page.

### 3.7 Kentucky Legislature blocked

- Symptom:
  - `https://legislature.ky.gov/Legislators/Pages/default.aspx` returned a blocked service page.
- Cause:
  - Official site returned a `403`-style service block page with no usable legislator content.
- Resolution:
  - Do not use the page as a roster source in this environment unless another official endpoint is found.

### 3.8 Montana Legislature 403 page is not usable data

- Symptom:
  - `https://leg.mt.gov/legislator-information/roster/` returned `403`.
- Cause:
  - Unlike Arkansas, the returned HTML is only a branded denial page and does not contain roster content.
- Resolution:
  - Do not apply the lenient-HTML workaround here.
  - Wait for another official endpoint.

### 3.9 Congress.gov bill pages may block direct scraper requests

- Symptom:
  - Direct `httpx` requests to bill pages such as
    - `https://www.congress.gov/bill/119th-congress/senate-bill/1216`
    returned `403 Forbidden`.
- Cause:
  - In this environment, `Congress.gov` may allow browser access but reject plain programmatic requests.
- Resolution:
  - Keep deriving canonical bill URLs from the bill number structure:
    - `https://www.congress.gov/bill/{congress}th-congress/{bill-type}/{number}`
  - Store and display official bill URLs even if detail scraping is temporarily blocked.
  - Prefer a future fallback path using the official `Congress.gov API` when available.
  - Keep detail enrichment code in place so it can run in environments where the site does not block requests.

### 3.10 Congress.gov API keys may be pasted with an `API:` prefix

- Symptom:
  - A user-provided Congress API key returned `403 Forbidden` for API calls.
- Cause:
  - The pasted key included an `API:` prefix, but the actual query parameter value should only be the key body.
- Resolution:
  - Strip the `API:` prefix before storing or using `CONGRESS_API_KEY`.
  - The config loader now normalizes:
    - `API:xxxxxxxx` -> `xxxxxxxx`

## 4. 匯入與同步策略

### 4.1 Official state sources added incrementally

- Current working pattern:
  - Add one or a few states at a time.
  - Validate parser locally.
  - Run single-state or filtered sync.
  - Confirm counts in DB.
- Reason:
  - Reduces lock contention.
  - Makes it easier to isolate broken state sites.
  - Keeps official sources primary and Wikipedia supplemental.

### 4.2 Historical Congress bills import strategy

- Current rule:
  - If `TW > 0` in the Excel source, the bill is included.
  - Mixed-topic bills are still included if they mention Taiwan, even if they also touch `HK`, `TB`, `UG`, `MC`, `DL`, or `FL`.
- Current state:
  - Historical Taiwan-related legislation pages are first created from Excel seed data.
  - `Congress.gov` official links are then added where available.
- Reason:
  - This gets historical pages live quickly without waiting for every official field to be enriched first.

### 4.3 Congress legislation enrichment progress

- Imported from Excel:
  - historical `TW > 0` bills
- Enrichment approach:
  - keep Excel seed as the page backbone
  - add `Congress.gov` official URLs and sponsor linkage incrementally
- Status:
  - Works well for many bills, but not every historical bill has a clean modern `Congress.gov` mapping.

## 5. Recommended next logging practice

- When a new source is added, record:
  - state or agency
  - source URL
  - parser identity
  - whether it returned HTML, JSON, GraphQL, CSV, or JS app-shell
  - any blockers such as `403`, timeout, geoblocking, or Cloudflare
  - whether sync was verified in DB

- When a new UI or data bug is fixed, record:
  - user-visible symptom
  - root cause
  - file(s) changed
  - whether old data also needed cleanup
