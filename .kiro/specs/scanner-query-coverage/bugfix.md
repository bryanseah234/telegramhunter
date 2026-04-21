# Bugfix Requirements Document

## Introduction

The system discovers Telegram bot tokens by querying internet-wide search engines (Shodan, Netlas, FOFA) and a browser extension that scrapes FOFA. The original approach used a single heavily-joined compound query requiring Telegram API patterns AND C2 keywords AND `/start` payloads all simultaneously. This over-constrains results — a host must expose all three signal types at once to match, which is rare in practice. The result is missed detections.

The correct approach is a **decoupled, tiered query strategy**: each signal group runs as independent queries. Any host that matches a Telegram fingerprint query is fetched and its body is scanned by `_perform_active_deep_scan` for tokens. C2 and malware keyword queries use `api.telegram.org/bot` as a relevance anchor (one keyword per query) rather than joining all keywords together. This maximises recall while keeping queries relevant.

A manual audit also identified specific missing entries within each scanner's existing query lists.

---

## Query Strategy

### Tier 1 — Telegram Fingerprint Queries (standalone, no anchor required)

These identify hosts that are directly serving or referencing the Telegram Bot API. Each runs independently:

- `http.headers:"X-Telegram-Bot-Api"` — server is actively proxying bot API traffic
- `http.body:"api.telegram.org/bot"` — body contains a direct bot API URL
- `http.body:"http://t.me/bot"` — body contains a t.me bot link (http variant)
- `http.body:"https://t.me" + http.body:"/start"` — body contains a t.me deep link with /start

### Tier 2 — C2 Payload Queries (anchored to `api.telegram.org/bot`, one payload per query)

These identify hosts where a Telegram bot is being used as a C2 channel. The anchor ensures relevance:

- `/start payload=`, `/start token=`, `/start cmd=`, `/start c2=`, `/start key=`, `/start id=`
- `/start /bin/bash`, `/start /powershell`, `/start download`
- `/start http://`, `/start https://`
- `/start /exec`, `/start /run`, `/start /invoke`, `/start /script`

### Tier 3 — Malware Keyword Queries (anchored to `api.telegram.org/bot`, one keyword per query)

These identify hosts where malware or RAT infrastructure is co-located with a Telegram bot:

- `malware`, `rat`, `remote access`, `spyware`, `stealer`, `keylogger`
- `c2 server`, `command and control`, `exploit`, `bypass`, `inject`
- `persistence`, `privilege escalation`

### Status Filter

All queries except header-based ones (`http.headers:`) SHALL include a `http.status:200` (Shodan) or `http.status_code:200` (Netlas) filter. FOFA queries SHALL include `status_code="200"`.

### Exclusion Filters (Shodan and Netlas only)

Queries that use `http.body:` fields SHALL append `-http.body:"telegram.org" -http.body:"github.com"` to suppress documentation and repository pages. FOFA does not support negation in the same way; exclusions are omitted there.

---

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `_scan_shodan_async` executes its default query list THEN the system uses a single over-joined compound query structure, causing it to miss hosts that only expose one or two signal types rather than all simultaneously

1.2 WHEN `_scan_shodan_async` executes its default query list THEN the system omits Tier 1 standalone queries for `http.body:"http://t.me/bot"` and `http.body:"https://t.me" http.body:"/start"`, missing hosts identified only by t.me URL patterns

1.3 WHEN `_scan_shodan_async` executes its default query list THEN the system omits Tier 2 C2 payload queries for `/start key=`, `/start id=`, `/start /run`, `/start /invoke`, and `/start /script`

1.4 WHEN `_scan_shodan_async` executes its default query list THEN the system omits Tier 3 malware keyword queries for `spyware`, `bypass`, `c2 server`, `command and control`, and `privilege escalation`

1.5 WHEN `_scan_shodan_async` executes non-header queries THEN the system omits `http.status:200`, causing results to include non-200 responses

1.6 WHEN `_scan_shodan_async` executes `http.body:` queries THEN the system applies no exclusion filters, causing false positives from documentation and repository pages

1.7 WHEN `scan_shodan_c2` executes its `C2_QUERIES` list THEN the system omits `/start key=` and `/start id=` Tier 2 payload queries

1.8 WHEN `scan_shodan_c2` executes its `C2_QUERIES` list THEN the system applies no exclusion filters

1.9 WHEN `_scan_netlas_async` executes `NETLAS_QUERIES` THEN the system omits Tier 2 queries for `/start id=`, `/start /run`, `/start /invoke`, `/start /script`, and `/start http://`

1.10 WHEN `_scan_netlas_async` executes `NETLAS_QUERIES` THEN the system applies no exclusion filters

1.11 WHEN `_scan_fofa_async` executes its default query list THEN the system includes no Tier 2 C2 payload queries at all

1.12 WHEN `_scan_fofa_async` executes its default query list THEN the system includes no Tier 3 malware keyword queries

1.13 WHEN `_scan_fofa_async` executes its default query list THEN the system includes no Tier 1 `t.me` URL pattern queries

1.14 WHEN `_scan_fofa_async` executes its default query list THEN the system applies no `status_code="200"` filter

1.15 WHEN the browser extension initiates a scan using `BASE_QUERY_TEMPLATE` THEN the system uses only `body="api.telegram.org"` with no Tier 2 or Tier 3 queries, missing the entire C2 and malware host population

### Expected Behavior (Correct)

2.1 WHEN `_scan_shodan_async` executes its default query list THEN the system SHALL run Tier 1, Tier 2, and Tier 3 queries as independent entries — not joined — so a host matching any single query is fetched and scanned

2.2 WHEN `_scan_shodan_async` executes its default query list THEN the system SHALL include standalone Tier 1 queries: `http.body:"http://t.me/bot" http.status:200` and `http.body:"https://t.me" http.body:"/start" http.status:200`

