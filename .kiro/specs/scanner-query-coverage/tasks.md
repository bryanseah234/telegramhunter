# Implementation Plan

- [ ] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Scanner Query Lists Violate Decoupled Tier Strategy
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists across all five scanners
  - **Scoped PBT Approach**: For each scanner, scope the property to the concrete failing cases (missing entries, over-joined queries, missing filters)
  - Import `scanner_tasks` and `extension/background.js` constants and inspect the query lists directly
  - Assert that `SHODAN_DEFAULT_QUERIES` (or the inline `default_queries`) contains all 4 Tier 1, 15 Tier 2, and 13 Tier 3 entries as independent queries — will fail on unfixed code
  - Assert that every `http.body:` entry in the Shodan general list contains `http.status:200` and both exclusion strings (`-http.body:"telegram.org"`, `-http.body:"github.com"`) — will fail on unfixed code
  - Assert that `C2_QUERIES` contains `/start key=` and `/start id=` entries with exclusion filters — will fail on unfixed code
  - Assert that `NETLAS_QUERIES` contains `/start id=`, `/start /run`, `/start /invoke`, `/start /script`, and `/start http://` entries — will fail on unfixed code
  - Assert that every `http.body:` entry in `NETLAS_QUERIES` contains Netlas-syntax exclusion filters (`NOT http.body:"telegram.org" NOT http.body:"github.com"`) — will fail on unfixed code
  - Assert that `FOFA_DEFAULT_QUERIES` (or the inline `COMMON_QUERIES`) contains at least one Tier 2 `/start payload=` entry in FOFA syntax — will fail on unfixed code
  - Assert that `FOFA_DEFAULT_QUERIES` contains at least one Tier 3 malware keyword entry in FOFA syntax — will fail on unfixed code
  - Assert that `BASE_QUERY_TEMPLATE` equals `'body="api.telegram.org/bot"'` — will fail on unfixed code
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bug exists)
  - Document counterexamples found (e.g., "AssertionError: '/start key=' not found in default_queries", "AssertionError: BASE_QUERY_TEMPLATE is 'body=\"api.telegram.org\"' not 'body=\"api.telegram.org/bot\"'")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15_

