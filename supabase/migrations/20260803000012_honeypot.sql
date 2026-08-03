-- Migration: honeypot_updates table
-- Purpose: store incoming webhook POSTs captured after we take over a
-- third-party's stolen webhook and re-register it to point at us.
-- Only populated when HONEYPOT_MODE=True + public HTTPS endpoint deployed.
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS public.honeypot_updates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    credential_id UUID REFERENCES public.discovered_credentials(id) ON DELETE SET NULL,
    update_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    received_at TIMESTAMPTZ DEFAULT NOW(),
    source_ip TEXT DEFAULT NULL,
    processed_at TIMESTAMPTZ DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_honeypot_credential
    ON public.honeypot_updates(credential_id);
CREATE INDEX IF NOT EXISTS idx_honeypot_received
    ON public.honeypot_updates(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_honeypot_type
    ON public.honeypot_updates(update_type);
CREATE INDEX IF NOT EXISTS idx_honeypot_unprocessed
    ON public.honeypot_updates(received_at)
    WHERE processed_at IS NULL;
