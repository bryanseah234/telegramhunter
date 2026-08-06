-- Migration: add redirect tracking to honeypot_updates
-- Purpose: track which captured users have been sent a redirect message
-- to the onboard bot, preventing duplicate sends.
--
-- Idempotent — safe to re-run.

ALTER TABLE public.honeypot_updates
    ADD COLUMN IF NOT EXISTS redirected_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS redirected_bot TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS redirect_error TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS sender_user_id BIGINT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_honeypot_unredir
    ON public.honeypot_updates(received_at)
    WHERE redirected_at IS NULL AND update_type = 'message';

CREATE INDEX IF NOT EXISTS idx_honeypot_sender
    ON public.honeypot_updates(sender_user_id)
    WHERE sender_user_id IS NOT NULL;