- [ ] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Caller-Supplied Query Pass-Through and No Existing Entries Removed
  - **IMPORTANT**: Follow observation-first methodology
  - Observe: when `_scan_shodan_async(query="my_custom_query")` is called on unfixed code, the query list is `["my_custom_query"]`
  - Observe: when `_scan_fofa_async(task_self, query="my_custom_query")` is called on unfixed code, the query list is `["my_custom_query"]`
  - Observe: when `_scan_netlas_async(query="my_custom_query")` is called on unfixed code, the query list is `["my_custom_query"]`
  - Observe: all 29 entries currently in `C2_QUERIES` are present (including the header entry and all body entries)
  - Observe: all 34 entries currently in `NETLAS_QUERIES` are present
  - Observe: all 5 entries currently in `_scan_fofa_async`'s `COMMON_QUERIES` are present (`body="api.telegram.org/bot"`, `body="bot_token"`, `body="TELEGRAM_BOT_TOKEN"`, `title="Telegram Bot"`, `body="sendMessage" && body="chat_id"`)
  - Write property-based test: for all non-empty arbitrary query strings `q`, each scanner (shodan, fofa, netlas) uses exactly `[q]` as its query list when `query=q` is supplied (from Preservation Requirements 3.1 in design)
  - Write example-based test: assert all 29 pre-fix `C2_QUERIES` entries are still present in the fixed list (from Preservation Requirements 3.2)
  - Write example-based test: assert all 34 pre-fix `NETLAS_QUERIES` entries are still present in the fixed list (from Preservation Requirements 3.3)
  - Write example-based test: assert all 5 pre-fix FOFA `COMMON_QUERIES` entries are still present in the fixed list (from Preservation Requirements 3.4)
  - Verify all tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [ ] 3. Fix scanner query coverage across all five scanners

  - [ ] 3.1 Add `_shodan_body_query` helper to `scanner_tasks.py`
    - Add new module-level helper function before the query constants
    - Function signature: `def _shodan_body_query(anchor: str, extra: str = "") -> str`
    - Builds query by joining anchor, optional extra, `-http.body:"telegram.org"`, `-http.body:"github.com"`, and `http.status:200`
    - This centralises the exclusion-filter suffix so it cannot be omitted from new entries
    - _Bug_Condition: isBugCondition(queryList, scanner) where allBodyQueriesHaveExclusionFilters returns False_
    - _Expected_Behavior: every http.body: query produced by this helper contains both exclusion strings and http.status:200_
    - _Preservation: helper is additive — no existing code paths are altered_
    - _Requirements: 2.5, 2.7, 2.9_

  - [ ] 3.2 Add `SHODAN_DEFAULT_QUERIES` module-level constant and update `_scan_shodan_async`
    - Add `SHODAN_DEFAULT_QUERIES` as a module-level list constant (replaces inline `default_queries` construction)
    - Tier 1 entries: `'http.headers:"X-Telegram-Bot-Api"'`, `_shodan_body_query('http.body:"api.telegram.org/bot"')`, `_shodan_body_query('http.body:"http://t.me/bot"')`, `_shodan_body_query('http.body:"https://t.me"', 'http.body:"/start"')`
    - Tier 2 entries: all 15 `/start` payload variants via `_shodan_body_query` (payload=, token=, cmd=, c2=, key=, id=, /bin/bash, /powershell, download, /exec, /run, /invoke, /script, http://, https://)
    - Tier 3 entries: all 13 malware keywords via `_shodan_body_query` (malware, rat, remote access, spyware, stealer, keylogger, c2 server, command and control, exploit, bypass, inject, persistence, privilege escalation)
    - Retain existing `http.html:` and `http.title:` entries from `COMMON_QUERIES` unchanged
    - Update `_scan_shodan_async` to use `default_queries = SHODAN_DEFAULT_QUERIES` instead of inline construction
    - _Bug_Condition: isBugCondition(queryList, shodan_general) — missing Tier 2/3 entries, over-joined queries, no exclusion filters_
    - _Expected_Behavior: SHODAN_DEFAULT_QUERIES contains all 4 Tier 1, 15 Tier 2, 13 Tier 3 entries as independent queries, each http.body: entry has status filter and exclusion filters_
    - _Preservation: all existing http.html: and http.title: entries retained; caller-supplied query pass-through unchanged_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ] 3.3 Update `C2_QUERIES` in `_scan_shodan_c2_async` to add missing entries and exclusion filters
    - Add two missing Tier 2 entries using `_shodan_body_query`: `/start key=` and `/start id=`
    - Apply `_shodan_body_query` to all existing `http.body:` entries to add exclusion filters (entries that already have `http.status:200` but lack exclusion filters)
    - Retain the header-based entry `'http.headers:"X-Telegram-Bot-Api"'` unchanged (no body filter needed)
    - _Bug_Condition: isBugCondition(queryList, shodan_c2) — missing /start key= and /start id=, no exclusion filters on body queries_
    - _Expected_Behavior: C2_QUERIES contains /start key= and /start id= entries; all http.body: entries have both exclusion strings_
    - _Preservation: all 29 existing C2_QUERIES entries retained; no entries removed_
    - _Requirements: 2.6, 2.7_

  - [ ] 3.4 Update `NETLAS_QUERIES` to add missing Tier 2 entries and exclusion filters
    - Add 5 missing Tier 2 entries with Netlas syntax and exclusion filters: `/start id=`, `/start /run`, `/start /invoke`, `/start /script`, `/start http://`
    - Format: `'http.body:"api.telegram.org/bot" http.body:"/start id=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"'`
    - Apply Netlas-syntax exclusion filters (`NOT http.body:"telegram.org" NOT http.body:"github.com"`) to all existing `http.body:` entries in `NETLAS_QUERIES` that lack them
    - _Bug_Condition: isBugCondition(queryList, netlas) — missing /start id=, /run, /invoke, /script, http:// entries; no exclusion filters_
    - _Expected_Behavior: NETLAS_QUERIES contains all 5 previously missing Tier 2 entries; all http.body: entries have Netlas-syntax exclusion filters_
    - _Preservation: all 34 existing NETLAS_QUERIES entries retained; no entries removed_
    - _Requirements: 2.8, 2.9_

  - [ ] 3.5 Add `FOFA_DEFAULT_QUERIES` module-level constant and update `_scan_fofa_async`
    - Add `FOFA_DEFAULT_QUERIES` as a module-level list constant (replaces inline `COMMON_QUERIES` construction)
    - Retain all 5 existing entries, adding `status_code="200"` where missing
    - Add Tier 1 t.me entries: `'body="http://t.me/bot" && status_code="200"'`, `'body="https://t.me" && body="/start" && status_code="200"'`
    - Add all 15 Tier 2 entries in FOFA syntax: `'body="/start payload=" && body="api.telegram.org/bot" && status_code="200"'` (one per payload variant)
    - Add all 13 Tier 3 entries in FOFA syntax: `'body="malware" && body="api.telegram.org/bot" && status_code="200"'` (one per keyword)
    - Update `_scan_fofa_async` to use `queries = [query] if query else FOFA_DEFAULT_QUERIES`
    - _Bug_Condition: isBugCondition(queryList, fofa) — no Tier 2/3 entries, no t.me Tier 1 entries, no status_code="200" filter_
    - _Expected_Behavior: FOFA_DEFAULT_QUERIES contains all 15 Tier 2 and 13 Tier 3 entries in FOFA syntax; every entry includes status_code="200"_
    - _Preservation: all 5 original COMMON_QUERIES entries retained; caller-supplied query pass-through unchanged_
    - _Requirements: 2.10, 2.11, 2.12, 2.13_

  - [ ] 3.6 Update `BASE_QUERY_TEMPLATE` in `extension/background.js`
    - Change `BASE_QUERY_TEMPLATE` from `'body="api.telegram.org"'` to `'body="api.telegram.org/bot"'`
    - This narrows the default anchor to the `/bot` path, reducing documentation page false positives
    - _Bug_Condition: isBugCondition(queryList, extension) — BASE_QUERY_TEMPLATE uses broad anchor without /bot path_
    - _Expected_Behavior: BASE_QUERY_TEMPLATE equals 'body="api.telegram.org/bot"'_
    - _Preservation: token validation (getMe), deduplication, upload routes, and all other extension logic unchanged_
    - _Requirements: 2.14_

  - [ ] 3.7 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Scanner Query Lists Satisfy Decoupled Tier Strategy
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior for all five scanners
    - When this test passes, it confirms all required tier entries are present, all body queries have status filters, and all applicable body queries have exclusion filters
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed across all scanners)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14_

  - [ ] 3.8 Verify preservation tests still pass
    - **Property 2: Preservation** - Caller-Supplied Query Pass-Through and No Existing Entries Removed
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions — caller pass-through intact, no entries removed)
    - Confirm all tests still pass after fix (no regressions)

- [ ] 4. Checkpoint — Ensure all tests pass
  - Run the full test suite: `pytest tests/unit/ tests/integration/ -v`
  - Ensure all tests pass, ask the user if questions arise
  - Confirm that the bug condition exploration test (task 1) now passes
  - Confirm that all preservation tests (task 2) still pass
  - Confirm no existing scanner tests are broken
