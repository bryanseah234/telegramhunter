# Task List — Remediation Cycle 1 (Full Scope)

**Generated:** 2026-04-21 (Revised)  
**Source:** bugfix.md + design.md  
**Execution Order:** Sequential. Each task is atomic.  
**Deferred Items:** None. All 27 items are in scope.

---

## BATCH 1: Startup Crash Fixes (Highest Priority)

### TASK-001 — Fix GithubGistService Import
**Bug Ref:** BUG-001  
**File:** `app/workers/tasks/scanner_tasks.py`  
**Status:** Pending  
**Change:** Move `GithubGistService` from `scanners` import block to `scanners_extension` import block  
**Acceptance Criteria:**
- `from app.services.scanners import (...)` no longer includes `GithubGistService`
- `from app.services.scanners_extension import ...` includes `GithubGistService`
- No other import changes
- No syntax errors

---

### TASK-002 — Instantiate PublicWwwService
**Bug Ref:** BUG-002  
**File:** `app/workers/tasks/scanner_tasks.py`  
**Status:** Pending  
**Change:** Add `PublicWwwService` to `scanners_extension` import and instantiate `publicwww_srv`  
**Acceptance Criteria:**
- `PublicWwwService` imported from `scanners_extension`
- `publicwww_srv = PublicWwwService()` present at module level
- `scan_publicwww` task references `publicwww_srv` without `NameError`

---

### TASK-003 — Fix AuditLogger.log_event() Calls
**Bug Ref:** BUG-003  
**File:** `app/workers/tasks/flow_tasks.py`  
**Status:** Pending  
**Change:** Replace `audit_log.log_event()` calls with `AuditLogger.log()` static method  
**Acceptance Criteria:**
- No calls to `log_event()` remain in `flow_tasks.py`
- `AuditLogger.log()` called with correct parameters
- `audit_log = AuditLogger()` instantiation lines removed
- No `AttributeError` on exfiltration task execution

---

## BATCH 2: Security Fixes

### TASK-004 — Fix Monitor API Auth Bypass
**Bug Ref:** BUG-005, BUG-015  
**File:** `app/api/routers/monitor.py`  
**Status:** Pending  
**Change:** Fix `/monitor/stats` header parameter; remove dead `_verify_monitor_auth` helper  
**Acceptance Criteria:**
- `/monitor/stats` uses `x_monitor_key: str | None = Header(None)` parameter
- Auth check correctly compares `x_monitor_key` against `settings.MONITOR_API_KEY`
- `_verify_monitor_auth()` function removed
- All three monitor routes use consistent auth pattern
- No regressions in `/monitor/credentials` or `/monitor/messages`

---

### TASK-005 — Route Extension Writes Through API (Raw Token Fix)
**Bug Ref:** BUG-006  
**Files:** `extension/background.js`, `extension/ui/popup.html`, `extension/ui/popup.js`  
**Status:** Pending  
**Change:** Add API URL config field to extension. Route uploads through `/ingest/extension/credentials` for server-side encryption. Keep direct Supabase write as fallback.  
**Acceptance Criteria:**
- Extension settings popup has `API URL` field
- `uploadToSupabase()` checks for `apiUrl` config; if present, POSTs to `/ingest/extension/credentials`
- Payload matches `ExtensionIngestRequest` schema (source, domain, query, results array)
- Direct Supabase write path preserved as fallback when `apiUrl` not configured
- No breaking changes to existing extension functionality

---

## BATCH 3: Data Integrity Fixes

### TASK-006 — Fix Multi-Chat Meta Overwrite
**Bug Ref:** BUG-004  
**File:** `app/workers/tasks/flow_tasks.py`  
**Status:** Pending  
**Change:** Preserve `topic_id`, `bot_username`, `bot_id` in second meta update for multi-chat credentials  
**Acceptance Criteria:**
- Second `update()` call in `_enrich_logic` includes `topic_id`, `bot_username`, `bot_id`
- `topic_id` variable confirmed in scope at fix location
- No other changes to enrichment logic

---

### TASK-007 — Fix Async DB Calls in Audit Tasks
**Bug Ref:** BUG-007  
**File:** `app/workers/tasks/audit_tasks.py`  
**Status:** Pending  
**Change:** Import `async_execute` from `flow_tasks`; wrap all synchronous `db.table().execute()` calls  
**Acceptance Criteria:**
- `async_execute` imported from `app.workers.tasks.flow_tasks`
- All `db.table(...).execute()` calls in async functions replaced with `await async_execute(...)`
- No circular import introduced
- `_audit_active_topics_async()` and `_system_self_heal_async()` both fixed

---

## BATCH 4: Architectural Fixes

