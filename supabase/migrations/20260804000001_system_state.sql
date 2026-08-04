-- Migration: system_state key-value table
-- Purpose: a small, versionless place to persist singleton state like
-- 'pinned_readme_msg_id', 'canary_last_run', 'last_takeover_alert_at', etc.
-- Prevents scattered custom tables for one-off flags.
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS public.system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
