# Application-wide constants — replaces magic numbers scattered across modules.

# Bot listener distributed lock TTL (seconds)
LOCK_TTL_SECONDS = 120

# Broadcast claim timeout — claims older than this are considered stale (minutes)
CLAIM_TIMEOUT_MINUTES = 5

# Worker heartbeat timeout — alert if worker silent longer than this (seconds)
WORKER_HEARTBEAT_TIMEOUT_SECONDS = 45 * 60

# Rate limit sleep between broadcast sends (seconds)
BROADCAST_RATE_LIMIT_SLEEP = 2.0

# Maximum entries kept in scanner error buffers
MAX_ERRORS_BUFFER = 100

# Session file permissions: owner read/write only
SESSION_FILE_PERMISSIONS = 0o600
