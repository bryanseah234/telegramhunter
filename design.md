# Remediation Design

## 1. Objective
Define the minimum high-impact remediation path to improve correctness, security boundaries, scanner hit rate, and backfill success without destabilizing the system.

## 2. Execution Boundary
This document establishes the approved design baseline for later surgical implementation. It does not itself modify runtime code.

## 3. Architecture Changes

### 3.1 Discovery pipeline hardening
Current discovery relies on raw provider queries, token regex extraction, `getMe`, limited `getUpdates`, and enrichment.

Planned change:
- Add a token viability stage between discovery and enrichment.
- Compute a score using:
  - provider confidence
  - direct chat evidence presence
  - successful `getMe`
  - successful `getUpdates`
  - repeated cross-source occurrence
- Use score to route work:
  - high-confidence: immediate enrichment
  - medium-confidence: delayed retry / secondary evidence search
  - low-confidence: persist for review only

Expected effect:
- Reduce wasted scrape attempts.
- Prioritize tokens with an actual chance of backfill.

### 3.2 Chat discovery expansion
Current chat discovery is underpowered for cold tokens.

Planned change:
- Expand evidence collection from provider payloads and live pages:
  - `chat_id`
  - `t.me` references
  - bot username
  - webhook references
  - page config values
- Add secondary correlation path:
  - reuse known `bot_id -> chat_id` mappings
  - re-search by bot username / bot ID where available
- Add eligibility gate before heavy scrape:
  - token valid
  - chat candidate exists
  - access probe succeeds or user-agent invite path is possible

Expected effect:
- Increase ratio of valid token -> chat found.
- Improve message backfill yield.

### 3.3 History backfill strategy correction
Current scrape flow spends time on bot-centric history methods that frequently fail for restricted bots.

Planned change:
- Prefer user-session history path earlier when restriction indicators appear.
- Add retry windows for cold-but-valid tokens.
- Track terminal reasons for no-backfill so the system distinguishes:
  - no chat evidence
  - chat inaccessible
  - bot restricted
  - user-session unavailable
  - scrape completed with zero messages

Expected effect:
- Better operator visibility.
- Better use of scrape queue capacity.

### 3.4 Security boundary repair
Current database policies and API routes expose sensitive/internal state too broadly.

Planned change:
- Restrict anonymous reads from sensitive tables.
- Introduce a safe projection for browser use.
- Add auth gates or production disablement for operational/admin routes.

Expected effect:
- Align implementation with stated security intent.

### 3.5 Observability truthfulness
Current audit, metrics, and breaker primitives exist but are weakly connected.

Planned change:
- Choose one of two consistent paths for each feature:
  - fully connect to runtime, or
  - explicitly downgrade/remove unsupported claims and dead control surfaces
- Initial preferred direction:
  - instrument metrics on critical path
  - document or defer audit persistence if schema work is not accepted this cycle
  - connect breakers only where provider wrappers can realistically support them

## 4. Data Model Changes

### 4.1 Credential persistence contract
Required invariant:
- All encrypted credentials must be persisted in the schema-defined field.

Planned contract:
- `discovered_credentials.bot_token`: only encrypted token payload
- `discovered_credentials.token_hash`: stable dedupe identity
- `discovered_credentials.meta`: derived public-ish metadata only

### 4.2 Candidate enrichment metadata
Planned additions inside `meta` or adjacent modeled fields:
- viability score
- evidence sources list
- last discovery method
- last chat discovery status
- backfill terminal reason

Decision note:
- Prefer incremental metadata additions first to avoid immediate schema churn.
- If metadata complexity grows too far, split into dedicated evidence/status tables in a later cycle.

### 4.3 Frontend-safe access model
Planned change:
- Frontend should not read raw credential rows directly from the sensitive source table.
- Expose a reduced read model limited to:
  - id
  - created_at
  - source
  - safe chat/bot display metadata

## 5. Interface Contracts

### 5.1 Scanner task output
Scanner tasks should always return a deterministic summary object/string with:
- provider name
- candidates found
- candidates saved
- candidates queued for enrichment
- error count

### 5.2 Enrichment result classification
Enrichment should classify outcome into explicit buckets:
- `chat_found`
- `valid_token_no_chat`
- `restricted_requires_user_agent`
- `invalid_token`
- `retry_later`

### 5.3 Backfill status reporting
Scrape path should record one final status per attempt:
- `messages_backfilled`
- `zero_messages_no_access`
- `zero_messages_no_history`
- `zero_messages_no_chat`
- `scrape_failed_transient`
- `scrape_failed_terminal`

## 6. Dependency Compatibility Verification
Based on repository evidence only:
- Python 3.11 is consistent across `Dockerfile` and `pyproject.toml`.
- FastAPI/Uvicorn/Celery/Redis architecture is already in use and no new runtime dependency is required for Phase 1 planning.
- Next.js frontend already uses Supabase JS; restricting exposed fields does not require a new frontend package.
- Proposed initial fixes are compatible with current dependency footprint because they are contract and routing changes, not framework swaps.

## 7. Non-Goals for Initial Surgical Cycle
- No full rewrite of scanner modules.
- No provider expansion beyond currently integrated sources.
- No immediate large schema redesign unless a minimal new table/view is necessary.
- No frontend redesign beyond access-path corrections and documentation accuracy.

## 8. Verification Approach for Later Phase 3
When implementation begins, each task will require:
- pre-scan of target file
- smallest-possible change
- local validation after each edit
- artifact updates (`bugfix.md`, `tasks.md`)
- explicit checkpoint summary