2.3 WHEN `_scan_shodan_async` executes its default query list THEN the system SHALL include Tier 2 queries for all 15 `/start` payload variants, each anchored to `api.telegram.org/bot` with `http.status:200`

2.4 WHEN `_scan_shodan_async` executes its default query list THEN the system SHALL include Tier 3 queries for all 13 malware keywords, each anchored to `api.telegram.org/bot` with `http.status:200`

2.5 WHEN `_scan_shodan_async` executes any `http.body:` query THEN the system SHALL append `-http.body:"telegram.org" -http.body:"github.com"` exclusion filters

2.6 WHEN `scan_shodan_c2` executes its `C2_QUERIES` list THEN the system SHALL include `/start key=` and `/start id=` as independent anchored queries with `http.status:200`

2.7 WHEN `scan_shodan_c2` executes any `http.body:` query THEN the system SHALL append `-http.body:"telegram.org" -http.body:"github.com"` exclusion filters

2.8 WHEN `_scan_netlas_async` executes `NETLAS_QUERIES` THEN the system SHALL include Tier 2 queries for `/start id=`, `/start /run`, `/start /invoke`, `/start /script`, and `/start http://`, each with `http.status_code:200`

2.9 WHEN `_scan_netlas_async` executes `NETLAS_QUERIES` THEN the system SHALL append exclusion filters to all `http.body:` queries

2.10 WHEN `_scan_fofa_async` executes its default query list THEN the system SHALL include Tier 2 C2 payload queries in FOFA syntax: `body="/start payload=" && body="api.telegram.org/bot" && status_code="200"` (one entry per payload variant)

2.11 WHEN `_scan_fofa_async` executes its default query list THEN the system SHALL include Tier 3 malware keyword queries in FOFA syntax: `body="malware" && body="api.telegram.org/bot" && status_code="200"` (one entry per keyword)

2.12 WHEN `_scan_fofa_async` executes its default query list THEN the system SHALL include Tier 1 `t.me` queries in FOFA syntax: `body="http://t.me/bot" && status_code="200"` and `body="https://t.me" && body="/start" && status_code="200"`

2.13 WHEN `_scan_fofa_async` executes its default query list THEN all queries SHALL include `status_code="200"`

2.14 WHEN the browser extension initiates a scan using `BASE_QUERY_TEMPLATE` THEN the system SHALL use `body="api.telegram.org/bot"` as the default (broader than the current `body="api.telegram.org"`) and the popup SHALL display a recommended query hint covering the `X-Telegram-Bot-Api` header pattern

### Unchanged Behavior (Regression Prevention)

3.1 WHEN any scanner service receives an explicit `query` parameter THEN the system SHALL CONTINUE TO use that caller-supplied query without modification

3.2 WHEN `scan_shodan_c2` runs its existing `C2_QUERIES` entries that already include `http.status:200` THEN the system SHALL CONTINUE TO execute those queries unchanged (additions only, no removals)

3.3 WHEN `NETLAS_QUERIES` entries that are already present and correct execute THEN the system SHALL CONTINUE TO run them in their current form and order (additions only, no removals)

3.4 WHEN `_scan_fofa_async` runs with its existing `body="api.telegram.org/bot"` and `body="sendMessage" && body="chat_id"` queries THEN the system SHALL CONTINUE TO include those queries in the default list

3.5 WHEN the extension's scan completes THEN the system SHALL CONTINUE TO validate tokens via the Telegram `getMe` API and upload results through the existing API or direct Supabase routes

3.6 WHEN any scanner encounters a paused system state THEN the system SHALL CONTINUE TO return early without executing queries

3.7 WHEN `_save_credentials_async` processes results from any scanner THEN the system SHALL CONTINUE TO deduplicate by token hash, validate token format, and call the Telegram API before saving

3.8 WHEN `_perform_active_deep_scan` is called on a host URL returned by any query THEN the system SHALL CONTINUE TO fetch the body, extract tokens via `TOKEN_PATTERN`, and validate via `_is_valid_token` — no changes to the deep scan logic

---

## Bug Condition Pseudocode

Bug Condition Function — identifies a scanner query list that violates the decoupled tier strategy:

```pascal
FUNCTION isBugCondition(queryList, scanner)
  INPUT: queryList — the list of query strings used by a scanner
         scanner   — identifier of the scanner service
  OUTPUT: boolean

  // A query list is buggy if it is missing any required tier entries,
  // or if it joins multiple tiers into a single query (over-constraining),
  // or if body queries lack the required status filter
  RETURN (
    NOT containsTier1Queries(queryList, scanner)
    OR NOT containsAllTier2Variants(queryList, scanner)
    OR NOT containsAllTier3Keywords(queryList, scanner)
    OR hasOverJoinedQueries(queryList)
    OR NOT allBodyQueriesHaveStatusFilter(queryList, scanner)
  )
END FUNCTION
```

Property: Fix Checking

```pascal
FOR ALL scanner IN {shodan_general, shodan_c2, netlas, fofa, extension} DO
  queryList <- getDefaultQueryList'(scanner)
  ASSERT NOT isBugCondition(queryList, scanner)
END FOR
```

Property: Preservation Checking

```pascal
FOR ALL scanner IN {shodan_general, shodan_c2, netlas, fofa, extension} DO
  FOR ALL query WHERE isExplicitCallerQuery(query) DO
    ASSERT getDefaultQueryList'(scanner, query) = [query]
  END FOR
END FOR
```

Property: Deep Scan Unchanged

```pascal
FOR ALL host IN hostsReturnedByAnyQuery DO
  ASSERT _perform_active_deep_scan(host) = _perform_active_deep_scan_original(host)
END FOR
```
