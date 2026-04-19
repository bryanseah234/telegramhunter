# Product Requirements Document — Telegram Hunter

> **Source of truth**: This document reflects the codebase as implemented. Every claim is grounded in source files, database DDL (`database/init.sql`, `database/rls_policies.sql`), or container configuration (`docker-compose.yml`). Features absent from code are omitted.

---

## 1. Executive Summary

Telegram Hunter is a self-hosted, continuously-running OSINT pipeline that automatically discovers exposed Telegram bot tokens across 11 public data sources, validates each token against the live Telegram API, harvests full message history from every chat the bot can access, and broadcasts findings into a private Telegram supergroup organized by per-bot forum topics.

The system is designed for security researchers and intelligence analysts who require automated, continuous monitoring of public credential leakage with minimal operational overhead.

---

## 2. System Architecture

### 2.1 Component Topology

```text
┌──────────────────────────────────┐
│   Supabase (managed Postgres)    │  Row Level Security, Fernet-encrypted tokens
└──────────────┬───────────────────┘
               │
       ┌───────┴────────────────────────────┐
       │                                    │
┌──────▼──────┐                   ┌─────────▼──────────┐
│  API Service│                   │   Celery Workers   │
│  (FastAPI,  │                   │  3 queues:         │
│  2 workers) │                   │  celery / scanners │
└─────────────┘                   │  / scrape          │
                                  └────────────────────┘
                                           │
                                  ┌────────▼────────────┐
                                  │   Redis 7-alpine    │  broker, locks, counters
                                  └─────────────────────┘
                                           │
                                  ┌────────▼────────────┐
                                  │   Celery Beat       │  20 scheduled tasks
                                  └─────────────────────┘
                                           │
                                  ┌────────▼────────────┐
                                  │   Bot Listener      │  admin Telegram commands
                                  └─────────────────────┘
```

### 2.2 Service Inventory

| Service | Base Image | Concurrency | Queue |
|---|---|---|---|
| `redis` | redis:7-alpine | — | — |
| `api` | python:3.11-slim | 2 uvicorn workers | HTTP |
| `worker-core` | python:3.11-slim | 4 | `celery` |
| `worker-scanners` | python:3.11-slim | 2 | `scanners` |
| `worker-scrape` | python:3.11-slim | 2 | `scrape` |
| `beat` | python:3.11-slim | — | scheduler |
| `bot` | python:3.11-slim | async | messages |

**Named volumes:** `redis_data`, `sessions`, `imports`
**Log driver:** json-file (10 MB max, 3 rotations per service)

### 2.3 Data Flow Pipeline

```
[Scanner Sources x11]
        │
        ▼ regex extraction + format validation
[Token Validation]  ──── GET /getMe ────► Telegram Bot API
        │ live token confirmed
        ▼
[Persistence]  ──── Fernet encrypt ────► discovered_credentials (status=pending)
        │
        ▼ Celery: flow.enrich_credential
[Enrichment]  ──── Telethon get_dialogs ──► all chats enumerated
        │          create forum topic ──────► Monitor Supergroup
        ▼ status=active
[Exfiltration]  (4 strategies, see §3.5)
        │          upsert ─────────────────► exfiltrated_messages
        ▼
[Broadcasting]  ──── atomic DB claim ──► post to topic ──► Monitor Supergroup
        │          mark is_broadcasted=true
        ▼
[Self-Healing]  ──── hourly/6h audits ──► reconcile DB vs Telegram reality
```

---

## 3. Feature Matrix

### 3.1 Token Discovery — 11 Scanner Sources

| Scanner | Service | Auth Required | Schedule | Queue |
|---|---|---|---|---|
| Shodan | Shodan Internet DB | API key | Every 4 h @ :00 | `scanners` |
| FOFA | FOFA Chinese engine | Email + key | Every 4 h @ :00 (+1 h offset) | `scanners` |
| URLScan | URLScan.io | API key | Every 4 h @ :40 | `scanners` |
| GitHub Code | GitHub API v3 | PAT | Every 4 h @ :00 | `scanners` |
| GitHub Gists | GitHub API v3 | PAT | Every 6 h @ :45 | `scanners` |
| GitLab | GitLab API | PAT | Every 6 h @ :10 | `scanners` |
| grep.app | grep.app | None | Every 6 h @ :25 | `scanners` |
| PublicWWW | PublicWWW API | API key | Every 6 h @ :05 (+1 h offset) | `scanners` |
| Pastebin | Pastebin (scraped) | None | Every 12 h @ :15 | `scanners` |
| Serper | Serper (Google SERPs) | API key | Every 12 h @ :35 | `scanners` |
| Bitbucket | Bitbucket API | User + app password | Hourly @ :55 | `scanners` |