### TASK-008 — Implement Persistent Event Loop for Celery Workers
**Bug Ref:** BUG-008  
**Files:** `app/workers/celery_app.py`, `app/workers/tasks/flow_tasks.py`, `app/workers/tasks/scanner_tasks.py`, `app/workers/tasks/audit_tasks.py`  
**Status:** Pending  
**Change:** Add `get_worker_loop()` function and loop lifecycle management to `celery_app.py`. Replace `asyncio.run()` with `loop.run_until_complete()` in all `_run_sync` helpers.  
**Acceptance Criteria:**
- `get_worker_loop()` exported from `celery_app.py`
- Loop initialized in `worker_ready` signal handler
- Loop closed in `worker_shutdown` signal handler
- `_run_sync()` in `flow_tasks.py`, `scanner_tasks.py`, `audit_tasks.py` uses `get_worker_loop().run_until_complete()`
- `asyncio.run()` no longer called in any task file
- `_send_signal_log()` in `celery_app.py` updated to use persistent loop

---

### TASK-009 — Implement BroadcasterService Singleton
**Bug Ref:** BUG-011  
**Files:** `app/workers/tasks/flow_tasks.py`, `app/workers/tasks/scanner_tasks.py`, `app/workers/tasks/audit_tasks.py`  
**Status:** Pending  
**Change:** Add `get_broadcaster()` lazy singleton factory to `flow_tasks.py`. Replace all `BroadcasterService()` instantiations across task files.  
**Acceptance Criteria:**
- `_broadcaster` module-level variable and `get_broadcaster()` function added to `flow_tasks.py`
- All `BroadcasterService()` instantiations in `flow_tasks.py` replaced with `get_broadcaster()`
- `scanner_tasks.py` and `audit_tasks.py` import and use `get_broadcaster` from `flow_tasks`
- Bot rotation state (`_token_cycle`) persists across task invocations within same worker process

---

## BATCH 5: Logic Fixes

### TASK-010 — Increase Broadcast Batch Size
**Bug Ref:** BUG-009  
**File:** `app/workers/tasks/flow_tasks.py`  
**Status:** Pending  
**Change:** Increase broadcast query limit from 20 to 100  
**Acceptance Criteria:**
- `.limit(20)` in `_broadcast_logic` changed to `.limit(100)`
- Comment updated to reflect new limit
- No other changes to broadcast logic

---

### TASK-011 — Add Enrichment Re-queue Cooldown
**Bug Ref:** BUG-010  
**File:** `app/workers/tasks/scanner_tasks.py`  
**Status:** Pending  
**Change:** Gate `enrich_credential.delay()` for existing tokens without chat_id using Redis cooldown  
**Acceptance Criteria:**
- `redis_srv` imported in `scanner_tasks.py`
- Cooldown key `enrich_requeue:{existing_id}` checked before queuing
- 1-hour TTL set after queuing
- First-time enrichment for new tokens unaffected

---

### TASK-012 — Fix /scan/trigger Valid Sources
**Bug Ref:** BUG-013  
**File:** `app/api/routers/scan.py`  
**Status:** Pending  
**Change:** Remove `censys` and `hybrid`; add `gitlab` and `urlscan` to valid sources  
**Acceptance Criteria:**
- Valid sources: `["shodan", "fofa", "github", "gitlab", "urlscan"]`
- Error message updated to reflect new list
- No other changes to scan router

---

### TASK-013 — Remove LOCK_TTL_SECONDS Redefinition
**Bug Ref:** BUG-014  
**File:** `app/services/bot_listener.py`  
**Status:** Pending  
**Change:** Remove local `LOCK_TTL_SECONDS = 120` redefinition  
**Acceptance Criteria:**
- Line `LOCK_TTL_SECONDS = 120` removed from module body
- Import from `app.core.constants` remains and is the sole definition used
- No references to `LOCK_TTL_SECONDS` broken

---

### TASK-014 — Standardize Token Secret Length Validation
**Bug Ref:** BUG-012  
**File:** `app/utils/helpers.py`  
**Status:** Pending  
**Change:** Change secret length check from `< 33 or > 35` to `!= 35`  
**Acceptance Criteria:**
- `is_valid_telegram_token()` in `helpers.py` requires exactly 35-char secret
- Matches `_is_valid_token()` behavior in `scanners.py`
- No changes to `scanners.py` or `content.js`

---

### TASK-015 — Fix Circuit Breaker Thresholds
**Bug Ref:** BUG-016  
**File:** `app/core/circuit_breaker.py`  
**Status:** Pending  
**Change:** Update all circuit breakers to `failure_threshold=5, recovery_timeout=60` to match PRD  
**Acceptance Criteria:**
- All four named breakers (shodan, urlscan, github, fofa) use `failure_threshold=5, recovery_timeout=60`
- `get_circuit_breaker()` default values updated to match
- `CircuitBreaker` class default parameters updated to match

---

## BATCH 6: New Features

### TASK-016 — Implement CSV Import Pipeline
**Bug Ref:** MISSING-001  
**Files:** `app/workers/tasks/import_tasks.py` (new), `app/workers/celery_app.py`  
**Status:** Pending  
**Change:** Create `import_tasks.py` with `system.import_csv` task. Register in beat schedule.  
**Acceptance Criteria:**
- New file `app/workers/tasks/import_tasks.py` created
- Task scans `imports/` for `.csv` files
- Renames file to `.pending` before processing (atomic claim)
- Parses `token` and `chat_id` columns
- Calls `_save_credentials_async` for validation and DB insert
- Moves processed file to `imports/processed/`
- Task registered in `celery_app.py` beat schedule as `system.import_csv` every 5 minutes
- `imports` module added to `celery_app.py` `imports` list
- Handles empty `imports/` directory gracefully (returns early)

