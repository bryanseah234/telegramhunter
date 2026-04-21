# Scanner Query Coverage Bugfix Design

## Overview

The scanner pipeline discovers Telegram bot tokens by querying Shodan, Netlas, FOFA, and a
browser extension that scrapes FOFA. The current query lists use over-joined compound queries
that require multiple signal types to co-occur on a single host, which is rare in practice.
The fix decouples the query strategy into three independent tiers and adds missing entries to
each scanner's list. The change is purely additive — no existing queries are removed, no
scanner logic is altered, and no service classes are touched.

The fix also introduces two structural improvements: module-level query constants
(`SHODAN_DEFAULT_QUERIES`, `FOFA_DEFAULT_QUERIES`) replace inline list construction inside
async functions, and a small helper `_shodan_body_query` centralises the exclusion-filter
suffix so it cannot be omitted from new entries.

## Glossary

- **Bug_Condition (C)**: A scanner's default query list that is missing required tier entries,
  joins multiple tiers into a single compound query, or omits required status/exclusion filters.
- **Property (P)**: After the fix, every scanner's default query list satisfies the decoupled
  tier strategy — all required entries are present, each with the correct status filter and
  (where applicable) exclusion filters.
- **Preservation**: All existing query entries, caller-supplied query pass-through, pause-state
  early-return, token deduplication, and deep-scan logic remain unchanged.
- **`SHODAN_DEFAULT_QUERIES`**: New module-level constant in `scanner_tasks.py` that replaces
  the inline `default_queries` list built inside `_scan_shodan_async`.
- **`FOFA_DEFAULT_QUERIES`**: New module-level constant in `scanner_tasks.py` that replaces
  the inline `COMMON_QUERIES` list built inside `_scan_fofa_async`.
- **`_shodan_body_query(anchor, extra="")`**: Helper that appends
  `-http.body:"telegram.org" -http.body:"github.com" http.status:200` to a Shodan body query,
  eliminating per-entry duplication of the exclusion suffix.
- **`C2_QUERIES`**: Inline list inside `_scan_shodan_c2_async` used by `scan_shodan_c2`.
- **`NETLAS_QUERIES`**: Existing module-level constant used by `_scan_netlas_async`.
- **`BASE_QUERY_TEMPLATE`**: Constant in `extension/background.js` used as the default FOFA
  query when the extension starts a scan.
- **Tier 1**: Standalone Telegram fingerprint queries — no anchor required.
- **Tier 2**: C2 payload queries anchored to `api.telegram.org/bot`, one payload per query.
- **Tier 3**: Malware keyword queries anchored to `api.telegram.org/bot`, one keyword per query.

## Bug Details

### Bug Condition

The bug manifests when any scanner's default query list is evaluated. The list either joins
multiple signal types into a single compound query (over-constraining recall), omits required
tier entries, or omits status/exclusion filters that suppress noise.

**Formal Specification:**
```
FUNCTION isBugCondition(queryList, scanner)
  INPUT: queryList — the list of query strings used by a scanner
         scanner   — one of {shodan_general, shodan_c2, netlas, fofa, extension}
  OUTPUT: boolean

  RETURN (
    NOT containsAllTier1Queries(queryList, scanner)
    OR NOT containsAllTier2Variants(queryList, scanner)
    OR NOT containsAllTier3Keywords(queryList, scanner)
    OR hasOverJoinedQueries(queryList)
    OR NOT allBodyQueriesHaveStatusFilter(queryList, scanner)
    OR (scanner IN {shodan_general, shodan_c2, netlas}
        AND NOT allBodyQueriesHaveExclusionFilters(queryList, scanner))
  )
END FUNCTION
```

### Examples

- **Shodan general — over-joined**: Current list contains
  `'http.body:"api.telegram.org/bot" http.body:"/start" http.body:"malware"'` — a host must
  expose all three signals simultaneously. Expected: three independent queries, one per signal.
- **Shodan general — missing Tier 2**: `/start key=` and `/start id=` are absent from
  `default_queries`. Expected: both present as independent anchored queries with status filter.
- **Shodan general — missing Tier 3**: `spyware`, `bypass`, `c2 server`, `command and control`,
  `privilege escalation` are absent. Expected: all 13 keywords present as independent queries.