All scanners degrade gracefully when API keys are absent. Missing-key scanners are silently skipped.

### 3.2 Token Validation

- **Regex pattern:** `\b(\d{8,10}:[A-Za-z0-9_-]{35})\b`
- **Strict rejection rules:**
  - Fernet ciphertexts (secret starts with `gAAAA`)
  - Pure hexadecimal strings (likely hash collisions)
  - Bot ID with leading zeros
  - Secret not starting with `AA` (Telegram format requirement)
- **Liveness check:** HTTP `GET /getMe` against `api.telegram.org`
- **Deduplication:** SHA-256 hash of plaintext token; `token_hash` column is UNIQUE

### 3.3 Credential Persistence & Encryption

- **Algorithm:** Fernet (AES-128-CBC + HMAC-SHA256)
- **Key length:** 44-character URL-safe base64 (validated at settings load)
- **Storage:** `bot_token` column always contains Fernet ciphertext
- **Self-healing:** legacy plaintext tokens encountered during enrichment/exfil are automatically re-encrypted in place

### 3.4 Chat Enumeration (Enrichment)

- Telethon `get_dialogs()` retrieves all chats accessible to the bot
- Creates a Telegram forum topic in the monitor supergroup per credential
- Stores `topic_id`, `all_chats`, `bot_id`, `bot_username` in the `meta` JSONB column
- Credential status advances from `pending` → `active`

### 3.5 Message Exfiltration — 4 Strategies

| Strategy | Method | When Used |
|---|---|---|
| 1 — Direct history | Telethon `get_history()` | Primary; most complete |
| 2 — ID bruteforce | Backward scan from anchor message ID | When strategy 1 is restricted |
| 3 — Bot API updates | `getUpdates` (recent messages only) | Fallback; limited backlog |
| 4 — Blind forwarding | Auto-invite bot; forward from target chat | Last resort; invasive |

All strategies upsert into `exfiltrated_messages` with a unique constraint on `(credential_id, telegram_msg_id)`.

### 3.6 Message Broadcasting

- **Distributed lock:** Redis SET NX (55 s TTL) prevents concurrent broadcasters
- **Atomic DB claim:** `broadcast_claimed_at` conditional UPDATE (only one worker wins)
- **Stale claim reclamation:** claims older than 5 minutes become eligible for re-claim
- **Rate limiting:** 2-second sleep between messages (Telegram flood control)
- **Multi-bot rotation:** round-robin across comma-separated `MONITOR_BOT_TOKEN` list
- **Guarantee:** exactly-once broadcast on single-machine deployments; at-least-once on multi-machine with shared DB

### 3.7 Scheduled Maintenance Tasks

| Task | Cadence | Purpose |
|---|---|---|
| `flow.broadcast_pending` | Hourly (configurable) | Post unbroadcasted messages to Telegram |
| `flow.rescrape_active` | Hourly (configurable) | Re-pull message history from active chats |
| `flow.system_heartbeat` | Every 30 min | Post liveness ping to monitor group |
| `flow.system_help` | Every 6 h @ :30 | Post command reference to monitor group |
| `scanner.retry_cold` | Every 12 h @ :50 | Retry tokens that previously failed enrichment |
| `audit.audit_active_topics` | Hourly @ :15 | Verify topic exists in Telegram; clear stale `topic_id` |
| `system.self_heal` | Every 6 h @ :45 | Reconcile DB credentials vs Telegram topics |
| `system.enforce_whitelist` | Every 6 h @ :00 (+1 h offset) | Keep whitelisted bots present in monitor group |
| `system.cleanup_general_topic` | Hourly @ :30 | Remove stray posts from Telegram General topic |

### 3.8 Admin Bot Commands

