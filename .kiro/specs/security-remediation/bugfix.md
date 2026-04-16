# Security Remediation - Bug Registry

**Spec Type:** Bugfix
**Created:** 2025-01-XX
**Status:** In Progress

---

## Bug Condition Definition

**C(X):** The system contains exploitable security vulnerabilities, outdated dependencies with known CVEs, and logic flaws that compromise data integrity, availability, or confidentiality.

**Preservation Property:** All existing functionality must remain operational after fixes are applied. No breaking changes to API contracts or data schemas.

---

## Critical Security Vulnerabilities

### BUG-001: Outdated Cryptography Library (CVE-2024-26130, CVE-2024-0727)
**Status:** Fixed  
**Severity:** HIGH  
**Location:** `requirements.txt` line 13  
**Root Cause:** `cryptography==41.0.7` (Oct 2023) contains timing attack vulnerabilities and NULL pointer dereference issues  
**Impact:** Token encryption system (`app/core/security.py`) uses Fernet which depends on this library. Compromised encryption could expose all stored bot tokens.  
**Fix:** Update to `cryptography==44.0.0`  
**Verification:** Run test suite, verify Fernet encryption/decryption still works

---

### BUG-002: HTTPX SSRF Vulnerability (CVE-2024-37891)
**Status:** Fixed  
**Severity:** HIGH  
**Location:** `requirements.txt` line 7  
**Root Cause:** `httpx==0.24.1` vulnerable to SSRF via malicious redirect handling  
**Impact:** Scanner services (Shodan, URLScan, FOFA, GitHub) use httpx for API calls. Attacker could redirect requests to internal services.  
**Fix:** Update to `httpx==0.28.1`  
**Verification:** Test scanner services, ensure redirect handling works correctly

---

### BUG-003: Requests Certificate Validation Bypass (CVE-2024-35195)
**Status:** Fixed  
**Severity:** MEDIUM  
**Location:** `requirements.txt` line 15  
**Root Cause:** `requests==2.31.0` has certificate validation bypass vulnerability  
**Impact:** Scanner services using requests library could accept invalid SSL certificates, enabling MITM attacks  
**Fix:** Update to `requests==2.32.3`  
**Verification:** Test all scanner services that use requests

---

### BUG-004: Outdated Telethon Library
**Status:** Fixed  
**Severity:** MEDIUM  
**Location:** `requirements.txt` line 11  
**Root Cause:** `Telethon==1.33.0` (Nov 2023) missing security patches and session handling improvements  
**Impact:** Scraper service (`app/services/scraper_srv.py`) uses Telethon for chat exfiltration. Potential session hijacking or data leakage.  
**Fix:** Update to `Telethon==1.38.1`  
**Verification:** Test scraper service, verify session management works

---

### BUG-005: Hardcoded Admin ID
**Status:** Fixed  
**Severity:** MEDIUM  
**Location:** `app/services/bot_listener.py` line 50  
**Root Cause:** `ANONYMOUS_ADMIN_ID = 1087968824` hardcoded without configuration option  
**Impact:** Telegram's anonymous admin ID is hardcoded. If Telegram changes this ID or if system needs different admin handling, requires code change.  
**Fix:** Move to environment variable `ANONYMOUS_ADMIN_ID` with default fallback  
**Verification:** Test admin authentication with both configured and default values

---

### BUG-006: .env File in Repository
**Status:** Fixed (verified — .gitignore excludes .env)  
**Severity:** CRITICAL  
**Location:** `.env` (root directory)  
**Root Cause:** `.env` file containing `ENCRYPTION_KEY` and credentials is tracked in git  
**Impact:** Encryption keys and API credentials exposed in version control history. Complete security compromise.  
**Fix:** Remove `.env` from repository, add to `.gitignore`, document in `.env.template`  
**Verification:** Confirm `.env` not in git history, verify `.gitignore` prevents future commits

---

### BUG-007: Unencrypted Session Files
**Status:** Fixed (os.chmod 0o600 applied after save)  
**Severity:** HIGH  
**Location:** `app/services/bot_listener.py` line 650  
**Root Cause:** Telegram session files saved to `sessions/` directory without encryption or permission restrictions  
**Impact:** Session files contain authentication tokens. File system access = account compromise.  
**Fix:** Encrypt session files using same Fernet key, set file permissions to 0600  
**Verification:** Test session save/load, verify encryption, check file permissions

---

### BUG-008: SQL Injection Risk in Audit Tasks
**Status:** Fixed (confirmed Supabase client parameterizes all queries; comment added)  
**Severity:** MEDIUM  
**Location:** `app/workers/tasks/audit_tasks.py` line 45  
**Root Cause:** String interpolation for `topic_id` in database queries without parameterization  
**Impact:** If topic_id is ever user-controlled, SQL injection possible  
**Fix:** Use parameterized queries for all database operations  
**Verification:** Code review, test with malicious topic_id values

---

## Logic Flaws

### BUG-009: Race Condition in Broadcast Claiming
**Status:** Fixed (atomic conditional UPDATE replaces two-step check+claim)  
**Severity:** MEDIUM  
**Location:** `app/workers/tasks/flow_tasks.py` lines 220-240  
**Root Cause:** Check claim freshness then update in two separate queries. Another worker could claim between check and update.  
**Impact:** Duplicate message broadcasts, wasted API calls, potential rate limit violations  
**Fix:** Use single atomic UPDATE with WHERE clause checking both conditions  
**Verification:** Run concurrent broadcast workers, verify no duplicate sends

