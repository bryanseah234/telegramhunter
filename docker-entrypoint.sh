#!/bin/bash
# ==============================================
# Docker Entrypoint Script
# ==============================================

set -e

echo "🚀 [Entrypoint] Starting Telegram Hunter..."

# Create required directories and fix ownership for named volumes.
# Docker named volumes are created as root at first mount — chown here ensures
# the celery user can write to /app/beat (beat schedule) and /app/sessions.
mkdir -p /app/imports/processed /app/sessions /app/beat
# Only chown if we can (entrypoint runs as celery user, but volume dirs may be root-owned)
chown celery:celery /app/beat /app/sessions 2>/dev/null || true

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

# For scrape/core workers: clear stale session leases left from previous container lifecycle.
# Docker assigns new hostnames on each recreate, so old locked_by values are orphaned.
# This is safe — if another container holds a genuine lease it will re-acquire within seconds.
if [[ "$*" == *"celery"* ]]; then
    python3 -c "
import os, sys
sys.path.insert(0, '/app')
try:
    from app.core.database import db
    db.table('telegram_accounts').update({'locked_by': None, 'locked_until': None}).eq('status', 'active').execute()
    print('🔓 [Entrypoint] Cleared stale session leases.')
except Exception as e:
    print(f'⚠️ [Entrypoint] Could not clear leases: {e}')
" 2>/dev/null || true
fi

exec "$@"