Commands are accepted only from whitelisted admins or the anonymous group admin.

| Command | Effect |
|---|---|
| `/start` | Greet user, confirm availability |
| `/help` | Display command reference |
| `/status` | Show health, pending credential/message counts |
| `/pause` | Pause scanners and broadcaster |
| `/resume` | Resume all operations |
| `/restart` | Restart bot listener service |
| `/starthunter` | Interactive Telegram account login flow |
| `/bots` | Display bot pool status and lock state |

### 3.9 HTTP Monitoring API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/` | None | Liveness / version |
| GET | `/health/` | None | Basic health check (always 200) |
| GET | `/health/detailed` | `X-Monitor-Key` | DB, Redis, Telegram reachability |
| GET | `/health/metrics` | `X-Monitor-Key` | In-memory metric counters |
| GET | `/health/circuit-breakers` | `X-Monitor-Key` | Circuit breaker states |
| POST | `/health/circuit-breakers/{service}/reset` | `X-Monitor-Key` | Force-reset a breaker |
| GET | `/monitor/stats` | `X-Monitor-Key` | Aggregate credential/message counts |
| GET | `/monitor/credentials?limit=N` | `X-Monitor-Key` | Recent credentials (N ≤ 100) |
| GET | `/monitor/messages?limit=N` | `X-Monitor-Key` | Recent exfiltrated messages (N ≤ 100) |
| POST | `/scan/trigger` | None (dev only) | Manually enqueue scanner task; **403 in production** |

OpenAPI docs (`/docs`, `/redoc`) are disabled in production (`ENV=production`).

---

## 4. Data Architecture

### 4.1 Database Schema

**`discovered_credentials`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK | `gen_random_uuid()` |
| `bot_token` | TEXT | NOT NULL | Fernet-encrypted ciphertext |
| `token_hash` | TEXT | UNIQUE | SHA-256 of plaintext token |
| `chat_id` | BIGINT | — | Primary chat from enrichment |
| `bot_id` | TEXT | — | Numeric Telegram bot ID |
| `bot_username` | TEXT | — | `@username` |
| `chat_name` | TEXT | — | Display name of primary chat |
| `chat_type` | TEXT | — | `group`, `supergroup`, `channel`, `private` |
| `source` | TEXT | — | Scanner name (shodan, github, fofa, …) |
| `status` | TEXT | CHECK | `pending`, `active`, `revoked` |
| `meta` | JSONB | — | `topic_id`, `all_chats`, `bot_id`, etc. |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | — |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | — |

Indexes: `status`, `bot_id`

**`exfiltrated_messages`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK | `gen_random_uuid()` |
| `credential_id` | UUID | FK → `discovered_credentials.id` ON DELETE CASCADE | — |
| `telegram_msg_id` | INT | NOT NULL | Telegram-assigned message ID |
| `sender_name` | TEXT | — | Message author display name |
| `content` | TEXT | — | Message body |
| `media_type` | TEXT | DEFAULT `text` | `text`, `photo`, `document`, `audio`, `video` |
| `file_meta` | JSONB | — | `mime`, `size`, `thumb_id`, etc. |
| `is_broadcasted` | BOOLEAN | DEFAULT FALSE | Set TRUE after successful broadcast |
| `broadcast_claimed_at` | TIMESTAMPTZ | — | Distributed claim timestamp |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | — |

Unique constraint: `(credential_id, telegram_msg_id)`
Indexes: `credential_id`; partial on `is_broadcasted = FALSE`; composite on `(is_broadcasted, broadcast_claimed_at)`

**`telegram_accounts`**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK | `gen_random_uuid()` |
| `phone` | TEXT | UNIQUE | Telegram account phone number |
| `session_path` | TEXT | NOT NULL | Path to `.session` file |
| `status` | TEXT | CHECK `active`, `inactive` | — |
| `locked_by` | TEXT | — | Instance ID of current holder |
| `locked_until` | TIMESTAMPTZ | — | Session lease expiry |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | — |
| `updated_at` | TIMESTAMPTZ | DEFAULT NOW() | — |

Indexes: `phone`, `status`

### 4.2 Views

