-- Migration: media forensics table
-- Purpose: track SHA-256 + perceptual hash of exfiltrated media so we can
-- detect the same photo/document being sent from multiple compromised bots
-- (which would identify a common threat-actor sender or reused payload).
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS public.media_hashes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES public.exfiltrated_messages(id) ON DELETE CASCADE,
    credential_id UUID REFERENCES public.discovered_credentials(id) ON DELETE SET NULL,
    sha256 TEXT NOT NULL,
    phash TEXT DEFAULT NULL,
    file_size_bytes INT DEFAULT NULL,
    mime_type TEXT DEFAULT NULL,
    media_type TEXT DEFAULT NULL,
    downloaded_at TIMESTAMPTZ DEFAULT NOW(),
    error TEXT DEFAULT NULL,
    UNIQUE (message_id)
);

-- Duplicate detection: find same photo across bots
CREATE INDEX IF NOT EXISTS idx_media_hashes_sha256
    ON public.media_hashes(sha256);
CREATE INDEX IF NOT EXISTS idx_media_hashes_phash
    ON public.media_hashes(phash)
    WHERE phash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_media_hashes_credential
    ON public.media_hashes(credential_id);
