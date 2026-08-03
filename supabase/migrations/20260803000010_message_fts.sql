-- Migration: pg_trgm-backed full-text search over exfiltrated_messages.content
-- Purpose: enable near-instant LIKE '%pattern%' queries across 283k+ messages
-- for OSINT hunting (bitcoin, phishing keywords, phone numbers, etc.)
--
-- Uses trigram GIN index — much better than tsvector for arbitrary substring
-- matching, and doesn't require language-specific stemming.
--
-- Idempotent — safe to re-run.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_messages_content_trgm
    ON public.exfiltrated_messages
    USING GIN (content gin_trgm_ops);

-- Also index sender_name for "find all messages from X" queries
CREATE INDEX IF NOT EXISTS idx_messages_sender_trgm
    ON public.exfiltrated_messages
    USING GIN (sender_name gin_trgm_ops);