**`discovered_credentials_public`** — safe projection for anonymous (frontend) reads. Exposes only `id`, `created_at`, `source`, `status`, `meta`. Hides encrypted token, hash, bot ID/username, and chat details.

### 4.3 Row Level Security

| Table | anon SELECT | anon INSERT/UPDATE/DELETE | Notes |
|---|---|---|---|
| `discovered_credentials` | Denied | Allowed | Backend uses service-role key (bypasses RLS) |
| `discovered_credentials_public` | Allowed | — | Safe view only |
| `exfiltrated_messages` | Allowed | Allowed | No sensitive data in this table |
| `telegram_accounts` | Denied | Denied | Internal-only |

---

## 5. Security Architecture

### 5.1 Credential Encryption

- **Algorithm:** Fernet (AES-128-CBC + HMAC-SHA256)
- **Key generation:** `Fernet.generate_key()` → 44-character URL-safe base64 string
- **Validation:** key length strictly enforced at application startup via Pydantic settings validator
- **Tokens are never logged** and never stored in plaintext

### 5.2 Database Access Tiers

| Tier | Key Used | Access Level |
|---|---|---|
| Backend (all workers, API) | `SUPABASE_SERVICE_ROLE_KEY` | Full bypass of RLS |
| Frontend (Next.js) | `SUPABASE_KEY` (anon key) | RLS-restricted, public view only |

### 5.3 API Authentication

- `MONITOR_API_KEY` environment variable enables header authentication
- Protected endpoints require `X-Monitor-Key: <value>` header
- If `MONITOR_API_KEY` is unset, protected endpoints are openly accessible (development behavior)

### 5.4 Token Validation Security

Strict validation prevents false positives and injection:
- Rejects Fernet ciphertexts to prevent double-encryption detection
- Rejects pure hex strings (common hash format)
- Verifies liveness against Telegram API before any storage

### 5.5 Dependency Security Posture

| Package | Version | Addressed CVE(s) |
|---|---|---|
| httpx | 0.28.1 | CVE-2024-37891 (SSRF) |
| cryptography | 44.0.0 | CVE-2024-26130, CVE-2024-0727 |
| requests | 2.32.3 | CVE-2024-35195 (cert validation) |
| Telethon | 1.38.1 | Latest stable |

---

## 6. Reliability & Resilience

### 6.1 Retry Strategy

**`@retry` decorator** (sync and async variants, `app/core/retry.py`)
- Exponential backoff: `delay = base × 2^attempt`, capped at `max_delay`
- Configurable exception types per call site

**Telegram-specific (`@retry_on_telegram_error`)**
- Catches: `RetryAfter`, `TimedOut`, `NetworkError`
- 3 attempts, 2–30 s backoff

**Connection-specific (`@retry_on_connection_error`)**
- Catches: `ConnectionError`, `TimeoutError`, `httpx.ConnectError`
- 3 attempts, 1–10 s backoff

### 6.2 Circuit Breaker Pattern

Per-service breakers for Shodan, URLScan, GitHub, FOFA:

| Parameter | Value |
|---|---|
| Failure threshold | 5 consecutive failures → OPEN |
| Recovery timeout | 60 s → HALF_OPEN |
| Success threshold | 2 consecutive successes → CLOSED |

States exposed at `/health/circuit-breakers`.

### 6.3 Distributed Locking (Redis)

| Lock | Key | TTL | Purpose |
|---|---|---|---|
| Broadcast | `telegram_hunter:lock:broadcast` | 55 s | Single broadcaster at a time |
| Bot poller | `bot_listener:poll_lock:{bot_id}` | 120 s | Single `getUpdates` poller per bot |
| User session | `user_agent:{session_name}` | 600 s | Exclusive Telethon session use |

### 6.4 Task Reliability

| Setting | Value | Effect |
|---|---|---|
| `task_acks_late` | True | Worker acks only after task completion (at-least-once) |
| `worker_prefetch_multiplier` | 1 | Fetch one task at a time (no batching) |
| `worker_max_memory_per_child` | 800 MB | Auto-recycle child process on memory threshold |
| `exfiltrate_chat` soft limit | 2400 s | Graceful shutdown with partial results |
| `exfiltrate_chat` hard limit | 2500 s | Forced kill (last resort) |
| Default task soft limit | 1200 s | — |
| Default task hard limit | 1300 s | — |

