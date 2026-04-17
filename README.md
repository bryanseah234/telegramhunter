# Telegram Hunter

**Telegram Hunter** is an automated, self-hosted OSINT system for discovering, validating, and monitoring exposed Telegram bot tokens. Built as a production-ready microservices architecture using **FastAPI**, **Celery**, **Redis**, and **Supabase**.

## 🚀 Features

### Core Functionality

- **Multi-Source Scanning**: GitHub, Shodan, URLScan, FOFA with country filtering
- **Token Enrichment**: Auto-discovers chats linked to each token
- **Deep Scraping**: Logs in as the bot (via Telethon) and scrapes chat history
- **Real-time Alerts**: Broadcasts findings to your private Telegram group
- **Encryption**: All tokens encrypted at rest (Fernet)
- **Frontend Dashboard**: Telegram-style UI to browse discovered data

### Production-Ready Enhancements ✨

- **Structured Logging**: Context-aware logging with JSON format for production
- **Retry Logic**: Exponential backoff for API calls and database operations
- **Circuit Breakers**: Automatic protection against cascading failures
- **Metrics Collection**: Track performance, success rates, execution times
- **Health Checks**: `/health/` endpoints for monitoring and observability
- **Audit Logging**: Security event tracking for compliance
- **Code Quality**: Automated linting (Ruff), type checking (MyPy), pre-commit hooks

## 🛠 Tech Stack

| Component | Technology |
|-----------|------------|
| API | FastAPI |
| Workers | Celery + Redis |
| Database | Supabase (PostgreSQL) |
| Scraping | Telethon (MTProto) |
| Frontend | Next.js + Tailwind CSS |
| Monitoring | Health checks, Metrics, Circuit breakers |
| Quality | Ruff, MyPy, Pytest, Pre-commit |
| Deployment | Docker Compose |

## 📋 Prerequisites

