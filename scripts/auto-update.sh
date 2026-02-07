#!/bin/bash
# =============================================================================
# Telegram Hunter Auto-Update Script
# 
# Automatically checks for new releases and updates the Docker deployment.
# Add to cron for fully automated updates.
#
# Usage:
#   ./scripts/auto-update.sh              # Run manually
#   ./scripts/auto-update.sh --force      # Force rebuild even if up-to-date
#   ./scripts/auto-update.sh --check-only # Just check, don't update
# =============================================================================

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_FILE="${PROJECT_DIR}/logs/auto-update.log"
BRANCH="main"
FORCE_UPDATE=false
CHECK_ONLY=false

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
for arg in "$@"; do
    case $arg in
        --force)
            FORCE_UPDATE=true
            shift
            ;;
        --check-only)
            CHECK_ONLY=true
            shift
            ;;
    esac
done

# Ensure logs directory exists
mkdir -p "${PROJECT_DIR}/logs"

# Logging function
log() {
    local level=$1
    local message=$2
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${timestamp} [${level}] ${message}" | tee -a "$LOG_FILE"
}

log_info() { log "INFO" "${BLUE}$1${NC}"; }
log_success() { log "SUCCESS" "${GREEN}$1${NC}"; }
log_warn() { log "WARN" "${YELLOW}$1${NC}"; }
log_error() { log "ERROR" "${RED}$1${NC}"; }

# Change to project directory
cd "$PROJECT_DIR"

log_info "=========================================="
log_info "Telegram Hunter Auto-Update Check"
log_info "=========================================="
log_info "Project directory: $PROJECT_DIR"
log_info "Branch: $BRANCH"

# Check if git repository
if [ ! -d ".git" ]; then
    log_error "Not a git repository. Please clone the repo first."
    exit 1
fi

# Fetch latest changes from remote
log_info "Fetching latest changes from origin..."
git fetch origin "$BRANCH" --quiet

# Get current and remote commit hashes
LOCAL_COMMIT=$(git rev-parse HEAD)
REMOTE_COMMIT=$(git rev-parse "origin/$BRANCH")
LOCAL_SHORT=$(git rev-parse --short HEAD)
REMOTE_SHORT=$(git rev-parse --short "origin/$BRANCH")

log_info "Local version:  $LOCAL_SHORT"
log_info "Remote version: $REMOTE_SHORT"

# Check if update is needed
if [ "$LOCAL_COMMIT" = "$REMOTE_COMMIT" ] && [ "$FORCE_UPDATE" = false ]; then
    log_success "✅ Already up to date! No action needed."
    exit 0
fi

# Show what's new
log_info "📋 Changes since last update:"
git log --oneline HEAD..origin/$BRANCH 2>/dev/null || log_info "(new commits available)"

# Check-only mode exits here
if [ "$CHECK_ONLY" = true ]; then
    log_warn "🔍 Update available! Run without --check-only to apply."
    exit 0
fi

log_info "📥 New version available! Updating..."

# Check for local changes that might conflict
if ! git diff-index --quiet HEAD --; then
    log_warn "⚠️  Local changes detected. Stashing..."
    git stash push -m "Auto-update stash $(date '+%Y-%m-%d_%H:%M:%S')"
fi

# Pull latest changes
log_info "Pulling latest changes..."
if git pull origin "$BRANCH"; then
    log_success "Git pull successful!"
else
    log_error "Git pull failed. Please resolve manually."
    exit 1
fi

# Check if .env.example has new variables
if [ -f ".env" ] && [ -f ".env.example" ]; then
    log_info "Checking for new environment variables..."
    NEW_VARS=$(comm -23 <(grep -oP '^[A-Z_]+(?==)' .env.example | sort) <(grep -oP '^[A-Z_]+(?==)' .env | sort) 2>/dev/null || true)
    if [ -n "$NEW_VARS" ]; then
        log_warn "⚠️  New environment variables in .env.example:"
        echo "$NEW_VARS" | while read var; do
            log_warn "   - $var"
        done
        log_warn "Please add these to your .env file!"
    fi
