# Telegram Hunter

**Telegram Hunter** is an automated, self-hosted system for harvesting, validating, and monitoring exposed Telegram bot tokens. It is built as a microservices architecture using **FastAPI**, **Celery**, **Redis**, and **Supabase**.

## 🚀 Features

- **Automated Scanning**: periodically scans OSINT sources (Shodan, FOFA) for exposed tokens.
- **Deep Scraping**: Logs in as the compromised bot (via Telethon) and scrapes chat history.
- **Exfiltration**: Saves messages, credentials, and metadata to a secure PostgreSQL database.
- **Real-time Monitoring**: Broadcasts finding summaries to your own private Telegram group.
- **Encryption**: All discovered tokens are encrypted at rest using Fernet (symmetric encryption).
- **Oracle Cloud Ready**: Optimized for ARM architectures (e.g., Oracle Always Free Tier).

## 🛠 Tech Stack

- **Core**: Python 3.11 (AsyncIO)
- **API**: FastAPI
- **Workers**: Celery + Redis
- **Database**: Supabase (PostgreSQL)
- **Libs**: Telethon (MTProto), Python-Telegram-Bot, Pydantic

## 📋 Prerequisites

1. **Oracle Cloud VM** (Ubuntu 22.04 recommended, ARM compatible).
2. **Docker & Docker Compose**.
3. **Supabase Project**: For the database.
4. **Telegram API Keys**:
    - `API_ID` & `API_HASH` (from [my.telegram.org](https://my.telegram.org)).
    - **Monitoring Bot Token** (from [@BotFather](https://t.me/BotFather)).
    - **Group ID**: The chat ID (or username) where you want alerts sent.

## ⚙️ Installation & Setup

### 1. Clone the Repository

```bash
git clone https://github.com/bryanseah234/telegramhunter.git
cd telegramhunter
```

### 2. Environment Configuration

Copy the example file and fill in your keys:

```bash
cp .env.example .env
nano .env
```

* **Essential**: `SUPABASE_URL`, `SUPABASE_KEY`, `ENCRYPTION_KEY`, `MONITOR_BOT_TOKEN`, `MONITOR_GROUP_ID`.
- **Scraping**: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`.
- **Scanning**: `SHODAN_KEY`, `FOFA_KEY`, etc.

**Generating an Encryption Key:**
You can generate a valid Fernet key with this Python 1-liner:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Database Initialization

1. Log in to your Supabase Dashboard.
2. Go to the **SQL Editor**.
3. Copy the contents of `init.sql` and run it. This creates the `discovered_credentials` and `exfiltrated_messages` tables.

### 4. Deploy (Oracle Cloud / Docker)

Run the stack in detached mode:

```bash
docker-compose up -d --build
```

* **Note**: The Dockerfile uses `python:3.11-slim-bookworm` which is fully compatible with Oracle Cloud ARM instances (Ampere).

## 🖥 Usage

### Monitor API

Check the status of the system:

```bash
curl http://localhost:8000/monitor/stats
```

### Trigger a Scan Manually

```bash
curl -X POST http://localhost:8000/scan/trigger \
  -H "Content-Type: application/json" \
  -d '{"source": "shodan", "query": "product:Telegram"}'
```

### Logs

Tail the worker logs to see scanning/scraping in action:

```bash
docker-compose logs -f worker
```

## ☁️ Oracle Cloud Specifics

- **Firewall**: Ensure you allow Ingress traffic on port `8000` (if you want public API access) and `22` (SSH) in your Oracle Cloud Subnet Security List.
- **IPTables**: Oracle Ubuntu images usually have strict iptables. You might need to flush them or explicitly allow ports:

    ```bash
    sudo iptables -F
    sudo netfilter-persistent save
    ```

## 🛡 Security Note

This tool is for **educational and defensive research purposes only**. Only use this on systems you own or have explicit permission to test.
