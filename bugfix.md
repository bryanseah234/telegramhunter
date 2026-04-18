# Bug Registry

## Status Legend
- Open
- Fixed

## B001 — Credential persistence field mismatch
- Status: Fixed
- Root Cause: Scanner insertion logic writes `token` while the database schema defines `bot_token`.
- Impact: New discoveries may fail to persist correctly or persist in a shape that breaks downstream encryption/decryption and enrichment flows.
- Evidence:
  - `database/init.sql`: `discovered_credentials.bot_token`
  - `app/workers/tasks/scanner_tasks.py`: `new_data = {"token": security.encrypt(token), ...}`
- Planned Fix:
  - Align scanner persistence payload to schema.
  - Audit all reads/writes against `discovered_credentials` for field consistency.

## B002 — Shodan task returns undefined result variable
- Status: Fixed
- Root Cause: `_scan_shodan_async` appends to `result_msg` inside the error branch but does not initialize it before return.
- Impact: Shodan tasks can fail after partial execution, hiding actual scanner health and suppressing valid results.
- Evidence:
  - `app/workers/tasks/scanner_tasks.py`
- Planned Fix:
  - Initialize and maintain a deterministic result summary string before all return paths.

## B003 — Sensitive credential table is readable by anonymous role
- Status: Fixed
- Root Cause: RLS grants `anon` unrestricted `SELECT` on `discovered_credentials`.
- Impact: Browser clients can query sensitive records, including encrypted bot secrets and internal metadata.
- Evidence:
  - `database/rls_policies.sql`
  - `frontend/components/Sidebar.tsx`
- Planned Fix:
  - Replace direct public reads with a least-privilege projection or view.
  - Restrict anonymous access to non-sensitive fields only.

## B004 — Monitor/admin endpoints are unauthenticated
- Status: Open
- Root Cause: API routers expose monitoring data and administrative operations without auth dependencies.
- Impact: Any reachable client can enumerate system state and invoke operational controls.
- Evidence:
  - `app/api/routers/monitor.py`
  - `app/api/routers/health.py`
  - `app/api/routers/scan.py`
- Planned Fix:
  - Add explicit authentication/authorization checks or disable unsafe routes in production.

## B005 — Audit logging persistence is stubbed
- Status: Open
- Root Cause: `AuditLogger._persist_to_db` is a placeholder and no audit table exists in schema.
- Impact: Compliance/security claims are not backed by durable records.
- Evidence:
  - `app/core/audit.py`
  - `database/init.sql`
- Planned Fix:
  - Either implement durable audit persistence with schema support or downgrade claims and remove dead hooks.

## B006 — Metrics collector is mostly disconnected from runtime
- Status: Open
- Root Cause: A metrics framework exists, but task and service paths are not instrumented.
- Impact: Reported observability is largely fictitious; failures and latency trends are not measurable.
- Evidence:
  - `app/core/metrics.py`
  - absence of `metrics.track(...)` usage in task/service modules
- Planned Fix:
  - Instrument critical scanner, enrichment, scrape, and broadcast paths.

## B007 — Circuit breaker endpoints exist without actual service integration
- Status: Open
- Root Cause: Circuit breaker state is exposed through health APIs, but scanner calls rely on ad hoc retry only.
- Impact: Operators are given false confidence about failure isolation.
- Evidence:
  - `app/api/routers/health.py`
  - `app/core/circuit_breaker.py`
  - `app/services/scanners.py`
- Planned Fix:
  - Integrate breaker wrappers around external providers or remove unsupported control surface.

## B008 — CSV startup import is documented but not implemented
- Status: Open
- Root Cause: Entrypoint renames CSV files to `.pending` but does not parse or import them.
- Impact: Operators believe seed imports are happening while the system silently does nothing useful.
- Evidence:
  - `docker-entrypoint.sh`
  - `README.md`
  - `imports/README.md`
- Planned Fix:
  - Implement actual import pipeline or correct documentation and startup messaging.

## B009 — Multi-chat enrichment is intentionally dropped
- Status: Open
- Root Cause: Enrichment flow updates only the first discovered chat and ignores additional chats.
- Impact: Valid reachable chats remain untracked, reducing scrape coverage and backfill chance.
- Evidence:
  - `app/workers/tasks/flow_tasks.py`
- Planned Fix:
  - Persist secondary chats or add a modeled backlog queue for additional chat candidates.

## B010 — Chat discovery is too weak for cold tokens
- Status: Fixed
- Root Cause: Discovery is dominated by `getUpdates` and direct page extraction, with limited secondary correlation.
- Impact: Valid tokens become `pending`/`active` without chat context, leading to zero message backfill.
- Evidence:
  - `app/workers/tasks/scanner_tasks.py`
  - `app/workers/tasks/flow_tasks.py`
  - `app/services/scraper_srv.py`
- Planned Fix:
  - Introduce token viability scoring, secondary evidence extraction, and fallback discovery pivots.

## B011 — Headless scanners over-rely on live fetches
- Status: Open
- Root Cause: Provider hits are often followed by best-effort active fetches instead of first maximizing provider-returned evidence.
- Impact: Headless scan hit rate is poor compared with browser-assisted collection.
- Evidence:
  - `app/services/scanners.py`
  - `app/services/scanners_extension.py`
  - `chrome_extension/background.js`
- Planned Fix:
  - Add source-specific extractors and provider-native evidence parsing before live rescans.

## B012 — Broad TLS verification bypass in scanners
- Status: Open
- Root Cause: Multiple HTTP clients use `verify=False`.
- Impact: The system trusts unverified remote content and weakens transport integrity during active scans.
- Evidence:
  - `app/services/scanners.py`
  - `app/services/scanners_extension.py`
- Planned Fix:
  - Remove insecure defaults or gate them behind an explicit debug-only flag.

## B013 — Monitoring queries are inefficient and unbounded for growth
- Status: Open
- Root Cause: Stats endpoint uses expensive count/select patterns unsuitable for large tables.
- Impact: Monitoring becomes slower as data volume grows, and may stress the database.
- Evidence:
  - `app/api/routers/monitor.py`
- Planned Fix:
  - Replace with aggregate queries or precomputed stats.

## B014 — Test suite validates stale behavior
- Status: Open
- Root Cause: Tests mock outdated request paths and do not cover current async scanner contract.
- Impact: Regression detection is weak; broken runtime paths can appear green.
- Evidence:
  - `tests/integration/test_scanner_flow.py`
  - `app/workers/tasks/scanner_tasks.py`
- Planned Fix:
  - Realign tests to async `httpx`-based logic and current schema contracts.

## B015 — Frontend documentation is stale and non-descriptive
- Status: Open
- Root Cause: `frontend/README.md` is boilerplate and does not describe the implemented dashboard or direct database subscriptions.
- Impact: The documented system intent is materially inaccurate.
- Evidence:
  - `frontend/README.md`
  - `frontend/components/Sidebar.tsx`
  - `frontend/components/ChatWindow.tsx`
- Planned Fix:
  - Replace boilerplate documentation with actual UI/data-flow description.
