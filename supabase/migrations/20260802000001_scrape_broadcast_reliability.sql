-- Scrape/broadcast reliability columns for exfiltrated_messages.
-- Idempotent — safe to re-run.
-- Already applied manually in Supabase Dashboard on 2026-08-03.
-- Tracked here so `supabase db push` picks up future changes cleanly.
-- After login+link, baseline with:
--   supabase migration repair --status applied 20260802000001

ALTER TABLE public.exfiltrated_messages
    ADD COLUMN IF NOT EXISTS broadcast_error JSONB DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS broadcast_attempts INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_messages_next_retry
    ON public.exfiltrated_messages(is_broadcasted, next_retry_at)
    WHERE is_broadcasted = FALSE;
