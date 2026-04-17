#!/bin/bash
# ==============================================
# Docker Entrypoint Script
# ==============================================

set -e

echo "🚀 [Entrypoint] Starting Telegram Hunter..."

# Create required directories
mkdir -p /app/imports/processed /app/sessions

# Process any CSV files dropped into imports/
CSV_FILES=()
if ls /app/imports/*.csv 1>/dev/null 2>&1; then
    CSV_FILES+=(/app/imports/*.csv)
fi
if ls /app/fofa_scraper_*.csv 1>/dev/null 2>&1; then
    CSV_FILES+=(/app/fofa_scraper_*.csv)
fi

if [ ${#CSV_FILES[@]} -gt 0 ]; then
    echo "📂 [Entrypoint] Found ${#CSV_FILES[@]} CSV file(s) — queuing for import on next worker run."
    for csv in "${CSV_FILES[@]}"; do
        mv "$csv" "/app/imports/processed/$(basename "$csv").pending" 2>/dev/null || true
    done
fi

# Execute the main command
echo "🎯 [Entrypoint] Starting: $*"
exec "$@"
