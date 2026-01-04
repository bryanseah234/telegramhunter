# Telegram Hunter

**Telegram Hunter** is an automated, self-hosted OSINT system for discovering, validating, and monitoring exposed Telegram bot tokens. Built as a microservices architecture using **FastAPI**, **Celery**, **Redis**, and **Supabase**.

## 🚀 Features

- **Multi-Source Scanning**: GitHub, Shodan, Censys, FOFA, Hybrid Analysis
- **Token Enrichment**: Auto-discovers chats linked to each token
- **Deep Scraping**: Logs in as the bot (via Telethon) and scrapes chat history
- **Real-time Alerts**: Broadcasts findings to your private Telegram group
- **Encryption**: All tokens encrypted at rest (Fernet)
- **Frontend Dashboard**: Telegram-style UI to browse discovered data

## 🛠 Tech Stack

| Component | Technology |
|-----------|------------|
| API | FastAPI |
| Workers | Celery + Redis |
| Database | Supabase (PostgreSQL) |
| Scraping | Telethon (MTProto) |
| Frontend | Next.js + Tailwind CSS |
| Deployment | Docker Compose (Oracle Cloud), Vercel (Frontend) |

## 📋 Prerequisites

1. **Docker & Docker Compose** installed
2. **Supabase Project** (run `init.sql` in SQL Editor)
3. **Telegram API Keys** from [my.telegram.org](https://my.telegram.org)
4. **Monitoring Bot Token** from [@BotFather](https://t.me/BotFather)

## ⚙️ Quick Start

### 1. Clone & Configure

```bash
git clone https://github.com/bryanseah234/telegramhunter.git
cd telegramhunter
cp .env.example .env
nano .env  # Fill in your keys
# Tip: Set WHITELISTED_BOT_IDS to keep specific bots (like admins) in the group during cleanup.
```

**Generate Encryption Key:**

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. Initialize Database

Run `init.sql` in your Supabase SQL Editor.

### 3. Run Locally

```bash
docker-compose up --build
```

- **API**: <http://localhost:8000/docs>
- **Manual Scans**: <http://localhost:8000/scan/trigger-dev/github>

## ☁️ Production Deployment

### Backend → Oracle Cloud

1. SSH into your VM, install Docker
2. Clone repo & copy your `.env` file
3. Run: `docker-compose up -d --build`

**Automated Deployments**: Add these secrets to GitHub Actions:

- `ORACLE_HOST` - Your VM's public IP
- `ORACLE_USERNAME` - Usually `ubuntu`
- `ORACLE_KEY` - Your private SSH key content

### Frontend → Vercel

1. Import repo to Vercel
2. Set **Root Directory** to `frontend`
3. Add environment variables:
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_KEY`

### GitHub Actions Secrets

For the Supabase keep-alive workflow:

- `SUPABASE_URL`
- `SUPABASE_KEY`

## 🔒 Security

- **Production Mode**: POST scan endpoints are disabled
- **Dev Endpoints**: Only accessible from `localhost`
- **API Docs**: Hidden in production (`/docs` returns 404)

## 🖥 Usage

### Check Stats

```bash
curl http://localhost:8000/monitor/stats
```

### Manual Scan (Dev Mode)

```bash
curl http://localhost:8000/scan/trigger-dev/github
```

### Manual Token Import
Import a CSV of tokens (Format: `token,chat_id`):

```bash
# 1. Place CSV in root as import_tokens.csv
# 2. Run import script
python tests/manual_scrape.py -i import_tokens.csv
```

### View Logs

```bash
docker-compose logs -f worker-scanner
```

telegramhunter/
├── app/                    # FastAPI backend
│   ├── api/               # API routes
│   ├── services/          # Scanner & Scraper services
│   └── workers/           # Celery tasks
├── frontend/              # Next.js dashboard
│   └── public/            # Static assets (logo.png)
├── scripts/               # Helper scripts (login, stats, regex)
├── tests/                 # Pytest suite
├── docker-compose.yml     # Orchestration
├── init.sql              # Database schema
└── .env.example          # Environment template

```

## ⚙️ Configuration Hints

### Worker Optimization (Critical)
To prevent crashes and ensure stability on limited RAM:
*   **Concurrency:** `2` (1 Scan + 1 Broadcast)
*   **Optimization:** Must use `PYTHONOPTIMIZE=0` to support `pycparser`.
*   **Command:**
    ```bash
    export PYTHONOPTIMIZE=0; celery -A app.workers.celery_app worker -B --loglevel=info --concurrency=2
    ```

### Scan Schedule
Scans run **3 times a day** (Every 8 hours) with partial staggering to prevent CPU spikes:
*   **Times (UTC):** 00:00, 08:00, 16:00
*   **Timeout:** 15 Minutes per scan type.

## 🛡 Disclaimer

This tool is for **educational and defensive research purposes only**. Only use on systems you own or have explicit permission to test.
