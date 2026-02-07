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
3. Files are processed and moved to `processed/` subfolder

## Also Accepted

- `fofa_scraper_*.csv` files in project root
- `import_tokens.csv` in project root (legacy)
