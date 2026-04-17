-- ============================================================
-- Telegram Hunter — Database Schema
-- Run this once in your Supabase SQL Editor to initialize.
-- Safe to re-run: all statements use IF NOT EXISTS guards.
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
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_creds_status   ON discovered_credentials(status);
CREATE INDEX IF NOT EXISTS idx_creds_bot_id   ON discovered_credentials(bot_id);


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
