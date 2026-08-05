-- Migration: add sender_user_id to exfiltrated_messages
-- Purpose: enable attribution graph — link the same Telegram user_id
-- across multiple compromised bots to identify serial victims and
-- coordinated operator patterns.
--
-- Currently we only store sender_name (display name) which is not unique
-- and can't be joined reliably. sender_user_id is the immutable numeric
-- Telegram user ID from message.from.id.
--
-- Idempotent — safe to re-run.

ALTER TABLE public.exfiltrated_messages
    ADD COLUMN IF NOT EXISTS sender_user_id BIGINT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_messages_sender_user_id
    ON public.exfiltrated_messages(sender_user_id)
    WHERE sender_user_id IS NOT NULL;
