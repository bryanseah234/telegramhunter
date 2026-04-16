# Security Remediation - Task List

**Spec Type:** Bugfix
**Status:** In Progress
**Execution Order:** Sequential. Each task must pass validation before proceeding.

---

## PHASE 3 EXECUTION CHECKLIST

Legend: `[ ]` = Open | `[x]` = Complete | `[-]` = Won't Fix / N/A

---

### TIER 1 — Dependency CVEs (Pre-verified Fixed)

- [x] **TASK-001** — Verify dependency updates in `requirements.txt`
  - BUG: BUG-001, BUG-002, BUG-003, BUG-004
  - Action: Confirm `cryptography==44.0.0`, `httpx==0.28.1`, `requests==2.32.3`, `Telethon==1.38.1` are present
  - Acceptance: `requirements.txt` contains all four pinned versions with security comments
  - Status: **COMPLETE** (already applied in previous session)

---

### TIER 2 — Critical Security Fixes

- [x] **TASK-002** — Externalize hardcoded ANONYMOUS_ADMIN_ID (BUG-005)
  - File: `app/core/config.py` + `app/services/bot_listener.py`
  - Action: Add `ANONYMOUS_ADMIN_ID: int = 1087968824` to Settings class in config.py. Replace hardcoded value in bot_listener.py with `settings.ANONYMOUS_ADMIN_ID`
  - Acceptance: `ANONYMOUS_ADMIN_ID = 1087968824` line removed from bot_listener.py; value reads from settings

- [x] **TASK-003** — Verify .env git exclusion (BUG-006)
  - Action: Run `git ls-files .env` — must return empty. Confirm `.env` is in `.gitignore`
  - Acceptance: `.env` not tracked by git; `.gitignore` contains `.env` entry
  - Note: `.gitignore` already has `.env` — this is a verification-only task

- [x] **TASK-004** — Set session file permissions to 0600 (BUG-007)
  - File: `app/services/bot_listener.py` `finalize_login()` function
  - Action: After `os.replace(tmp_final_path, final_path)` succeeds, add `os.chmod(final_path, 0o600)`
  - Also add chmod after the sqlite3 injection fallback path succeeds
  - Acceptance: Saved `.session` files have mode `-rw-------`

- [x] **TASK-005** — Confirm Supabase parameterization in audit_tasks (BUG-008)
  - File: `app/workers/tasks/audit_tasks.py`
  - Action: Audit all DB calls — confirm no raw f-string SQL. Add inline comment: `# Supabase client parameterizes all .eq() / .update() calls — no raw SQL`
  - Acceptance: No `f"...{topic_id}..."` style SQL strings found; comment added

---

### TIER 3 — Logic Flaw Fixes

- [x] **TASK-006** — Atomic broadcast claim (BUG-009)
  - File: `app/workers/tasks/flow_tasks.py` `_broadcast_logic()`
  - Action: Replace the two-step (read fresh state → check → update) with a single atomic UPDATE that includes the condition in the WHERE clause. Use `.eq("is_broadcasted", False)` combined with `.is_("broadcast_claimed_at", "null")` for unclaimed, and a separate `.lt("broadcast_claimed_at", stale_iso)` for stale reclaim.
  - Acceptance: The `fresh = await async_execute(...)` read + separate update is replaced by a single conditional update; logic still correctly skips already-claimed messages

- [x] **TASK-007** — Harden signal handler in bot_listener (BUG-010)
  - File: `app/services/bot_listener.py` — signal handler and main loop
  - Action: Wrap signal handler body in `try/finally` with `stop_event.set()` in the `finally` block. Ensure `stop_event.set()` is called even if an exception occurs during handler execution.
  - Acceptance: SIGTERM/SIGINT causes clean exit; `stop_event` is always set on signal receipt

