-- Migration: track telegram_user_id + admin-promoted state per session account
-- Purpose: enable membership audit (verify each session's account is still in
-- the monitor group) and auto-promote joining sessions with minimal admin
-- permissions (invite bots only — no group-edit/kick/pin/promote rights).
--
-- Idempotent — safe to re-run.

ALTER TABLE public.telegram_accounts
    ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS is_admin_promoted BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS in_monitor_group BOOLEAN DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS last_membership_check_at TIMESTAMPTZ DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_accounts_telegram_user_id
    ON public.telegram_accounts(telegram_user_id)
    WHERE telegram_user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_accounts_promoted
    ON public.telegram_accounts(is_admin_promoted, status)
    WHERE status = 'active';