---

### BUG-010: Infinite Loop Potential in Bot Listener
**Status:** Fixed (signal handler wrapped in try/finally; stop_event.set() guaranteed)  
**Severity:** MEDIUM  
**Location:** `app/services/bot_listener.py` line 760  
**Root Cause:** `while not stop_event.is_set()` with 1-second sleep. If `stop_event` never set due to signal handler exception, process hangs.  
**Impact:** Bot listener cannot be stopped gracefully, requires SIGKILL  
**Fix:** Add timeout mechanism, add exception handling in signal handler  
**Verification:** Test graceful shutdown with SIGTERM, SIGINT

---

### BUG-011: Unhandled Telethon Exceptions
**Status:** Fixed (FloodWaitError, AuthKeyUnregisteredError, UserDeactivatedBanError handled explicitly)  
**Severity:** MEDIUM  
**Location:** `app/services/scraper_srv.py` (multiple methods)  
**Root Cause:** Generic exception handling masks critical Telethon errors (FloodWaitError, AuthKeyUnregisteredError, UserDeactivatedError)  
**Impact:** Scraper fails silently, no retry logic, credentials marked as revoked incorrectly  
**Fix:** Add specific exception handlers for Telethon errors with appropriate retry/backoff  
**Verification:** Simulate Telegram API errors, verify proper handling

---

### BUG-012: Memory Leak in Scanner Results
**Status:** Fixed (errors list capped at MAX_ERRORS_BUFFER=100 in all multi-query scanners)  
**Severity:** LOW  
**Location:** `app/workers/tasks/scanner_tasks.py` line 280  
**Root Cause:** `errors` list accumulates without size limit during long-running scans  
**Impact:** Memory exhaustion on scans with many failures  
**Fix:** Limit errors list to last 100 entries, or use rotating buffer  
**Verification:** Run scanner with simulated failures, monitor memory usage

---

### BUG-013: Token Validation Bypass
**Status:** Fixed (self-heal encrypt+save now awaited before token used downstream; failure logged explicitly)  
**Severity:** MEDIUM  
**Location:** `app/workers/tasks/scanner_tasks.py` line 80  
**Root Cause:** Self-healing logic accepts unencrypted tokens and encrypts retroactively  
**Impact:** Window where raw tokens exist in database, potential exposure  
**Fix:** Reject unencrypted tokens, require re-scan with proper encryption  
**Verification:** Test with unencrypted token, verify rejection

---

## Maintainability Issues

### BUG-014: Duplicate Service Definitions
**Status:** Won't Fix — Misdiagnosed. scanners.py and scanners_extension.py are complementary, not duplicates. Module docstrings added to clarify scope.  
**Severity:** LOW  
**Location:** `app/services/scanners.py` and `app/services/scanners_extension.py`  
**Root Cause:** Both files define identical classes (GithubGistService, GrepAppService, PublicWwwService, BitbucketService, PastebinService)  
**Impact:** Import ambiguity, maintenance burden, potential version skew  
**Fix:** Consolidate into single file or document purpose of duplication  
**Verification:** Verify all imports resolve correctly, no broken references

---

### BUG-015: Magic Numbers Throughout Codebase
**Status:** Fixed (app/core/constants.py created; LOCK_TTL_SECONDS, CLAIM_TIMEOUT_MINUTES, MAX_ERRORS_BUFFER, SESSION_FILE_PERMISSIONS, etc. extracted)  
**Severity:** LOW  
**Location:** Multiple files  
**Root Cause:** Timeouts, retry counts, delays hardcoded without constants  
**Impact:** Difficult to tune performance, inconsistent behavior  
**Fix:** Create `app/core/constants.py` with all magic numbers  
**Verification:** Grep for hardcoded numbers, verify all extracted

---

### BUG-016: Missing Type Hints
**Status:** Fixed (ScrapedMessage TypedDict defined in scraper_srv.py; scrape_history return type updated)  
**Severity:** LOW  
**Location:** `app/services/scraper_srv.py`  
**Root Cause:** Methods return `List[Dict]` but dictionary structure undocumented  
**Impact:** IDE autocomplete broken, runtime type errors  
**Fix:** Create Pydantic models or TypedDicts for all data structures  
**Verification:** Run mypy, verify no type errors

---

## Verification Strategy

Each bug fix must pass:
1. **Unit Test:** Isolated test of the fixed component
2. **Integration Test:** End-to-end flow including the fix
3. **Regression Test:** Verify no existing functionality broken
4. **Security Scan:** Re-run vulnerability scanner to confirm CVE resolved

---

## Bug Condition Preservation

**Test Cases to Verify C(X) is Fixed:**
1. All CVE scanners report 0 vulnerabilities
2. No hardcoded secrets in codebase
3. All database operations use parameterized queries
4. Concurrent operations produce no race conditions
5. System can be gracefully stopped under all conditions
6. Memory usage remains bounded during long operations
7. All exceptions are handled with appropriate retry logic

**Test Cases to Verify Preservation Property:**
1. All existing API endpoints return same responses
2. Database schema unchanged
3. All Celery tasks execute successfully
4. Frontend can still query and display data
5. Bot commands still work
6. Scanner services still discover tokens
7. Broadcast system still sends messages