---

### TASK-017 — Implement Audit DB Persistence
**Bug Ref:** MISSING-002  
**Files:** `database/init.sql`, `app/core/audit.py`  
**Status:** Pending  
**Change:** Add `audit_logs` table to schema. Implement `_persist_to_db()` in `AuditLogger`.  
**Acceptance Criteria:**
- `audit_logs` table created in `database/init.sql` with `IF NOT EXISTS` guard
- Table has columns: `id`, `timestamp`, `event_type`, `credential_id`, `user_agent`, `success`, `details`
- Indexes on `event_type` and `timestamp`
- `AuditLogger._persist_to_db()` inserts to `audit_logs` table via `db` client
- High-importance events (`TOKEN_DECRYPTED`, `TOKEN_REVOKED`, `CREDENTIAL_CREATED`) are persisted
- Failure to persist logs error but does not raise (existing behavior preserved)

---

## BATCH 7: Documentation Fixes

### TASK-018 — Update .env.template
**Bug Ref:** CONFIG-001, CONFIG-002, CONFIG-003, CONFIG-004  
**File:** `.env.template`  
**Status:** Pending  
**Change:** Add all missing environment variables  
**Acceptance Criteria:**
- `EXTENSION_WRITE_SECRET` added with description comment
- `BITBUCKET_API_TOKEN` present (replaces `BITBUCKET_APP_PASSWORD`)
- `NETLAS_API_KEY_1` and `NETLAS_API_KEY_2` added under scanner keys section
- `TARGET_COUNTRIES` noted as optional with reference to defaults
- Existing variables unchanged

---

### TASK-019 — Update README.md
**Bug Ref:** CONFIG-001, CONFIG-002, CONFIG-003, CONFIG-004, BUG-013  
**File:** `README.md`  
**Status:** Pending  
**Change:** Update scanner key table, optional variables table, and `/scan/trigger` usage section  
**Acceptance Criteria:**
- Scanner key table includes `NETLAS_API_KEY_1` and `NETLAS_API_KEY_2`
- Optional variables table includes `EXTENSION_WRITE_SECRET`
- `BITBUCKET_APP_PASSWORD` corrected to `BITBUCKET_API_TOKEN`
- `/scan/trigger` valid sources updated to: `shodan`, `fofa`, `github`, `gitlab`, `urlscan`
- No other README content changed

---

## EXECUTION SUMMARY

| Task | Bug Ref | File(s) | Priority | Status |
|---|---|---|---|---|
| TASK-001 | BUG-001 | scanner_tasks.py | CRITICAL | ✅ Complete |
| TASK-002 | BUG-002 | scanner_tasks.py | CRITICAL | ✅ Complete |
| TASK-003 | BUG-003 | flow_tasks.py | CRITICAL | ✅ Complete |
| TASK-004 | BUG-005, BUG-015 | monitor.py | HIGH | ✅ Complete |
| TASK-005 | BUG-006 | background.js, popup.html, popup.js | HIGH | ✅ Complete |
| TASK-006 | BUG-004 | flow_tasks.py | HIGH | ✅ Complete |
| TASK-007 | BUG-007 | audit_tasks.py | HIGH | ✅ Complete |
| TASK-008 | BUG-008 | celery_app.py + 3 task files | HIGH | ✅ Complete |
| TASK-009 | BUG-011 | flow_tasks.py + 2 task files | HIGH | ✅ Complete |
| TASK-010 | BUG-009 | flow_tasks.py | MEDIUM | ✅ Complete |
| TASK-011 | BUG-010 | scanner_tasks.py | MEDIUM | ✅ Complete |
| TASK-012 | BUG-013 | scan.py | MEDIUM | ✅ Complete |
| TASK-013 | BUG-014 | bot_listener.py | LOW | ✅ Complete |
| TASK-014 | BUG-012 | helpers.py | LOW | ✅ Complete |
| TASK-015 | BUG-016 | circuit_breaker.py | LOW | ✅ Complete |
| TASK-016 | MISSING-001 | import_tasks.py (new), celery_app.py | MEDIUM | ✅ Complete |
| TASK-017 | MISSING-002 | init.sql, audit.py | MEDIUM | ✅ Complete |
| TASK-018 | CONFIG-001..004 | .env.template | LOW | ✅ Complete |
| TASK-019 | CONFIG-001..004, BUG-013 | README.md | LOW | ✅ Complete |

**Total: 19 tasks across 14 files (1 new file)**  
**All 27 audit items covered. Zero deferrals.**

---

## CHECKPOINT FORMAT

After each task completion, emit:
```
✅ TASK-XXX COMPLETE
File(s): <paths>
Change: <one-line summary>
Bugs Fixed: BUG-XXX
Regressions: None detected / <description if any>
```

## SESSION RESUME INSTRUCTIONS

If context is lost, resume by:
1. Reading `tasks.md` — check Status column for last completed task
2. Reading `bugfix.md` — verify which bugs are marked Fixed
3. Continue from first Pending task