fi

# Stop containers gracefully
log_info "Stopping current containers..."
docker compose down --timeout 30 || docker-compose down --timeout 30

# Rebuild images
log_info "Rebuilding Docker images (this may take a few minutes)..."
if docker compose build --no-cache 2>&1 | tee -a "$LOG_FILE"; then
    log_success "Docker build successful!"
else
    # Fallback to docker-compose (older systems)
    if docker-compose build --no-cache 2>&1 | tee -a "$LOG_FILE"; then
        log_success "Docker build successful!"
    else
        log_error "Docker build failed! Check logs at: $LOG_FILE"
        exit 1
    fi
fi

# Start containers
log_info "Starting updated containers..."
docker compose up -d || docker-compose up -d

# Wait for services to be healthy
log_info "Waiting for services to start (30 seconds)..."
sleep 30

# Verify services are running
log_info "Verifying services..."
RUNNING_CONTAINERS=$(docker compose ps --format "{{.Name}}" 2>/dev/null | wc -l || docker-compose ps -q 2>/dev/null | wc -l)

if [ "$RUNNING_CONTAINERS" -ge 3 ]; then
    log_success "✅ Update complete! $RUNNING_CONTAINERS containers running."
    
    # Show running containers
    docker compose ps 2>/dev/null || docker-compose ps
    
    # Health check
    log_info "Running health check..."
    if curl -s http://localhost:8000/health/ > /dev/null 2>&1; then
        log_success "✅ API is healthy!"
    else
        log_warn "⚠️  API health check failed. It may still be starting up."
    fi
else
    log_error "❌ Some containers failed to start. Check with: docker compose logs"
    exit 1
fi

log_info "=========================================="
log_success "🎉 Telegram Hunter updated to $REMOTE_SHORT"
log_info "=========================================="

# =============================================================================
# System Resource Cleanup
# =============================================================================

log_info "🧹 Cleaning up system resources..."

# Docker cleanup - images, containers, build cache
log_info "Pruning unused Docker images..."
docker image prune -af > /dev/null 2>&1 || true

log_info "Pruning stopped containers..."
docker container prune -f > /dev/null 2>&1 || true

log_info "Pruning unused build cache..."
docker builder prune -f > /dev/null 2>&1 || true

# Note: We don't prune volumes automatically to avoid data loss
# Uncomment the line below if you want aggressive volume cleanup:
# docker volume prune -f > /dev/null 2>&1 || true

# Log rotation - keep last 5 log files, max 10MB each
log_info "Rotating logs..."
LOG_DIR="${PROJECT_DIR}/logs"
MAX_LOG_SIZE=10485760  # 10MB in bytes
MAX_LOG_FILES=5

if [ -f "$LOG_FILE" ]; then
    LOG_SIZE=$(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$LOG_SIZE" -gt "$MAX_LOG_SIZE" ]; then
        log_info "Log file exceeds 10MB, rotating..."
        # Rotate existing logs
        for i in $(seq $((MAX_LOG_FILES-1)) -1 1); do
            if [ -f "${LOG_FILE}.$i" ]; then
                mv "${LOG_FILE}.$i" "${LOG_FILE}.$((i+1))"
            fi
        done
        # Compress and rotate current log
        mv "$LOG_FILE" "${LOG_FILE}.1"
        gzip -f "${LOG_FILE}.1" 2>/dev/null || true
        # Remove oldest logs beyond MAX_LOG_FILES
        find "$LOG_DIR" -name "auto-update.log.*" -type f | sort -r | tail -n +$((MAX_LOG_FILES+1)) | xargs rm -f 2>/dev/null || true
        log_info "Log rotation complete."
    fi
fi

# Report disk space saved
DOCKER_SPACE=$(docker system df --format "{{.Reclaimable}}" 2>/dev/null | head -1 || echo "unknown")
log_info "Docker reclaimable space: $DOCKER_SPACE"

log_success "✅ Cleanup complete!"

exit 0