- **Shodan general — no exclusion filters**: `'http.body:"api.telegram.org/bot" http.body:"/start" http.body:"malware"'`
  has no `-http.body:"telegram.org"` suffix. Expected: exclusion filters on all body queries.
- **C2_QUERIES — missing entries**: `/start key=` and `/start id=` are absent. Expected: both
  present with status filter and exclusion filters.
- **NETLAS_QUERIES — missing entries**: `/start id=`, `/start /run`, `/start /invoke`,
  `/start /script`, `/start http://` are absent. Expected: all five present with status filter.
- **NETLAS_QUERIES — no exclusion filters**: Body queries lack
  `NOT http.body:"telegram.org" NOT http.body:"github.com"`. Expected: exclusions appended.
- **FOFA — no Tier 2/3**: `COMMON_QUERIES` contains no C2 payload or malware keyword entries.
  Expected: all 15 Tier 2 and 13 Tier 3 entries in FOFA syntax with `status_code="200"`.
- **Extension — broad anchor**: `BASE_QUERY_TEMPLATE = 'body="api.telegram.org"'` matches
  documentation pages. Expected: `body="api.telegram.org/bot"`.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- When any scanner receives an explicit `query` parameter, that caller-supplied query is used
  as-is without modification (requirements 3.1).
- All entries currently present in `C2_QUERIES`, `NETLAS_QUERIES`, and `_scan_fofa_async`'s
  query list continue to execute in their current form and order (requirements 3.2, 3.3, 3.4).
- The extension's token validation (`getMe`), deduplication, and upload routes are unchanged
  (requirement 3.5).
- Pause-state early-return in every scanner task is unchanged (requirement 3.6).
- `_save_credentials_async` deduplication, format validation, and Telegram API call are
  unchanged (requirement 3.7).
- `_perform_active_deep_scan`, `_is_valid_token`, `TOKEN_PATTERN`, and all service classes
  are unchanged (requirement 3.8).

**Scope:**
All code paths that do NOT involve the default query list construction in `_scan_shodan_async`,
`_scan_shodan_c2_async`, `_scan_fofa_async`, `NETLAS_QUERIES`, or `BASE_QUERY_TEMPLATE` are
completely unaffected by this fix.

## Hypothesized Root Cause

1. **Incremental list growth without a coverage audit**: The query lists were extended
   organically. New entries were added when specific C2 patterns were observed, but no
   systematic check was made against the full tier matrix, leaving gaps.

2. **Over-joined compound queries**: Early entries combined multiple signal types
   (`/start` + keyword) into a single query string to reduce API call count. This trades
   recall for cost efficiency — a reasonable short-term decision that became a coverage bug
   as the tier strategy was formalised.

3. **Missing status and exclusion filters on older entries**: The `http.status:200` and
   exclusion filter conventions were adopted after the initial query lists were written.
   Older entries were not retroactively updated.

4. **No module-level constants for Shodan/FOFA**: Because the query lists were inline inside
   async functions, they were harder to audit and compare against the tier matrix. The
   `NETLAS_QUERIES` pattern (module-level constant) was not applied consistently.

5. **Extension anchor too broad**: `body="api.telegram.org"` was the original anchor before
   the `/bot` path suffix was identified as a more precise discriminator.

## Correctness Properties

Property 1: Bug Condition — All Scanner Query Lists Satisfy the Tier Strategy

_For any_ scanner in `{shodan_general, shodan_c2, netlas, fofa, extension}`, after the fix,
`isBugCondition(getDefaultQueryList(scanner), scanner)` SHALL return `false` — meaning the
list contains all required Tier 1, Tier 2, and Tier 3 entries as independent queries, every
body query carries the correct status filter, and (for Shodan and Netlas) every body query
carries the exclusion filters.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14**

Property 2: Preservation — Caller-Supplied Query Pass-Through

_For any_ scanner and _for any_ non-empty string `q` passed as the explicit `query` parameter,
the fixed scanner SHALL use exactly `[q]` as its query list, producing the same result as the
original function for that input.

**Validates: Requirements 3.1**

Property 3: Preservation — No Existing Entries Removed

_For any_ entry `e` that was present in `C2_QUERIES`, `NETLAS_QUERIES`, or
`_scan_fofa_async`'s query list before the fix, the fixed list SHALL still contain `e`.

