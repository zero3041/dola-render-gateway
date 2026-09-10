# Video Generation Gateway - Architecture & Technical Notes

Technical reference for the service architecture, profile coordination, and task pipeline.

---

## 1. Core Architecture

The system operates across three tiers:
1. **API Tier (`server.py`)**: FastAPI application exposing standard OpenAI video endpoints (`/v1/videos/generations`, `/v1/videos/<id>`) and web dashboard routes (`/web`, `/api/admin/*`).
2. **Pool Management Tier (`browser_pool.py`)**: Manages persistent browser profiles in `accounts/`, controlling task concurrency, account locking, and daily quota rotations.
3. **Execution Tier (`video_worker_ui.py`, `video_worker.py`)**:
   - **UI Automation Mode**: Automates browser interactions, authenticates via persistent browser session, injects prompts, and handles verification challenges.
   - **Protocol Mode**: Sends structured SSE requests to completion endpoints and polls status endpoints for video rendering progress.

---

## 2. Duration Configuration

- The browser module manages duration parameters (`15s`, `30s`) via request interception.
- Synchronizes with client action bar configuration to present extended duration selections.

---

## 3. High-Definition Stream Processing

- Video rendering status is monitored through the event stream.
- High-definition stream URLs are parsed and downloaded directly to the local storage directory `downloads/`.

---

## 4. Verification Handling

- Handles verification challenges using automated visual template alignment to ensure reliable background execution.

---

## 5. Cookie Import & Session Re-injection

- Import accepts three browser-export formats through `cookie_import.parse_cookie_input`: JSON array (Cookie-Editor), Netscape file, and `k=v; k2=v2` header string. Only `sessionid` is required; `msToken` / `s_v_web_id` are warnings.
- The original paste is stored verbatim in `pool_usage.db` (`accounts_meta.raw_cookie`) by both entry points: the dashboard (`POST /api/admin/cookies` → `_run_cookie_job`) and the CLI (`setup_cookie.py <account> [file]` or `DOLA_COOKIE_STRING`).
- Re-importing for an existing account refreshes the session in place (profile is reused, `login_ok` resets to Unverified); it is not rejected.
- `browser.launch_account_context` calls `cookie_import.ensure_session_cookies` on every launch, so generate / resume / verify re-inject the stored original when the profile has no `sessionid`. A profile that already holds a live session is never overwritten.
