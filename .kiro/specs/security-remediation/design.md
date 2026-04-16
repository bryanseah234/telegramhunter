# Security Remediation - Design Document

**Spec Type:** Bugfix
**Status:** In Progress
**Depends On:** bugfix.md

---

## 1. Architecture Overview

The system is composed of:
- **FastAPI** — REST API layer (`app/api/`)
- **Celery + Redis** — Async task queue for scanners, broadcast, and audit tasks
- **Supabase (PostgreSQL)** — Persistent storage for credentials, messages, accounts
- **Telegram Bot API + Telethon** — Bot management, scraping, broadcasting
- **Fernet (cryptography lib)** — Symmetric encryption for bot tokens at rest

All fixes must preserve these integration points without schema or API contract changes.

---

## 2. Dependency Compatibility (BUG-001 to BUG-004)

**Status: ALREADY FIXED** in `requirements.txt`.

| Package | Old | New | Notes |
|---|---|---|---|
| `cryptography` | 41.0.7 | 44.0.0 | Fernet API unchanged — `SecurityService` unaffected |
| `httpx` | 0.24.1 | 0.28.1 | `AsyncClient` API compatible, `follow_redirects` param unchanged |
| `requests` | 2.31.0 | 2.32.3 | Drop-in replacement, no API changes |
| `Telethon` | 1.33.0 | 1.38.1 | Session format compatible, error class names unchanged |

No code changes required for these. Verification: run existing scanner and scraper tests after `pip install -r requirements.txt`.

---

## 3. Fix Strategy by Category

### 3.1 Security Fixes

**BUG-005: Hardcoded ANONYMOUS_ADMIN_ID**
- File: `app/services/bot_listener.py` line ~50
- Change: Read from `settings.ANONYMOUS_ADMIN_ID` with default `1087968824`
- Config: Add `ANONYMOUS_ADMIN_ID: int = 1087968824` to `app/core/config.py`
- No breaking change — default preserves existing behavior

**BUG-006: .env in repository**
- `.gitignore` already contains `.env` — file is excluded from future commits
- Action: Verify `.env` is not tracked (`git ls-files .env`). If tracked, run `git rm --cached .env`
- No code changes required

**BUG-007: Unencrypted session files**
- Files: `app/services/bot_listener.py` `finalize_login()` function
- Strategy: After saving `.session` file, set file permissions to `0o600` (owner read/write only)
- Encryption of SQLite session files is complex and risks breaking Telethon's session reader — permissions-only fix is the safe approach
- Add `os.chmod(final_path, 0o600)` after successful save

**BUG-008: SQL injection risk in audit_tasks.py**
- File: `app/workers/tasks/audit_tasks.py`
- Review: All DB calls use Supabase Python client with `.eq("id", cred_id)` — these are parameterized by the client library, not raw SQL
- The `topic_id` referenced in bugfix.md is passed to `.eq("topic_id", ...)` which is also parameterized
- Action: Confirm no raw `f-string` SQL exists. Add a code comment documenting that Supabase client handles parameterization.
- Risk: LOW — Supabase client does not use raw SQL interpolation

### 3.2 Logic Flaws

**BUG-009: Race condition in broadcast claiming**
- File: `app/workers/tasks/flow_tasks.py` `_broadcast_logic()`
- Current: Two-step check-then-update (read `is_broadcasted` → read `broadcast_claimed_at` → update)
- Fix: Collapse into a single atomic UPDATE with conditional WHERE clause
- Supabase supports: `.update({...}).eq("id", msg_id).eq("is_broadcasted", False).is_("broadcast_claimed_at", "null")`
- For stale claim reclaim: `.update({...}).eq("id", msg_id).eq("is_broadcasted", False).lt("broadcast_claimed_at", stale_threshold_iso)`
- This eliminates the TOCTOU window between check and claim

**BUG-010: Infinite loop in bot_listener**
- File: `app/services/bot_listener.py` main loop
- Current: `while not stop_event.is_set()` with 1s sleep — signal handler exception could prevent `stop_event.set()`
- Fix: Wrap signal handler in try/except, add `stop_event.set()` in finally block
- Add a hard timeout: if loop runs > N iterations without progress, force exit

**BUG-011: Unhandled Telethon exceptions in scraper**
- File: `app/services/scraper_srv.py`
- Current: Generic `except Exception as e` catches everything including `FloodWaitError`, `AuthKeyUnregisteredError`, `UserDeactivatedBanError`
- Fix: Add specific handlers before the generic catch:
  - `FloodWaitError` → sleep `e.seconds`, return empty list
  - `AuthKeyUnregisteredError` / `UserDeactivatedBanError` → mark session as revoked in DB, return empty list
  - `SessionPasswordNeededError` → log and return empty list
