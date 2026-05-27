-- 004_add_confidence_score_generated_column.sql
-- Surface meta.confidence_score as a real INT column so sorts/filters
-- are numeric, not lexicographic. Generated column = no app-side writes,
-- no triggers, Postgres maintains it automatically from the JSONB field.
--
-- Safe to run multiple times (IF NOT EXISTS guards).

ALTER TABLE discovered_credentials
    ADD COLUMN IF NOT EXISTS confidence_score INTEGER
    GENERATED ALWAYS AS (
        CASE
            WHEN meta ? 'confidence_score'
              AND jsonb_typeof(meta->'confidence_score') = 'number'
            THEN (meta->>'confidence_score')::int
            ELSE NULL
        END
    ) STORED;

-- Index for ORDER BY confidence_score DESC dashboards.
-- Partial index on non-null only (most rows from before Bundle 4 are NULL,
-- index size stays bounded).
CREATE INDEX IF NOT EXISTS idx_discovered_credentials_confidence_score
    ON discovered_credentials (confidence_score DESC NULLS LAST)
    WHERE confidence_score IS NOT NULL;

-- chat_member_count surfaced the same way for "biggest chat first" sorts.
ALTER TABLE discovered_credentials
    ADD COLUMN IF NOT EXISTS chat_member_count INTEGER
    GENERATED ALWAYS AS (
        CASE
            WHEN meta ? 'chat_member_count'
              AND jsonb_typeof(meta->'chat_member_count') = 'number'
            THEN (meta->>'chat_member_count')::int
            ELSE NULL
        END
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_discovered_credentials_chat_member_count
    ON discovered_credentials (chat_member_count DESC NULLS LAST)
    WHERE chat_member_count IS NOT NULL;

-- Refresh the public view so anon/frontend can read the new columns.
-- View must be DROP+CREATE (not REPLACE) when adding columns in PG.
DROP VIEW IF EXISTS discovered_credentials_public;
CREATE VIEW discovered_credentials_public AS
SELECT
    id,
    created_at,
    source,
    status,
    meta,
    confidence_score,
    chat_member_count
FROM discovered_credentials;

GRANT SELECT ON discovered_credentials_public TO anon;
