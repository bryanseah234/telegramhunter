# OSINT Credential Discovery Pipeline

A self-hosted, continuously-running OSINT pipeline that discovers exposed bot tokens across 13 public data sources, validates them against the live API, harvests accessible chat history, and broadcasts findings to a private Telegram supergroup. Delivered as a Docker Compose stack.

- **Runtime:** Python 3.11, FastAPI, Celery, Redis, Telethon
- **Database:** Supabase (managed PostgreSQL) with Row Level Security
- **Frontend (optional):** Next.js 16 + React 19  read-only dashboard
- **Browser Extension (optional):** Manifest V3 Chrome extension  FOFA scraper
- **Deployment:** Docker Compose (7 services)

---

## Prerequisites

| Tool | Minimum Version | Notes |
|---|---|---|
| Docker Engine | 24.x | Tested on 29.x |
| Docker Compose | v2 (bundled) | Use `docker compose`, not `docker-compose` |
| Python | 3.11+ | Local dev and tests only |
| Node.js | 18+ | Frontend only |
| Supabase project |  | Free tier sufficient |
| Telegram account |  | Required for `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` |

---

## Environment Configuration

Copy `.env.template` to `.env` and fill in every value before starting the stack.

```bash
cp .env.template .env
```

### Required Variables

| Variable | Type | Description |
|---|---|---|
| `SUPABASE_URL` | URL | Supabase project URL (`https://<ref>.supabase.co`) |
| `SUPABASE_KEY` | string | Supabase anon key  used by the frontend and extension |
| `SUPABASE_SERVICE_ROLE_KEY` | string | Supabase service-role key  backend only, never expose to clients |
| `REDIS_URL` | URL | Redis connection string (`redis://redis:6379/0` for Docker) |
| `ENCRYPTION_KEY` | 44 chars | Fernet key  generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `MONITOR_BOT_TOKEN` | string | Comma-separated bot tokens used to post findings (e.g. `123:AAA,456:BBB`) |
| `MONITOR_GROUP_ID` | integer | Supergroup chat ID where findings are posted; bot(s) must be admin |
| `TELEGRAM_API_ID` | integer | From https://my.telegram.org |
| `TELEGRAM_API_HASH` | string | 32-character hex from https://my.telegram.org |

### Optional  Operations

| Variable | Default | Description |
|---|---|---|
| `PROJECT_NAME` | `Telegram Hunter` | FastAPI application title |
| `ENV` | `development` | Set to `production` to disable `/docs` and `/scan/trigger` |
| `DEBUG` | `True` | Log verbosity |
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
| `EXTENSION_WRITE_SECRET` | *(unset)* | Secret for Chrome extension RLS policy. Must also be set in Supabase: `ALTER DATABASE postgres SET app.extension_write_secret = '<value>';` |
| `TARGET_COUNTRIES` | *(built-in 50-country list)* | Optional JSON array of ISO-3166 codes for country-rotation scanning |

### Optional  Scanner API Keys

All scanner keys degrade gracefully when absent  the corresponding scanner is silently skipped.

| Variable | Scanner |
|---|---|
| `SHODAN_KEY` | Shodan |
| `FOFA_EMAIL` + `FOFA_KEY` | FOFA |
| `URLSCAN_KEY` | URLScan.io |
| `GITHUB_TOKEN` | GitHub Code Search + Gists |
| `GITLAB_TOKEN` | GitLab |
| `BITBUCKET_USER` + `BITBUCKET_API_TOKEN` | Bitbucket (Bearer auth) |
| `PUBLICWWW_KEY` | PublicWWW |
| `SERPER_API_KEY` | Serper (Google SERPs) |
| `GOOGLE_SEARCH_KEY` + `GOOGLE_CSE_ID` | Google Custom Search |
| `NETLAS_API_KEY_1` | Netlas account 1 (50 req/day) |
| `NETLAS_API_KEY_2` | Netlas account 2 (100 req/day) |

---

## Database Setup

Apply the schema to your Supabase project before starting the stack.

