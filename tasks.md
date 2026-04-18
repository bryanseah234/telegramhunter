# Atomic Task List

## T001 — Fix credential persistence contract
- Description: Align scanner write payloads with the database schema so encrypted token data is stored in the correct field.
- Acceptance Criteria:
  - All write paths targeting `discovered_credentials` use the schema-defined encrypted token field.
  - Downstream read/decrypt paths stay consistent.
  - No stale references to the old mismatched field remain in active code paths.
- Status: Complete

## T002 — Repair Shodan task result handling
- Description: Make the Shodan task return deterministic summaries on all success/error paths.
- Acceptance Criteria:
  - `_scan_shodan_async` initializes and returns a valid result summary regardless of error count.
  - No undefined local variable remains.
- Status: Complete

## T003 — Add token viability scoring to discovery pipeline
- Description: Introduce a scoring layer between token discovery and enrichment scheduling.
- Acceptance Criteria:
  - Discovery code computes a score from available evidence.
  - Enrichment scheduling behavior differs by confidence tier.
  - Low-confidence tokens do not immediately consume heavy scrape capacity.
- Status: Complete

## T004 — Strengthen chat discovery evidence extraction
- Description: Expand how chat candidates are inferred before enrichment/backfill.
- Acceptance Criteria:
  - Provider/live evidence extraction includes more than raw token regex alone.
  - Existing known metadata can improve chat candidate resolution.
  - Chat discovery failure reasons are recorded explicitly.
- Status: Complete

## T005 — Add scrape eligibility gate
- Description: Prevent heavy backfill attempts when prerequisite access/context is missing.
- Acceptance Criteria:
  - Backfill is skipped or deferred when token/chat/access prerequisites are not met.
  - Skip/defer decisions are observable in logs/status metadata.
- Status: Complete

## T006 — Prefer user-session history earlier for restricted bots
- Description: Reroute backfill attempts toward the user-session path earlier when bot restrictions are detected.
- Acceptance Criteria:
  - Restriction indicators trigger earlier fallback.
  - The scrape queue loses fewer cycles to known-bad bot-history attempts.
- Status: Complete

## T007 — Introduce retry windows for cold valid tokens
- Description: Add delayed retry logic for tokens that validate but lack immediate chat evidence.
- Acceptance Criteria:
  - Retry classification exists.
  - Cold valid tokens are revisited on a controlled schedule instead of being treated as terminal failures.
- Status: Complete

## T008 — Restrict anonymous read access to sensitive credential data
- Description: Repair database/browser access boundaries.
- Acceptance Criteria:
  - Anonymous/browser paths can no longer read sensitive credential rows directly.
  - Frontend access remains functional through a safe projection or equivalent reduced contract.
- Status: Complete

## T009 — Add auth or production safeguards to monitor/admin endpoints
- Description: Protect monitoring and operational endpoints.
- Acceptance Criteria:
  - Monitor/admin routes are gated or explicitly disabled in production.
  - Unsafe public invocation paths are removed.
- Status: Complete

## T010 — Decide and implement observability truthfulness baseline
- Description: Connect or downgrade audit/metrics/circuit-breaker claims so the system state is truthful.
- Acceptance Criteria:
  - Metrics are wired into at least the critical task path, or the feature claim is reduced.
  - Audit persistence is either implemented minimally or clearly left non-durable with documentation corrected.
  - Circuit breaker control surface matches actual runtime behavior.
- Status: Complete

## T011 — Correct CSV import behavior or documentation
- Description: Eliminate the mismatch between documented CSV import and startup reality.
- Acceptance Criteria:
  - Startup either performs a real import flow, or all misleading docs/messages are corrected.
- Status: Pending

## T012 — Update stale tests for current async/runtime contract
- Description: Realign tests with the active scanner and schema implementation.
- Acceptance Criteria:
  - Tests target current async request path and current field names.
  - At least one regression test exists for persistence contract and scanner summary handling.
- Status: Pending

## T013 — Replace stale frontend documentation
- Description: Rewrite the frontend documentation to describe actual data flow and constraints.
- Acceptance Criteria:
  - `frontend/README.md` no longer contains starter boilerplate.
  - It documents realtime data flow, read-only behavior, and security assumptions.
- Status: Pending

## Checkpoint Rules
- After each completed task:
  - update this file status to `Complete`
  - update corresponding item in `bugfix.md`
  - emit a `State Summary`
- If ambiguity blocks implementation:
  - stop and ask for clarification before editing code
