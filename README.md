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

This command starts the **API**, **Worker**, **Scheduler**, and **Redis** in the background.

```bash
docker-compose up -d --build
```

### 4. Verify Running Services

```bash
docker-compose ps
```

You should see 4 services running:

| Service | Description | Port |
|---------|-------------|------|
| `api` | FastAPI backend | 8000 |
| `worker` | Celery worker (4 concurrent) | - |
| `beat` | Celery scheduler | - |
| `redis` | Message broker | 6379 |

### 5. Access the Application

- **API Dashboard**: <http://localhost:8000/docs>
- **Health Check**: <http://localhost:8000/health/detailed>
- **Metrics**: <http://localhost:8000/health/metrics>

### 6. View Logs

```bash
# Worker logs (shows scanning activity)
docker-compose logs -f worker

# All services
docker-compose logs -f

# Stop everything
docker-compose down
```

## 🔄 Auto-Update System

Telegram Hunter includes an **automatic update system** that checks for new releases and updates your deployment automatically. Once configured, it's fully self-healing!

### 🚀 One-Time Setup (5 minutes)

After cloning the repo, run these commands **once** to enable automatic updates:

**Linux/Mac:**

```bash
# 1. Make the update script executable
chmod +x scripts/auto-update.sh

# 2. Test it works
./scripts/auto-update.sh --check-only

# 3. Add to crontab (runs every 6 hours)
(crontab -l 2>/dev/null; echo "0 */6 * * * cd $(pwd) && ./scripts/auto-update.sh >> logs/auto-update.log 2>&1") | crontab -

# Verify cron was added
crontab -l
```

**Windows (Task Scheduler via WSL):**

```bash
# In WSL, add to crontab same as Linux
chmod +x scripts/auto-update.sh
(crontab -l 2>/dev/null; echo "0 */6 * * * cd $(pwd) && ./scripts/auto-update.sh >> logs/auto-update.log 2>&1") | crontab -
```

> **That's it!** After this one-time setup, your deployment will automatically:
> - Check for updates every 6 hours
> - Pull new code when available
> - Rebuild and restart Docker containers
> - Alert you if new `.env` variables are needed
> - Log all activity to `logs/auto-update.log`

### ✅ What Happens Automatically

| Event | Action |
|-------|--------|
| New release pushed | Auto-detected within 6 hours |
| Code changes | Pulled and containers rebuilt |
| New dependencies | Installed during Docker rebuild |
| New `.env` variables | Logged as warning (manual action needed) |
| Containers healthy | Verified after restart |

### 🛠 Manual Update Commands

You can also trigger updates manually:

```bash
# Check if update is available (no changes made)
./scripts/auto-update.sh --check-only

# Run update now
./scripts/auto-update.sh

# Force rebuild even if already up-to-date
./scripts/auto-update.sh --force
```

### 📋 View Update History

```bash
# View recent update logs
tail -100 logs/auto-update.log

# Check current version
git log -1 --oneline
```

### ⚙️ Customize Update Frequency

Edit your crontab to change the schedule:

```bash
crontab -e
```

Common schedules:
- `0 */6 * * *` - Every 6 hours (default)
- `0 */12 * * *` - Every 12 hours
- `0 4 * * *` - Once daily at 4 AM
- `0 4 * * 0` - Once weekly on Sunday at 4 AM

### 🔧 Traditional Manual Update

If you prefer not to use auto-updates:

```bash
cd telegramhunter

# 1. Pull latest changes
git pull origin main

# 2. Rebuild and restart containers
docker compose up -d --build

# 3. Verify all services are running
docker compose ps
```

### Check for .env Changes

After updating, check if `.env.example` has new variables:

```bash
# Compare your .env with the example
diff .env .env.example
```

If new variables were added, copy them to your `.env` file.

### Automatic Token Import

On every container startup, the system automatically imports tokens from `import_tokens.csv` (if the file exists). This is useful for:

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