1. Open the Supabase SQL editor for your project.
2. Run `database/init.sql`  creates all tables, indexes, views, and the `audit_logs` table.
3. Run `database/rls_policies.sql`  applies Row Level Security policies.
4. If using the Chrome extension with direct Supabase writes, set the write secret:
   ```sql
   ALTER DATABASE postgres SET app.extension_write_secret = 'your-secret-value';
   SELECT pg_reload_conf();
   ```

Both SQL files are idempotent and safe to re-run.

---

## Installation & Setup

### 1. Clone the repository

```bash
git clone <repository-url>
cd <repository-directory>
```

### 2. Configure environment

```bash
cp .env.template .env
# Edit .env  fill in all required variables
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

---

## Usage

### Monitor API

All endpoints return JSON.

**Liveness:**
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

**Detailed health check (DB + Redis + Bot API):**
```bash
curl -H "X-Monitor-Key: <your-key>" http://localhost:8011/health/detailed
```

**Circuit breaker status:**
```bash
curl -H "X-Monitor-Key: <your-key>" http://localhost:8011/health/circuit-breakers
```

**Force-reset a circuit breaker:**
```bash
curl -X POST -H "X-Monitor-Key: <your-key>" http://localhost:8011/health/circuit-breakers/shodan/reset
```

**Manually trigger a scanner** (development only  returns 403 in production):
```bash
curl -X POST http://localhost:8011/scan/trigger \
  -H "Content-Type: application/json" \
  -d '{"source": "shodan", "query": "telegram bot"}'
```

Valid `source` values: `shodan`, `fofa`, `github`, `gitlab`, `urlscan`.

**Ingest credentials from external tooling:**
```bash
curl -X POST http://localhost:8011/ingest/extension/credentials \
  -H "Content-Type: application/json" \
  -d '{"source":"manual","results":[{"token":"123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}]}'
```

### Telegram Admin Commands

Send these commands in the monitor supergroup from a whitelisted admin account:

| Command | Effect |
|---|---|
| `/status` | System health, pending counts, bot pool info |
| `/pause` | Pause scanners and broadcaster |
| `/resume` | Resume all operations |
| `/bots` | Show bot pool status and lock state |
| `/starthunter` | Start interactive Telegram account login |
| `/restart` | Restart the bot listener process |
| `/help` | Full command reference |

### CSV Import

Drop `.csv` files into the `imports/` directory (mounted as a Docker volume). The `system.import_csv` task picks them up every 5 minutes.

Required format:
```csv
token,chat_id
1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx,-1001234567890
9876543210:AAyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy,
```

The `chat_id` column is optional  leave blank if unknown.

### Docker Compose Operations

```bash
# Start all services (build if needed)
docker compose up -d --build

# Tail logs  all services
docker compose logs -f

# Tail logs  specific service
docker compose logs -f worker-scrape

# Stop services (preserve volumes)
docker compose down

# Stop and wipe all volumes (full reset)
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

### Run the full test suite

```bash
pytest
```

**68 tests** across unit, integration, API, security, and Supabase R/W suites.

### Run specific suites

```bash
# Unit tests only (no external dependencies)
pytest tests/unit/

# API tests
pytest tests/test_api.py

# Security tests
pytest tests/test_security.py

# Integration tests (requires live Supabase + Redis)
pytest tests/integration/

# Supabase read/write test (writes a real record)
ALLOW_SUPABASE_WRITE=1 pytest tests/test_supabase_rw.py

# With coverage report
pytest --cov=app --cov-report=html
```

### Test markers

```
@pytest.mark.unit         Unit tests (no external dependencies)
@pytest.mark.integration  Integration tests (may require live services)
@pytest.mark.slow         Long-running tests
```

---

## Development

### Code quality

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
npm run dev
```

Frontend requires two environment variables in `frontend/.env.local`:
```
NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co
NEXT_PUBLIC_SUPABASE_KEY=<anon-key>
```

### Chrome Extension

1. Open Chrome  `chrome://extensions/`
2. Enable **Developer mode**
3. Click **Load unpacked**  select the `extension/` directory
4. Open the extension popup and configure:
   - **Supabase URL** and **Anon Key** (for direct write fallback)
   - **Write Secret** (must match `app.extension_write_secret` in your Supabase DB)
   - **API URL** (recommended  e.g. `http://localhost:8011`) for server-side encryption

