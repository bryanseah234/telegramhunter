#!/bin/bash
# ==============================================
# Docker Entrypoint Script
# Runs CSV import on startup, then starts the service
# Supports multiple CSV files for batch processing
# ==============================================

set -e

echo "🚀 [Entrypoint] Starting Telegram Hunter..."

# Create imports directory if it doesn't exist
mkdir -p /app/imports

# Collect all CSV files to import
CSV_FILES=()

# 1. Check imports/ directory for any CSV files
if ls /app/imports/*.csv 1> /dev/null 2>&1; then
    CSV_FILES+=(/app/imports/*.csv)
fi

# 2. Check for fofa_scraper_*.csv files in root (Chrome extension exports)
if ls /app/fofa_scraper_*.csv 1> /dev/null 2>&1; then
    CSV_FILES+=(/app/fofa_scraper_*.csv)
fi

# 3. Check for import_tokens.csv (legacy single file)
if [ -f "/app/import_tokens.csv" ] && [ -s "/app/import_tokens.csv" ]; then
    CSV_FILES+=("/app/import_tokens.csv")
fi

# Process all found CSV files
if [ ${#CSV_FILES[@]} -gt 0 ]; then
    echo "📂 [Entrypoint] Found ${#CSV_FILES[@]} CSV file(s) to import:"
    for csv in "${CSV_FILES[@]}"; do
        echo "   - $(basename "$csv")"
    done
    
    for csv in "${CSV_FILES[@]}"; do
        echo "📥 [Entrypoint] Importing: $(basename "$csv")..."
        python /app/tests/manual_scrape.py -i "$csv" || echo "⚠️ [Entrypoint] Warning: Some tokens in $(basename "$csv") may have failed"
        
        # Move processed file to imports/processed/
        mkdir -p /app/imports/processed
        mv "$csv" "/app/imports/processed/$(basename "$csv").done" 2>/dev/null || true
    done
    
    echo "✅ [Entrypoint] All imports complete."
else
    echo "ℹ️ [Entrypoint] No CSV files found to import."
    echo "   Place files in: /app/imports/ or /app/import_tokens.csv"
fi

# 4. Run Self-Healing Sync (Bridge gaps between DB and Telegram)
# Skip if we are running the login script to prevent database locking issues
if [[ "$*" == *"scripts/login_user.py"* ]]; then
    echo "🔐 [Entrypoint] Login detected. Skipping Self-Healing Sync to avoid locks."
else
    echo "🩹 [Entrypoint] Running Self-Healing Sync..."
    python /app/scripts/self_heal_sync.py || echo "⚠️ [Entrypoint] Warning: Self-healing sync failed (ignoring to allow startup)"
fi

# Execute the main command passed to the container
echo "🎯 [Entrypoint] Starting main service: $@"
exec "$@"