1. **Docker & Docker Compose** installed
2. **Supabase Project** (run `database/init.sql` in SQL Editor)
3. **Telegram API Keys** from [my.telegram.org](https://my.telegram.org)
4. **Monitoring Bot Token** from [@BotFather](https://t.me/BotFather)
5. **API Keys** (optional): Shodan, URLScan, GitHub, FOFA

## 🐳 Docker Deployment

The recommended way to run Telegram Hunter is using Docker Compose. This works on **Windows (WSL2)**, **Mac**, and **Linux**.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- **Windows Users**: Ensure [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) is enabled and integrated with Docker Desktop

### 1. Clone & Configure

```bash
git clone https://github.com/bryanseah234/telegramhunter.git
cd telegramhunter
cp .env.example .env
# Edit .env and add your keys (Supabase, Telegram, etc.)
```

**Generate Encryption Key:**

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. Initialize Database

Run `database/init.sql` in your Supabase SQL Editor.

### 3. Run with Docker Compose

Use the launcher scripts — they automatically find free ports if the defaults (`API: 8011`, `Redis: 6379`) are taken.

**Linux / Mac:**
```bash
chmod +x start.sh
./start.sh -d --build
```

**Windows:**
```bat
start --build
```

The launcher auto-detects port conflicts and picks the next free port — no manual config needed.

You can also pin specific ports in `.env`:
```env
API_PORT=9000
REDIS_PORT=6380
```

### 4. Verify Running Services

```bash
docker compose ps
```

You should see 7 services running:

| Service | Description | Port |
|---------|-------------|------|
| `api` | FastAPI backend | 8011 (auto) |
| `worker-core` | Celery core worker (4 concurrent) | - |
| `worker-scanners` | Celery scanner worker (2 concurrent) | - |
| `worker-scrape` | Celery scrape worker (2 concurrent) | - |
| `beat` | Celery scheduler | - |
| `bot` | Telegram bot listener | - |
| `redis` | Message broker | 6379 (auto) |

### 5. Access the Application

- **API Dashboard**: <http://localhost:8011/docs>
- **Health Check**: <http://localhost:8011/health/detailed>
- **Metrics**: <http://localhost:8011/health/metrics>

### 6. Common Commands

| Command | Linux/Mac | Windows |
|---------|-----------|---------|
| Start | `./start.sh` | `start` |
| Start + rebuild | `./start.sh --build` | `start --build` |
| Stop | `./start.sh stop` | `start stop` |
| Restart | `./start.sh restart` | `start restart` |
| View logs | `./start.sh logs` | `start logs` |
| Status | `./start.sh status` | `start status` |
| Pull + rebuild | `./start.sh update` | `start update` |
| Wipe & reset | `./start.sh reset` | `start reset` |

## 🔑 Free OSINT API Key Setup Guide

You can significantly increase your hit rate by adding free API keys from various intelligence search engines to your `.env` file.

**1. Serper.dev (Replaces Google Search)**
*Highly recommended for automated web search and paste site dorking.*

- Go to [Serper.dev](https://serper.dev/) and sign up for a free account.
- You get **2,500 free queries** on signup.
- Copy your API Key from the dashboard and add it to `.env` as `SERPER_API_KEY`.

**2. GitLab Search API**
*Finds tokens leaked in raw GitLab repo blobs.*

- Log into GitLab and go to **Edit profile > Access Tokens** (`https://gitlab.com/-/profile/personal_access_tokens`).
- Click "Add New Token", tick `read_api`, copy the token (`GITLAB_TOKEN`).

**3. PublicWWW**
*Source code search engine that finds tokens embedded in raw HTML/JS.*

- Go to [PublicWWW Registration](https://publicwww.com/register.html) and sign up.
- Your API key will be on your Dashboard (`PUBLICWWW_KEY`).

**Note:** The `grep.app` scanner requires **no API keys** and runs completely free.

## 🔄 Updates

```bash
# Linux/Mac
./start.sh update

# Windows
start update
```

This pulls the latest code, rebuilds images, and restarts the stack. Your data in Supabase is untouched.

To schedule automatic updates via cron (Linux/Mac):
```bash
# Check for updates every 6 hours
(crontab -l 2>/dev/null; echo "0 */6 * * * cd $(pwd) && ./start.sh update >> logs/update.log 2>&1") | crontab -
```

### Automatic Token Import

On every container startup, the system automatically imports tokens from CSV files placed in the `imports/` directory. This is useful for:

1. **Manual FOFA scraping** via Chrome extension → export to CSV
2. **Bulk token imports** from other sources

**To use:**

1. Add tokens to `import_tokens.csv` in the project root:

   ```csv
   token,chat_id
   123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx,-1001234567890
   987654321:BBxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx,
   ```

   > Note: `chat_id` is optional. If omitted, the system will try to discover it automatically.

2. Restart containers:

   ```bash
   docker-compose up -d --build
   ```

3. Check logs to verify import:

   ```bash
   docker-compose logs worker | head -50
   ```

## ⚙️ Scan Schedule (Aggressive Mode)

The system runs scans automatically on this schedule (UTC):

| Task | Frequency | Schedule |
|------|-----------|----------|
| **GitHub Scan** | Every 4 hours | 00:00, 04:00, 08:00... |
| **Shodan Scan** | Every 4 hours | 00:20, 04:20, 08:20... |
| **URLScan Scan** | Every 4 hours | 00:40, 04:40, 08:40... |
| **FOFA Scan** | Every 4 hours | 01:00, 05:00, 09:00... |
| **Re-scrape Active** | Every 1 hour | Every hour at :00 |
| **Broadcast** | Every 1 hour | Every hour at :30 |
| **Heartbeat** | Every 30 min | Every :00 and :30 |

### Manual Scan Triggers

```bash
# Trigger GitHub scan
curl http://localhost:8000/scan/trigger-dev/github

# Trigger Shodan scan with country filter
curl "http://localhost:8000/scan/trigger-dev/shodan?country_code=US"

# Trigger with random country from target list
curl "http://localhost:8000/scan/trigger-dev/shodan?country_code=RANDOM"

# Trigger FOFA scan
curl http://localhost:8000/scan/trigger-dev/fofa
```

## 🔒 Security

- **Encrypted Storage**: All tokens encrypted with Fernet
- **RLS Policies**: Row-level security on Supabase tables
- **Audit Logging**: Tracks token decryption, credential access
- **Config Validation**: Startup checks for invalid configuration

## 🖥 Manual Operations

### Import Tokens from CSV

```bash
# Format: token,chat_id (one per line)
python tests/manual_scrape.py -i import_tokens.csv
```

### Check Stats

```bash
curl http://localhost:8000/monitor/stats
```

## 📁 Project Structure

```
telegramhunter/
├── app/
│   ├── api/               # API routes + health endpoints
│   ├── core/              # Config, database, logging, retry, metrics
│   ├── services/          # Scanner, scraper, broadcaster
│   ├── utils/             # Helper utilities
│   └── workers/           # Celery tasks
├── frontend/              # Next.js dashboard
├── database/              # SQL schemas
├── scripts/               # Validation, setup, helpers
├── tests/
│   ├── unit/              # Unit tests
│   └── integration/       # Integration tests
├── chrome_extension/      # Browser extension for token collection
├── docker-compose.yml
├── pyproject.toml         # Ruff, MyPy, Pytest config
├── .pre-commit-config.yaml
└── README.md
```

## ⚙️ Configuration

### Target Countries

Configure in `app/core/config.py`:

```python
TARGET_COUNTRIES = ["RU", "IR", "IN", "ID", "BR", "UA", "VN", "US", "NG", "EG", "KZ", "CN", "DE"]
```

### Worker Settings

Current aggressive configuration in `app/workers/celery_app.py`:

| Setting | Value |
|---------|-------|
| Worker Concurrency | 4 |
| Memory per Worker | 800MB |
| Task Timeout | 20 minutes |
| Broker Connections | 10 |

## 🧪 Development

### Setup Development Environment

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run validation
python scripts/validate_startup.py

# Setup pre-commit hooks
pip install pre-commit
pre-commit install
```

### Code Quality

```bash
# Lint and format
ruff check app/ --fix
ruff format app/

# Type checking
mypy app/

# Run tests
pytest tests/ -v

# Coverage
pytest tests/ --cov=app --cov-report=html

# Pre-commit (runs automatically on commit)
pre-commit run --all-files
```

### Testing

```bash
# Unit tests only
pytest tests/unit/ -v

# Integration tests
pytest tests/integration/ -v -m integration

# Specific test
pytest tests/unit/test_helpers.py::TestTokenValidation -v
```

## 📊 Monitoring

### Metrics Tracked

- Task execution times (min, max, avg)
- Success/failure rates
- External API response times
- Circuit breaker states

### Circuit Breakers

Automatically protect against:

- Shodan API failures
- URLScan API failures
- GitHub API failures
- FOFA API failures

When a service fails repeatedly, the circuit breaker opens and prevents further calls until recovery timeout.

## 🛡 Disclaimer

This tool is for **educational and defensive research purposes only**. Only use on systems you own or have explicit permission to test.

## 📝 License

MIT License - See LICENSE file for details

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Install pre-commit hooks: `pre-commit install`
4. Make your changes
5. Run tests: `pytest tests/ -v`
6. Submit a pull request

---

**Built with ❤️ for security researchers and OSINT enthusiasts**