---

## Project Structure

```
.
 app/
    api/
       main.py                  FastAPI app, lifespan hooks, CORS
       routers/
           health.py            /health/* endpoints
           monitor.py           /monitor/* endpoints
           scan.py              /scan/trigger (dev only)
           ingest.py            /ingest/extension/credentials
    core/
       config.py                Pydantic Settings, env validation
       database.py              Supabase client singleton
       security.py              Fernet encrypt/decrypt
       redis_srv.py             Locks, cooldowns, counters
       retry.py                 @retry decorator (sync/async)
       circuit_breaker.py       Per-service circuit breakers
       metrics.py               In-memory metrics collector
       audit.py                 Security audit event logger
       constants.py             Application-wide constants
       logger.py                Logger factory
    schemas/
       models.py                Pydantic request/response models
    services/
       scanners.py              ShodanService, FofaService, UrlScanService,
                                  GithubService, GitlabService, SerperService
       scanners_extension.py    GithubGistService, GrepAppService,
                                  PublicWwwService, BitbucketService,
                                  PastebinService, GoogleSearchService,
                                  NetlasService
       scraper_srv.py           Telethon chat history scraper (4 strategies)
       broadcaster_srv.py       Telegram message sender, topic manager
       bot_manager_srv.py       Telethon client pool (BotClientManager)
       bot_listener.py          Admin command handler, watchdog
       user_agent_srv.py        User session manager (multi-session rotation)
    utils/
       helpers.py               Token/chat ID validation & extraction
    workers/
        celery_app.py            Celery config, persistent event loop,
                                   beat schedule (25 tasks)
        tasks/
            flow_tasks.py        enrich, exfiltrate, broadcast, rescrape,
                                   heartbeat, help, broadcaster singleton
            scanner_tasks.py     Per-scanner task runners, _save_credentials_async
            audit_tasks.py       audit_active_topics, self_heal,
                                   enforce_whitelist, cleanup_general_topic
            import_tasks.py      system.import_csv  CSV file pipeline
 database/
    init.sql                     Schema DDL (idempotent, IF NOT EXISTS)
    rls_policies.sql             Row Level Security policies
 extension/                       Manifest V3 Chrome extension
    manifest.json
    background.js                Service worker  scan logic, upload
    content.js                   FOFA page scraper
    ui/                          Popup HTML/JS/CSS
 frontend/                        Next.js 16 dashboard (optional)
 imports/                         Drop CSV files here for auto-import
 tests/
    conftest.py                  Fixtures, env injection
    test_api.py                  API route tests
    test_security.py             Encryption tests
    test_scraper_restriction.py  Scraper caching tests
    test_supabase_rw.py          Live DB read/write test
    unit/                        Isolated unit tests (55 tests)
    integration/                 Integration tests (5 tests)
 scripts/
    validate_deployment.py       Post-deploy health checks
    validate_startup.py          Pre-start environment checks
 .env.template                    Environment variable template
 docker-compose.yml               7-service stack definition
 Dockerfile                       python:3.11-slim-bookworm, non-root user
 docker-entrypoint.sh             Container entrypoint  CSV pre-processing
```

---

## Security Notes

- **`SUPABASE_SERVICE_ROLE_KEY`** bypasses all Row Level Security. Never expose it to browser clients or commit it to version control.
- **`ENCRYPTION_KEY`** is the sole protection for stored tokens. Losing it makes all stored credentials unrecoverable.
- **`MONITOR_API_KEY`** should be set in production to protect monitoring endpoints.
- Set `ENV=production` to disable OpenAPI docs and the manual scan endpoint.
- The stack does not terminate TLS. Place it behind a reverse proxy (nginx, Caddy) for external exposure.
- The `EXTENSION_WRITE_SECRET` is stored only inside the Supabase database  never in source code or environment files.

---

## License

See [LICENSE](LICENSE).