### 6.5 Self-Healing

- **`system.self_heal` (every 6 h):** reconciles DB credentials against live Telegram topic state; re-creates missing topics; triggers catch-up broadcast
- **`audit.audit_active_topics` (hourly):** sends silent `send_chat_action` probe; detects and clears deleted `topic_id` from meta; triggers re-enrichment
- **Inline token self-healing:** any plaintext token encountered during enrichment or exfiltration is automatically Fernet-encrypted in place

---

## 7. Observability

### 7.1 Logging

**Development format:**
```
2026-04-19 14:23:45 | INFO | scanner.tasks | [Shodan] Processing 250 matches...
```

**Production format (JSON):**
```json
{"time":"2026-04-19 14:23:45","level":"INFO","name":"scanner.tasks","msg":"[Shodan] Processing 250 matches..."}
```

Logging is force-initialized across uvicorn and Celery via `logging.basicConfig(..., force=True)`.

### 7.2 Metrics

**`MetricsCollector`** (`app/core/metrics.py`):
- `@metrics.track("name")` — wraps a function; records success/failure counts and duration
- `metrics.inc("counter")` — increment a named counter
- `metrics.get_all_metrics()` — full dict of tracked operations
- `metrics.get_summary()` — aggregate success rate across all tracked operations

Exposed at: `GET /health/metrics` (protected)

### 7.3 Audit Logging

**`AuditLogger`** (`app/core/audit.py`) records security-relevant events: `TOKEN_DECRYPTED`, `TOKEN_VALIDATED`, `CREDENTIAL_CREATED`, `BROADCAST_SENT`, `CREDENTIAL_REVOKED`. High-importance events are marked for DB persistence (table schema defined; DB write currently placeholder).

### 7.4 Circuit Breaker Visibility

`GET /health/circuit-breakers` returns per-service state: `name`, `state` (CLOSED/OPEN/HALF_OPEN), `failure_count`, `success_count`, `last_failure_time`.

---

## 8. Pydantic Data Models (API Contract)

```python
class CredentialOut(BaseModel):
    id: UUID
    source: str
    status: str
    chat_id: Optional[int]
    meta: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

class MessageOut(BaseModel):
    id: UUID
    credential_id: UUID
    telegram_msg_id: int
    sender_name: Optional[str]
    content: Optional[str]
    media_type: str
    is_broadcasted: bool
    created_at: datetime

class StatsOut(BaseModel):
    credentials_total: int
    credentials_active: int
    messages_exfiltrated: int
    messages_broadcasted: int

class ScanRequest(BaseModel):
    source: str  # shodan | fofa | github | censys | hybrid
    query: str
```

---

## 9. Non-Functional Requirements (Observed)

| Requirement | Implementation |
|---|---|
| **Availability** | Celery workers auto-restart on crash (Docker `restart: unless-stopped`) |
| **Data integrity** | Unique DB constraints on `token_hash` and `(credential_id, telegram_msg_id)` |
| **Idempotency** | All tasks safe to re-run; upsert semantics throughout |
| **Rate compliance** | 2 s inter-message sleep; circuit breakers for external APIs |
| **Secret protection** | Tokens never appear in logs; always encrypted at rest |
| **Scalability limit** | Single Redis instance; no Kubernetes support; horizontal scaling not tested |
| **TLS** | Not terminated internally; assumes upstream reverse proxy |
| **Schema migrations** | No migration framework; schema changes via re-running `database/init.sql` (idempotent DDL) |

---

## 10. Known Limitations

1. **No Alembic / migration framework** — schema changes require manual DDL re-application.
2. **Single Redis instance** — no high-availability or persistence tuning.
3. **No built-in TLS** — requires external reverse proxy (nginx, Caddy, etc.).
4. **No rate limiting on API endpoints** beyond optional API-key header.
5. **No alerting framework** — observability is log/metric based only; external aggregation required.
6. **No Kubernetes support** — Docker Compose only.
7. **CSV import pipeline incomplete** — `imports/` directory is prepared but processing logic is not implemented.
8. **Audit DB persistence placeholder** — `AuditLogger` marks events for DB write, but the backing table is not created in `init.sql`.