**Validates: Requirements 3.2, 3.3, 3.4**

## Fix Implementation

### Changes Required

**File**: `app/workers/tasks/scanner_tasks.py`

**Change 1 — Add `_shodan_body_query` helper** (new module-level function, before query constants):
```python
def _shodan_body_query(anchor: str, extra: str = "") -> str:
    parts = [anchor]
    if extra:
        parts.append(extra)
    parts += ['-http.body:"telegram.org"', '-http.body:"github.com"', "http.status:200"]
    return " ".join(parts)
```

**Change 2 — Add `SHODAN_DEFAULT_QUERIES` module-level constant** (replaces inline
`default_queries` in `_scan_shodan_async`):
- Tier 1: `http.headers:"X-Telegram-Bot-Api"`, `_shodan_body_query('http.body:"api.telegram.org/bot"')`,
  `_shodan_body_query('http.body:"http://t.me/bot"')`,
  `_shodan_body_query('http.body:"https://t.me"', 'http.body:"/start"')`
- Tier 2: all 15 `/start` payload variants via `_shodan_body_query`
- Tier 3: all 13 malware keywords via `_shodan_body_query`
- Retain existing non-body entries (`http.title:`, `http.html:` entries) unchanged

**Change 3 — Update `_scan_shodan_async`**: Replace the inline `COMMON_QUERIES` + `default_queries`
construction with `default_queries = SHODAN_DEFAULT_QUERIES`.

**Change 4 — Update `C2_QUERIES` in `_scan_shodan_c2_async`**: Add the two missing entries
using `_shodan_body_query`:
- `_shodan_body_query('http.body:"api.telegram.org/bot"', 'http.body:"/start key="')`
- `_shodan_body_query('http.body:"api.telegram.org/bot"', 'http.body:"/start id="')`
  Apply `_shodan_body_query` to all existing body entries to add exclusion filters.

**Change 5 — Update `NETLAS_QUERIES`**: Add the 5 missing Tier 2 entries:
- `'http.body:"api.telegram.org/bot" http.body:"/start id=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"'`
- Same pattern for `/start /run`, `/start /invoke`, `/start /script`, `/start http://`
  Apply Netlas-syntax exclusion filters (`NOT http.body:"telegram.org" NOT http.body:"github.com"`)
  to all existing `http.body:` entries in `NETLAS_QUERIES`.

**Change 6 — Add `FOFA_DEFAULT_QUERIES` module-level constant** (replaces inline `COMMON_QUERIES`
in `_scan_fofa_async`):
- Retain existing 5 entries (with `status_code="200"` added where missing)
- Add Tier 1 t.me entries: `'body="http://t.me/bot" && status_code="200"'`,
  `'body="https://t.me" && body="/start" && status_code="200"'`
- Add all 15 Tier 2 entries: `'body="/start payload=" && body="api.telegram.org/bot" && status_code="200"'` etc.
- Add all 13 Tier 3 entries: `'body="malware" && body="api.telegram.org/bot" && status_code="200"'` etc.

**Change 7 — Update `_scan_fofa_async`**: Replace inline `COMMON_QUERIES` with
`queries = [query] if query else FOFA_DEFAULT_QUERIES`.

**File**: `extension/background.js`

**Change 8 — Update `BASE_QUERY_TEMPLATE`**:
```js
const BASE_QUERY_TEMPLATE = 'body="api.telegram.org/bot"'; // Narrowed to /bot path
```

### No Changes To
`_perform_active_deep_scan`, `_is_valid_token`, `TOKEN_PATTERN`, `_save_credentials_async`,
any service class (`ShodanService`, `FofaService`, `NetlasService`, etc.), or any other task.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that
demonstrate the bug on the unfixed code, then verify the fix satisfies all three correctness
properties and preserves existing behaviour.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix.
Confirm or refute the root cause analysis.

**Test Plan**: Import the unfixed `scanner_tasks` module and inspect the query lists directly.
Assert that required entries are present — these assertions will fail on unfixed code, exposing
the exact gaps.

**Test Cases**:
1. **Shodan Tier 2 gap**: Assert `/start key=` and `/start id=` are in `default_queries` —
   will fail on unfixed code (will fail on unfixed code)
2. **Shodan Tier 3 gap**: Assert `spyware`, `bypass`, `privilege escalation` are in
   `default_queries` — will fail on unfixed code
