-- Migration: add broadcasted_at column to exfiltrated_messages
-- Purpose: capture the exact timestamp of successful broadcast so
-- flow.exfil_latency_report can compute true broadcasted_at - created_at
-- latency instead of the current upper-bound proxy (NOW - created_at).
--
-- Idempotent — safe to re-run.
-- Note: no updated_at column exists on this table, so no backfill of
-- legacy rows is possible. The new column starts NULL for existing rows
-- and gets populated from broadcasts going forward.

ALTER TABLE public.exfiltrated_messages
    ADD COLUMN IF NOT EXISTS broadcasted_at TIMESTAMPTZ DEFAULT NULL;

-- Index on broadcasted_at for latency queries (only on rows that succeeded)
CREATE INDEX IF NOT EXISTS idx_messages_broadcasted_at
    ON public.exfiltrated_messages(broadcasted_at)
    WHERE broadcasted_at IS NOT NULL;