- Note: `FloodWaitError` is already partially handled in `scrape_history()` outer try — extend this pattern to inner methods

**BUG-012: Memory leak in scanner errors list**
- File: `app/workers/tasks/scanner_tasks.py`
- Current: `errors = []` accumulates unbounded during scan loops
- Fix: Cap with `errors = errors[-100:]` after each append, or use `errors.append(str(e)); errors = errors[-100:]`
- Affects: `_scan_shodan_async`, `_scan_urlscan_async`, `_scan_github_async`, `_scan_fofa_async`

**BUG-013: Token validation bypass (self-heal accepts raw tokens)**
- File: `app/workers/tasks/flow_tasks.py` `_exfiltrate_logic()` and `_enrich_logic()`
- Current: If token doesn't start with `gAAAA`, it's used raw AND self-healed (encrypted + saved back)
- This is intentional legacy support, not a bypass — the token is immediately encrypted and saved
- Risk is the window between use and re-encryption, which is within the same async function (no yield point between decrypt and use)
- Fix: Add a log warning when raw token is detected, ensure the self-heal encrypt+save happens BEFORE the token is used downstream
- Reorder: encrypt → save → then use `bot_token`

### 3.3 Maintainability

**BUG-014: Duplicate service definitions**
- `scanners.py` has: `GitlabService`, `GithubService`, `ShodanService`, `FofaService`, `UrlScanService`
- `scanners_extension.py` has: `GithubGistService`, `GrepAppService`, `PublicWwwService`, `BitbucketService`, `PastebinService`, `GoogleSearchService`
- These are NOT duplicates — they are complementary. `scanners_extension.py` imports from `scanners.py` (`TOKEN_PATTERN`, `_is_valid_token`, `_perform_active_deep_scan`)
- The bugfix.md assessment was incorrect. No consolidation needed.
- Action: Add a module docstring to each file clarifying their scope. Mark BUG-014 as **Won't Fix / Misdiagnosed**.

**BUG-015: Magic numbers**
- Create `app/core/constants.py` with named constants
- Key values to extract: `LOCK_TTL_SECONDS=120`, `CLAIM_TIMEOUT_MINUTES=5`, `WORKER_HEARTBEAT_TIMEOUT=45*60`, `BROADCAST_RATE_LIMIT_SLEEP=2.0`, `MAX_ERRORS_BUFFER=100`
- Import in relevant files

**BUG-016: Missing type hints in scraper_srv.py**
- `scrape_history()` already has `-> List[Dict]` return type
- Add `TypedDict` definitions for the message dict structure
- Scope: `scraper_srv.py` only — other files are out of scope for this remediation

---

## 4. Data Model Verification

No schema changes required for any fix. All fixes are code-only:
- No new DB columns
- No new tables
- No changes to existing column types or constraints
- Supabase RLS policies unaffected

---

## 5. Rollback Strategy

Each fix is atomic and independently revertable:
1. Dependency updates: revert `requirements.txt` to previous pinned versions
2. Config additions: remove new fields from `config.py` (have defaults, so non-breaking)
3. Code fixes: each change is a small, isolated edit — git revert per file is sufficient
4. No migrations to roll back

---

## 6. Testing Strategy

| Bug | Test Type | Verification |
|---|---|---|
| BUG-001 to 004 | Smoke test | `from cryptography.fernet import Fernet` + encrypt/decrypt round-trip |
| BUG-005 | Unit | Test `is_admin()` with env var set and unset |
| BUG-006 | Git check | `git ls-files .env` returns empty |
| BUG-007 | File system | Check `os.stat(session_path).st_mode` after save |
| BUG-008 | Code review | Grep for raw f-string SQL — none expected |
| BUG-009 | Concurrency | Run two broadcast workers simultaneously, verify no duplicate sends |
| BUG-010 | Signal test | Send SIGTERM to bot_listener, verify clean exit |
| BUG-011 | Exception mock | Mock Telethon to raise `FloodWaitError`, verify sleep+return |
| BUG-012 | Memory | Run scanner with 200 simulated failures, verify `len(errors) <= 100` |
| BUG-013 | Order check | Verify encrypt+save precedes token use in flow |
| BUG-014 | N/A | Won't Fix |
| BUG-015 | Import test | `from app.core.constants import *` succeeds |
| BUG-016 | mypy | `mypy app/services/scraper_srv.py` passes |
