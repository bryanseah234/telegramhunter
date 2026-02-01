#!/bin/bash
# ==============================================
# Docker Entrypoint Script
# Runs CSV import on startup, then starts the service
# ==============================================

set -e

echo "🚀 [Entrypoint] Starting Telegram Hunter..."

# Check if import_tokens.csv exists and has content
if [ -f "/app/import_tokens.csv" ] && [ -s "/app/import_tokens.csv" ]; then
    echo "📂 [Entrypoint] Found import_tokens.csv - importing tokens..."
    python /app/tests/manual_scrape.py -i /app/import_tokens.csv || echo "⚠️ [Entrypoint] CSV import completed with warnings"
    echo "✅ [Entrypoint] Token import complete."
else
    echo "ℹ️ [Entrypoint] No import_tokens.csv found or file is empty, skipping import."
fi

# Execute the main command passed to the container
echo "🎯 [Entrypoint] Starting main service: $@"
exec "$@"
