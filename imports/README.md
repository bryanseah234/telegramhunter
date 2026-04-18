# Imports Directory

Drop your CSV files here for automatic import on container startup.

## Supported Formats

```csv
token,chat_id
1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx,-1001234567890
9876543210:AAyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy,
```

## How It Works

1. Drop any `.csv` files in this folder
2. Restart Docker: `docker compose restart`
3. Files are renamed to `.pending` and moved to `processed/`

**NOTE (T011)**: Current behavior only marks files as `.pending`; automated import task not yet implemented. Use manual import via `scripts/` if available, or add a Celery task to process `.pending` files.

## Also Accepted

- `fofa_scraper_*.csv` files in project root
- `import_tokens.csv` in project root (legacy)
