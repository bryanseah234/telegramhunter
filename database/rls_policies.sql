-- ============================================
-- Row Level Security (RLS) Policies
-- Execute these in Supabase SQL Editor
-- ============================================

-- ============================================
-- STEP 1: DROP ALL EXISTING POLICIES
-- ============================================
DROP POLICY IF EXISTS "Backend Only Access"       ON discovered_credentials;
DROP POLICY IF EXISTS "Deny All Public Access"    ON discovered_credentials;
DROP POLICY IF EXISTS "Allow Backend Writes"      ON discovered_credentials;
DROP POLICY IF EXISTS "Deny Public Reads"         ON discovered_credentials;
DROP POLICY IF EXISTS "Allow Backend Insert"      ON discovered_credentials;
DROP POLICY IF EXISTS "Allow Backend Update"      ON discovered_credentials;
DROP POLICY IF EXISTS "Allow Backend Delete"      ON discovered_credentials;
DROP POLICY IF EXISTS "Allow Public Reads"        ON discovered_credentials;
DROP POLICY IF EXISTS "Extension Insert"          ON discovered_credentials;
DROP POLICY IF EXISTS "Extension Update"          ON discovered_credentials;

DROP POLICY IF EXISTS "Public Read Access"        ON exfiltrated_messages;
DROP POLICY IF EXISTS "Deny Public Modifications" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Deny Public Updates"       ON exfiltrated_messages;
DROP POLICY IF EXISTS "Deny Public Deletes"       ON exfiltrated_messages;
DROP POLICY IF EXISTS "Allow Backend Writes"      ON exfiltrated_messages;
DROP POLICY IF EXISTS "Allow Backend Insert"      ON exfiltrated_messages;
DROP POLICY IF EXISTS "Allow Backend Update"      ON exfiltrated_messages;
DROP POLICY IF EXISTS "Allow Backend Delete"      ON exfiltrated_messages;

-- ============================================
-- STEP 2: STORE THE EXTENSION WRITE SECRET
-- ============================================
-- Run this ONCE, replacing the placeholder with your EXTENSION_WRITE_SECRET from .env.
-- This value lives only inside your Supabase database — never in source control.
--
--   ALTER DATABASE postgres
--     SET app.extension_write_secret = 'your_EXTENSION_WRITE_SECRET_value_here';
--   SELECT pg_reload_conf();
--
-- Retrieve it anytime:
--   SELECT current_setting('app.extension_write_secret');

-- ============================================
-- STEP 3: discovered_credentials TABLE
-- ============================================
--
-- WHO ACCESSES THIS TABLE:
--   - Backend workers/API  → SERVICE_ROLE key → bypasses RLS entirely, no policy needed
--   - Chrome extension     → anon key         → INSERT/UPDATE only, gated by write secret
--   - Frontend (Sidebar)   → anon key         → SELECT via discovered_credentials_public VIEW only
--                                                (the VIEW is defined in init.sql)
--
-- RESULT: anon can never SELECT the raw table (bot_token, token_hash etc. stay hidden).
--         anon can INSERT/UPDATE only when the correct write secret header is present.
--         Service role bypasses everything — workers are unaffected.

ALTER TABLE discovered_credentials ENABLE ROW LEVEL SECURITY;

-- Extension INSERT: only when x-extension-secret header matches the DB-stored secret
CREATE POLICY "Extension Insert"
ON discovered_credentials
FOR INSERT
TO anon
WITH CHECK (
    (current_setting('request.headers', true)::json ->> 'x-extension-secret')
        = current_setting('app.extension_write_secret', true)
    AND current_setting('app.extension_write_secret', true) IS NOT NULL
    AND current_setting('app.extension_write_secret', true) <> ''
);

-- Extension UPDATE: same secret check
CREATE POLICY "Extension Update"
ON discovered_credentials
FOR UPDATE
TO anon
USING (
    (current_setting('request.headers', true)::json ->> 'x-extension-secret')
        = current_setting('app.extension_write_secret', true)
    AND current_setting('app.extension_write_secret', true) IS NOT NULL
    AND current_setting('app.extension_write_secret', true) <> ''
)
WITH CHECK (
    (current_setting('request.headers', true)::json ->> 'x-extension-secret')
        = current_setting('app.extension_write_secret', true)
    AND current_setting('app.extension_write_secret', true) IS NOT NULL
    AND current_setting('app.extension_write_secret', true) <> ''
);

-- No anon SELECT on the raw table — frontend must use the discovered_credentials_public VIEW.
-- No anon DELETE — only service role can delete.

-- ============================================
-- STEP 4: exfiltrated_messages TABLE
-- ============================================
--
-- WHO ACCESSES THIS TABLE:
--   - Backend workers/API  → SERVICE_ROLE key → bypasses RLS, no policy needed
--   - Frontend ChatWindow  → anon key         → SELECT + realtime subscribe
--   - Frontend Sidebar     → anon key         → SELECT credential_id only
--
-- RESULT: anon can read messages (needed for the webapp display).
--         anon cannot write — only the service role worker writes here.

ALTER TABLE exfiltrated_messages ENABLE ROW LEVEL SECURITY;

-- Frontend needs to read messages to display the chat window
CREATE POLICY "Public Read Access"
ON exfiltrated_messages
FOR SELECT
TO anon
USING (true);

-- No anon INSERT/UPDATE/DELETE — service role handles all writes.

-- ============================================
-- STEP 5: telegram_accounts TABLE
-- ============================================
--
-- WHO ACCESSES THIS TABLE:
--   - bot_listener.py → SERVICE_ROLE key → bypasses RLS, no policy needed
--   - Nobody else
--
-- RESULT: anon has zero access. No policies needed — RLS enabled = deny by default.

ALTER TABLE telegram_accounts ENABLE ROW LEVEL SECURITY;

-- No policies for anon. Service role bypasses RLS and handles all access.

-- ============================================
-- VERIFICATION QUERIES
-- ============================================
SELECT schemaname, tablename, rowsecurity
FROM pg_tables
WHERE tablename IN ('discovered_credentials', 'exfiltrated_messages', 'telegram_accounts');

SELECT schemaname, tablename, policyname, permissive, roles, cmd
FROM pg_policies
WHERE tablename IN ('discovered_credentials', 'exfiltrated_messages', 'telegram_accounts')
ORDER BY tablename, policyname;

-- ============================================
-- SECURITY MODEL SUMMARY
-- ============================================
--
-- SERVICE_ROLE key (backend .env only, never in browser):
--   ✅ Full access to all tables, bypasses RLS
--   Used by: all workers, FastAPI, Celery tasks
--
-- ANON key (frontend / extension — can be public):
--   discovered_credentials  → INSERT/UPDATE only WITH valid x-extension-secret header
--   discovered_credentials  → SELECT blocked on raw table (use the _public VIEW)
--   exfiltrated_messages    → SELECT only (frontend display + realtime)
--   telegram_accounts       → no access at all
--
-- Anyone who clones the repo gets:
--   - The anon key (if accidentally committed) → can only read exfiltrated_messages
--   - The RLS policy code → shows the mechanism, not the secret value
--   - Zero write access without EXTENSION_WRITE_SECRET