3. **Shodan exclusion filter gap**: Assert every `http.body:` entry in `default_queries`
   contains `-http.body:"telegram.org"` — will fail on unfixed code
4. **FOFA Tier 2/3 gap**: Assert any `/start payload=` entry exists in FOFA queries —
   will fail on unfixed code
5. **Extension anchor**: Assert `BASE_QUERY_TEMPLATE` equals `'body="api.telegram.org/bot"'` —
   will fail on unfixed code

**Expected Counterexamples**:
- `AssertionError`: required query strings not found in the list
- Root cause confirmed: incremental growth without coverage audit, no exclusion filter
  convention applied retroactively

### Fix Checking

**Goal**: Verify that after the fix, `isBugCondition` returns `false` for all scanners.

**Pseudocode:**
```
FOR ALL scanner IN {shodan_general, shodan_c2, netlas, fofa, extension} DO
  queryList <- getDefaultQueryList_fixed(scanner)
  ASSERT NOT isBugCondition(queryList, scanner)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold (caller-supplied
query), the fixed scanner produces the same result as the original.

**Pseudocode:**
```
FOR ALL scanner IN {shodan_general, shodan_c2, netlas, fofa} DO
  FOR ALL query WHERE isExplicitCallerQuery(query) DO
    ASSERT getQueryList_fixed(scanner, query) = [query]
  END FOR
END FOR

FOR ALL entry IN originalEntries(C2_QUERIES ∪ NETLAS_QUERIES ∪ FOFA_COMMON_QUERIES) DO
  ASSERT entry IN fixedQueryList
END FOR
```

**Testing Approach**: Property-based testing is recommended for the caller-supplied query
pass-through because it generates arbitrary query strings and verifies the invariant holds
across all of them. List-membership preservation is verified with example-based tests.

**Test Cases**:
1. **Caller query pass-through**: Generate random query strings; assert each scanner uses
   exactly `[query]` when the parameter is supplied
2. **C2_QUERIES no removals**: Assert all 27 entries present before the fix are still present
3. **NETLAS_QUERIES no removals**: Assert all 34 entries present before the fix are still present
4. **FOFA no removals**: Assert the original 5 entries are still present

### Unit Tests

- Assert `SHODAN_DEFAULT_QUERIES` contains all 4 Tier 1, 15 Tier 2, and 13 Tier 3 entries
- Assert every `http.body:` entry in `SHODAN_DEFAULT_QUERIES` contains `http.status:200`
- Assert every `http.body:` entry in `SHODAN_DEFAULT_QUERIES` contains both exclusion strings
- Assert `C2_QUERIES` contains `/start key=` and `/start id=` entries
- Assert every `http.body:` entry in `C2_QUERIES` contains both exclusion strings
- Assert `NETLAS_QUERIES` contains all 5 previously missing Tier 2 entries
- Assert every `http.body:` entry in `NETLAS_QUERIES` contains Netlas-syntax exclusion filters
- Assert `FOFA_DEFAULT_QUERIES` contains all 15 Tier 2 and 13 Tier 3 entries in FOFA syntax
- Assert every entry in `FOFA_DEFAULT_QUERIES` contains `status_code="200"`
- Assert `BASE_QUERY_TEMPLATE == 'body="api.telegram.org/bot"'`

### Property-Based Tests

- Generate arbitrary non-empty query strings; for each scanner, assert the query list equals
  `[query]` when the parameter is supplied (caller pass-through preservation)
- Generate random subsets of the required tier entries; assert `isBugCondition` returns `true`
  for any list missing at least one required entry (validates the bug condition detector itself)
- Assert that for any entry in the pre-fix `NETLAS_QUERIES` snapshot, the entry is present in
  the post-fix list (no-removal property over the full list)

### Integration Tests

- Run `_scan_shodan_async` with a mock `ShodanService` that records queries; assert the
  recorded query set is a superset of all required tier entries
- Run `_scan_fofa_async` with a mock `FofaService`; assert `status_code="200"` appears in
  every recorded query
- Run `_scan_netlas_async` with a mock `NetlasService`; assert the 5 previously missing
  entries appear in the recorded query set
- Run `scan_shodan_c2` with a mock; assert `/start key=` and `/start id=` appear with
  exclusion filters
