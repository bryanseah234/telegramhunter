# Telegram Hunter

A self-hosted OSINT pipeline that discovers exposed Telegram bot tokens across public data sources, validates them, harvests accessible chat history, and broadcasts findings to a private Telegram supergroup. Delivered as a Docker Compose stack.

- **Runtime**: Python 3.11, FastAPI, Celery, Redis, Telethon
- **Database**: Supabase (managed Postgres) with Row Level Security
- **Frontend** (optional): Next.js 16 + React 19
- **Deployment**: Docker Compose (7 services)

---

## Prerequisites

| Tool | Minimum Version | Notes |
| --- | --- | --- |
| Docker Engine | 24.x | Tested on 29.x |
| Docker Compose | v2 (bundled) | `docker compose` (not `docker-compose`) |
| Python | 3.11+ | Local dev / tests only |
| Node.js | 18+ | Frontend only |
| Supabase project | — | Free tier sufficient |
| Telegram account | — | Required for `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` |

---

## Environment Configuration

Copy `.env.template` to `.env` and fill in every value before starting the stack.

```bash
cp .env.template .env
```

### Required Variables

| Variable | Type | Description |
| --- | --- | --- |
| `SUPABASE_URL` | URL | Supabase project URL (`https://<ref>.supabase.co`) |
| `SUPABASE_KEY` | string | Supabase anon (public) key — used by the frontend |
| `SUPABASE_SERVICE_ROLE_KEY` | string | Supabase service-role key — backend only, never expose to clients |
| `REDIS_URL` | URL | Redis connection string (`redis://redis:6379/0` for Docker) |
| `ENCRYPTION_KEY` | 44 chars | Fernet key — generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `MONITOR_BOT_TOKEN` | string | Comma-separated Telegram bot tokens used to post findings (e.g. `123:AAA,456:BBB`) |
| `MONITOR_GROUP_ID` | integer | Telegram supergroup chat ID where findings are posted; bot(s) must be admin |
| `TELEGRAM_API_ID` | integer | From [my.telegram.org](https://my.telegram.org) |
| `TELEGRAM_API_HASH` | string | 32-character hex from [my.telegram.org](https://my.telegram.org) |

### Optional — Operations

| Variable | Default | Description |
| --- | --- | --- |
| `PROJECT_NAME` | `Telegram Hunter` | FastAPI application title |
| `ENV` | `development` | Set to `production` to enable hardening (disables `/docs`, `/scan/trigger`) |
| `DEBUG` | `True` | Log verbosity; set `False` in production |
| `MONITOR_API_KEY` | *(unset)* | If set, all `/monitor/*` and `/health/detailed` endpoints require `X-Monitor-Key` header |
| `WHITELISTED_BOT_IDS` | `""` | Comma-separated bot usernames or IDs kept in the monitor group |
| `ANONYMOUS_ADMIN_ID` | `1087968824` | Telegram anonymous group admin bot ID |
| `USER_SESSION_STRING` | *(unset)* | Telethon session string for user-agent invite flow |
| `BROADCAST_INTERVAL_MINUTES` | `60` | How often pending messages are broadcast |
| `RESCRAPE_INTERVAL_HOURS` | `1` | How often active chats are re-scraped |
| `SCAN_INTERVAL_HOURS` | `4` | Primary scanner cadence |
| `AUDIT_INTERVAL_HOURS` | `2` | Topic-integrity audit cadence |
| `API_PORT` | `8011` | Host-side port for the API service |
| `REDIS_PORT` | `6379` | Host-side port for Redis |
| `COMPOSE_PROJECT_NAME` | `telegramhunter` | Docker Compose namespace |

### Optional — Scanner API Keys

All scanner keys degrade gracefully when absent — the corresponding scanner is silently skipped.

| Variable | Scanner |
| --- | --- |
| `SHODAN_KEY` | Shodan |
| `FOFA_EMAIL` + `FOFA_KEY` | FOFA |
| `URLSCAN_KEY` | URLScan.io |
| `GITHUB_TOKEN` | GitHub Code Search + Gists |
| `GITLAB_TOKEN` | GitLab |
| `BITBUCKET_USER` + `BITBUCKET_APP_PASSWORD` | Bitbucket |
| `PUBLICWWW_KEY` | PublicWWW |
| `SERPER_API_KEY` | Serper (Google SERPs) |
| `CENSYS_ID` + `CENSYS_SECRET` | Censys |
| `HYBRID_ANALYSIS_KEY` | Hybrid Analysis |
| `GOOGLE_SEARCH_KEY` + `GOOGLE_CSE_ID` | Google Custom Search |

---

## Database Setup

Apply the schema to your Supabase project:

1. Open the Supabase SQL editor for your project.
2. Run `database/init.sql` — creates all tables, indexes, and the `discovered_credentials_public` view.
3. Run `database/rls_policies.sql` — applies Row Level Security policies.

Both files are idempotent and safe to re-run.

---

## Installation & Setup

### 1. Clone the repository

```bash
git clone <repository-url>
cd telegramhunter
```

### 2. Configure environment

```bash
cp .env.template .env
# Edit .env — fill in all required variables
```

### 3. Generate an encryption key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Paste the output as ENCRYPTION_KEY in .env
```

### 4. Apply database schema

See [Database Setup](#database-setup) above.

### 5. Start the stack

```bash
docker compose up -d --build
```

This starts 7 services: `redis`, `api`, `worker-core`, `worker-scanners`, `worker-scrape`, `beat`, `bot`.

### 6. Verify startup

```bash
curl http://localhost:8011/
curl http://localhost:8011/health/
```

Both should return HTTP 200.

---

## Interactive Launcher (Alternative)

**Linux / macOS:**

```bash
./start.sh
```

**Windows:**

```bat
start.bat
```

The launcher auto-selects free ports and provides a menu for common operations.

---

## Usage

### Monitor API

All endpoints return JSON.

**Liveness check:**

```bash
curl http://localhost:8011/health/
```

**System statistics** (requires `X-Monitor-Key` if `MONITOR_API_KEY` is set):

```bash
curl -H "X-Monitor-Key: <your-key>" http://localhost:8011/monitor/stats
```

**Recent credentials:**

```bash
curl -H "X-Monitor-Key: <your-key>" "http://localhost:8011/monitor/credentials?limit=10"
```

**Recent exfiltrated messages:**

```bash
curl -H "X-Monitor-Key: <your-key>" "http://localhost:8011/monitor/messages?limit=20"
```

**Circuit breaker status:**

```bash
curl -H "X-Monitor-Key: <your-key>" http://localhost:8011/health/circuit-breakers
```

**Force-reset a circuit breaker:**

```bash
curl -X POST -H "X-Monitor-Key: <your-key>" http://localhost:8011/health/circuit-breakers/shodan/reset
```

**Manually trigger a scanner** (development only; returns 403 in production):

```bash
curl -X POST http://localhost:8011/scan/trigger \
  -H "Content-Type: application/json" \
  -d '{"source": "shodan", "query": "telegram bot"}'
```

Valid `source` values: `shodan`, `fofa`, `github`, `censys`, `hybrid`.

### Telegram Admin Commands

Send these commands in the monitor supergroup from a whitelisted admin account:

| Command | Effect |
| --- | --- |
| `/status` | System health and pending counts |
| `/pause` | Pause scanners and broadcaster |
| `/resume` | Resume all operations |
| `/bots` | Show bot pool status and lock state |
| `/starthunter` | Start interactive Telegram account login |
| `/help` | Full command reference |

### Docker Compose Operations

```bash
# Start all services (build if needed)
docker compose up -d --build

# Tail logs — all services
docker compose logs -f

# Tail logs — specific service
docker compose logs -f worker-scrape

# Stop services (preserve volumes)
docker compose down

# Stop and wipe all volumes (full reset — Redis, sessions)
docker compose down -v

# Rebuild after code changes
docker compose build && docker compose up -d
```

---

## Testing

### Install test dependencies

```bash
pip install -r requirements-dev.txt
```

### Run the test suite

```bash
# All tests (68 total)
pytest

# Unit tests only
pytest tests/unit/

# Filter by keyword
pytest -k encryption

# With coverage report
pytest --cov=app --cov-report=html
```

### Integration tests (real database writes)

Integration tests that write to Supabase are gated behind an environment variable:

```bash
ALLOW_SUPABASE_WRITE=1 pytest tests/test_supabase_rw.py
```

### Test markers

```text
@pytest.mark.unit         Unit tests (no external dependencies)
@pytest.mark.integration  Integration tests (may require live services)
@pytest.mark.slow         Long-running tests
```

---

## Development

### Code quality tools

```bash
# Lint and auto-fix
ruff check app/ --fix

# Format
ruff format app/

# Type check
mypy app/
```

### Pre-commit hooks

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

### Run the API locally (no Docker)

```bash
export $(grep -v '^#' .env | xargs)
uvicorn app.api.main:app --reload --port 8001
```

### Run the frontend locally

```bash
cd frontend
npm install
npm run dev        # Development server
npm run build      # Production build
```

---

## Project Structure

```text
telegramhunter/
├── app/
│   ├── api/
│   │   ├── main.py                  FastAPI app, lifespan hooks
│   │   └── routers/
│   │       ├── health.py            /health/*, /circuit-breakers
│   │       ├── monitor.py           /monitor/*
│   │       └── scan.py              /scan/trigger (dev only)
│   ├── core/
│   │   ├── config.py                Pydantic Settings, env validation
│   │   ├── database.py              Supabase client singleton
│   │   ├── security.py              Fernet encrypt/decrypt
│   │   ├── redis_srv.py             Locks, cooldowns, counters
│   │   ├── retry.py                 @retry decorator (sync/async)
│   │   ├── circuit_breaker.py       Per-service circuit breakers
│   │   ├── metrics.py               In-memory metrics collector
│   │   └── audit.py                 Security audit event logger
│   ├── schemas/
│   │   └── models.py                Pydantic request/response models
│   ├── services/
│   │   ├── scanners.py              6 primary scanner classes
│   │   ├── scanners_extension.py    6 additional scanner classes
│   │   ├── scraper_srv.py           Telethon chat history scraper
│   │   ├── broadcaster_srv.py       Telegram message sender, topic manager
│   │   ├── bot_manager_srv.py       Telethon client pool
│   │   ├── bot_listener.py          Admin command handler
│   │   └── user_agent_srv.py        User session manager
│   ├── utils/
│   │   └── helpers.py               Token/chat ID validation & extraction
│   └── workers/
│       ├── celery_app.py            Celery config, beat schedule (20 tasks)
│       └── tasks/
│           ├── flow_tasks.py        enrich, exfiltrate, broadcast, rescrape
│           ├── scanner_tasks.py     Per-scanner task runners
│           └── audit_tasks.py       audit_*, self_heal, enforce_whitelist
├── database/
│   ├── init.sql                     Schema DDL (idempotent)
│   └── rls_policies.sql             Row Level Security policies
├── frontend/                        Next.js 16 + React 19 (optional)
├── tests/
│   ├── conftest.py                  Fixtures, env injection
│   ├── unit/                        Isolated unit tests
│   └── integration/                 Integration tests
├── scripts/
│   ├── validate_deployment.py       Post-deploy health checks
│   └── validate_startup.py          Pre-start environment checks
├── .env.template                    Environment variable template
├── docker-compose.yml               7-service stack definition
├── Dockerfile                       Python 3.11-slim build spec
├── docker-entrypoint.sh             Container entrypoint
├── pyproject.toml                   Ruff, MyPy, Pytest configuration
├── requirements.txt                 Production dependencies
└── requirements-dev.txt             Test and lint dependencies
```

---

## Architecture Overview

The pipeline operates across five sequential phases, each handled by dedicated Celery workers:

1. **Discovery** — 11 scanners query public sources on staggered schedules; extracted token strings are format-validated and tested against the Telegram Bot API.
2. **Persistence** — Live tokens are Fernet-encrypted and inserted into `discovered_credentials` with status `pending`.
3. **Enrichment** — A Telethon client enumerates all chats accessible to the bot; a forum topic is created in the monitor supergroup; status advances to `active`.
4. **Exfiltration** — Chat history is scraped using up to four fallback strategies and upserted into `exfiltrated_messages`.
5. **Broadcasting** — Pending messages are claimed atomically and posted to their credential's forum topic at a rate-limited 2 s/message cadence.

Self-healing tasks run hourly and every 6 hours to reconcile database state against live Telegram state, re-create missing topics, and retry failed enrichments.

---

## Security Notes

- **`SUPABASE_SERVICE_ROLE_KEY`** bypasses all Row Level Security. Never expose it to browser clients or commit it to version control.
- **`ENCRYPTION_KEY`** is the sole protection for stored bot tokens. Losing it makes all stored credentials unrecoverable.
- **`MONITOR_API_KEY`** should be set in production to protect monitoring endpoints.
- Set `ENV=production` in production deployments to disable OpenAPI docs and the manual scan endpoint.
- The stack does not terminate TLS. Place it behind a reverse proxy (nginx, Caddy) for external exposure.

---

## License

See [LICENSE](LICENSE).