- [x] **TASK-008** — Specific Telethon exception handling in scraper (BUG-011)
  - File: `app/services/scraper_srv.py` — `_scrape_via_telethon()` and `_scrape_via_id_bruteforce()`
  - Action: Before the generic `except Exception` block, add specific handlers:
    - `except errors.FloodWaitError as e: await asyncio.sleep(e.seconds); return []`
    - `except errors.AuthKeyUnregisteredError: logger.error("Session revoked"); return []`
    - `except errors.UserDeactivatedBanError: logger.error("Account banned"); return []`
  - Acceptance: `FloodWaitError` triggers sleep+return; auth errors log and return without masking

- [x] **TASK-009** — Cap errors buffer in scanner tasks (BUG-012)
  - File: `app/workers/tasks/scanner_tasks.py`
  - Action: In `_scan_shodan_async`, `_scan_urlscan_async`, `_scan_github_async`, `_scan_fofa_async` — after each `errors.append(str(e))`, add `errors = errors[-100:]`
  - Acceptance: `errors` list never exceeds 100 entries during a scan run

- [x] **TASK-010** — Reorder self-heal: encrypt before use (BUG-013)
  - File: `app/workers/tasks/flow_tasks.py` — `_exfiltrate_logic()` and `_enrich_logic()`
  - Action: In the raw token branch (`if not encrypted_token.startswith("gAAAA")`), ensure the DB update (self-heal encrypt+save) is awaited BEFORE `bot_token` is passed to any downstream call. Currently the `try/except: pass` around the self-heal could silently fail — change to log the failure explicitly.
  - Acceptance: Self-heal save is awaited before scraper/enrichment call; failure is logged not silently swallowed

---

### TIER 4 — Maintainability

- [-] **TASK-011** — Evaluate scanner file duplication (BUG-014)
  - Finding: `scanners.py` and `scanners_extension.py` are NOT duplicates. They are complementary modules. `scanners_extension.py` imports helpers from `scanners.py`.
  - Action: Add module docstrings to both files clarifying their scope
  - Status: **WON'T FIX** (misdiagnosed in bugfix.md — update bugfix.md status)

- [x] **TASK-012** — Create constants module (BUG-015)
  - File: Create `app/core/constants.py`
  - Action: Extract magic numbers:
    - `LOCK_TTL_SECONDS = 120` (from bot_listener.py)
    - `CLAIM_TIMEOUT_MINUTES = 5` (from flow_tasks.py)
    - `WORKER_HEARTBEAT_TIMEOUT_SECONDS = 45 * 60` (from bot_listener.py watchdog)
    - `BROADCAST_RATE_LIMIT_SLEEP = 2.0` (from flow_tasks.py)
    - `MAX_ERRORS_BUFFER = 100` (new constant for BUG-012 fix)
    - `SESSION_FILE_PERMISSIONS = 0o600` (new constant for BUG-007 fix)
  - Import in relevant files and replace literals
  - Acceptance: `app/core/constants.py` exists; all listed literals replaced with named constants

- [x] **TASK-013** — Add TypedDict for scraper message structure (BUG-016)
  - File: `app/services/scraper_srv.py`
  - Action: Define a `ScrapedMessage` TypedDict at the top of the file:
    ```python
    from typing import TypedDict
    class ScrapedMessage(TypedDict):
        telegram_msg_id: int
        sender_name: str
        content: str
        media_type: str
        file_meta: dict
        chat_id: int
    ```
  - Update return type annotations: `-> List[ScrapedMessage]`
  - Acceptance: TypedDict defined; `scrape_history` return type updated; no mypy errors on the type definition itself

---

### TIER 5 — Artifact Updates

- [x] **TASK-014** — Update bugfix.md with final statuses
  - Action: Mark BUG-001 through BUG-004 as `Status: Fixed`. Mark BUG-014 as `Status: Won't Fix (Misdiagnosed)`. Mark all other completed bugs as `Status: Fixed`.
  - Acceptance: bugfix.md reflects actual implementation state

---

## Execution Notes

- Run `getDiagnostics` after each file edit before marking task complete
- Tasks within the same tier can be executed in any order
- Tier 2 must complete before Tier 3 (security before logic)
- TASK-012 (constants) should be done before or alongside TASK-009 (errors buffer) so `MAX_ERRORS_BUFFER` is available
- Do not modify database schema or API response shapes
