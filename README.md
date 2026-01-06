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

### New: Production-Ready Enhancements ✨

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
| Deployment | Docker Compose, Railway, Vercel |

## 📋 Prerequisites

1. **Docker & Docker Compose** installed
2. **Supabase Project** (run `init.sql` in SQL Editor)
3. **Telegram API Keys** from [my.telegram.org](https://my.telegram.org)
4. **Monitoring Bot Token** from [@BotFather](https://t.me/BotFather)
5. **API Keys** (optional): Shodan, URLScan, GitHub, FOFA

## ⚙️ Quick Start

### 1. Clone & Configure

```bash
git clone https://github.com/bryanseah234/telegramhunter.git
cd telegramhunter
cp .env.example .env
nano .env  # Fill in your keys
```

**Generate Encryption Key:**

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. Development Setup

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run validation
python scripts/validate_startup.py

# Optional: Setup pre-commit hooks
pip install pre-commit
pre-commit install
```

### 3. Initialize Database

Run `init.sql` in your Supabase SQL Editor.

### 4. Run Locally

```bash
docker-compose up --build
```

- **API**: <http://localhost:8000/docs>
- **Health Check**: <http://localhost:8000/health/detailed>
- **Metrics**: <http://localhost:8000/health/metrics>
- **Manual Scans**: <http://localhost:8000/scan/trigger-dev/github>

## ☁️ Production Deployment

### Pre-Deployment Validation

```bash
# Validate deployment readiness
python scripts/validate_deployment.py

# Check configuration
python scripts/validate_startup.py
```

### Backend → Railway/Oracle Cloud

1. SSH into your VM, install Docker
2. Clone repo & copy your `.env` file
3. Run: `docker-compose up -d --build`

**Railway Deployment:**

- Set environment variables in Railway dashboard
- Service will auto-deploy on push to main

### Frontend → Vercel

1. Import repo to Vercel
2. Set **Root Directory** to `frontend`
3. Add environment variables:
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_KEY`

## 🔒 Security

- **Production Mode**: POST scan endpoints are disabled
- **Audit Logging**: Tracks token decryption, credential access
- **Encrypted Storage**: All tokens encrypted with Fernet
- **Config Validation**: Startup checks for invalid configuration
- **RLS Policies**: Row-level security on Supabase tables

## 🖥 Usage

### Health & Monitoring

```bash
# Basic health check
curl http://localhost:8000/health/

# Detailed system status
curl http://localhost:8000/health/detailed

# Performance metrics
curl http://localhost:8000/health/metrics

# Circuit breaker status
curl http://localhost:8000/health/circuit-breakers

# Reset a circuit breaker
curl -X POST http://localhost:8000/health/circuit-breakers/shodan/reset
```

### Check Stats

```bash
curl http://localhost:8000/monitor/stats
```

### Manual Scan with Country Filter

```bash
# Scan specific country
curl http://localhost:8000/scan/trigger-dev/shodan?country_code=US

# Random country from TARGET_COUNTRIES
curl http://localhost:8000/scan/trigger-dev/shodan?country_code=RANDOM
```

### Manual Token Import

```bash
# Import CSV of tokens (Format: token,chat_id)
python tests/manual_scrape.py -i import_tokens.csv
```

### View Logs

```bash
docker-compose logs -f worker-scanner
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
├── scripts/               # Validation, setup, helpers
├── tests/
│   ├── unit/              # Unit tests
│   └── integration/       # Integration tests
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

### Worker Optimization

```bash
# Set concurrency and disable optimization for pycparser
export PYTHONOPTIMIZE=0
celery -A app.workers.celery_app worker -B --loglevel=info --concurrency=2
```

### Scan Schedule

Configured in `app/workers/celery_app.py`:

- **Heartbeat**: Every 30 minutes
- **Scans**: Every 12 hours (staggered by 20 minutes)
- **Broadcast**: Every 3 hours
- **Re-scrape**: Every 2 hours

## 🧪 Development

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
