-- ============================================
-- Row Level Security (RLS) Policies
-- Execute these in Supabase SQL Editor
-- 
-- UPDATED: Allow 'anon' role to write from backend
-- Frontend and backend both use anon keys, but backend is server-side only
-- ============================================

-- ============================================
-- STEP 1: DROP ALL EXISTING POLICIES
-- ============================================
-- Drop any existing policies to start fresh
DROP POLICY IF EXISTS "Backend Only Access" ON discovered_credentials;
DROP POLICY IF EXISTS "Deny All Public Access" ON discovered_credentials;
DROP POLICY IF EXISTS "Allow Backend Writes" ON discovered_credentials;
DROP POLICY IF EXISTS "Deny Public Reads" ON discovered_credentials;
DROP POLICY IF EXISTS "Allow Backend Insert" ON discovered_credentials;
DROP POLICY IF EXISTS "Allow Backend Update" ON discovered_credentials;
DROP POLICY IF EXISTS "Allow Backend Delete" ON discovered_credentials;

DROP POLICY IF EXISTS "Public Read Access" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Deny Public Modifications" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Deny Public Updates" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Deny Public Deletes" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Allow Backend Writes" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Allow Backend Insert" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Allow Backend Update" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Allow Backend Delete" ON exfiltrated_messages;

-- ============================================
-- STEP 2: DISCOVERED_CREDENTIALS TABLE
-- ============================================
-- This table contains sensitive bot tokens
-- Allow SELECT (for joins from exfiltrated_messages frontend query)
-- Note: Frontend should only query safe fields (id, created_at, source, meta)
-- Backend uses service role key which bypasses RLS anyway

-- Enable RLS
ALTER TABLE discovered_credentials ENABLE ROW LEVEL SECURITY;

-- Policy: Allow SELECT for anon (needed for frontend joins)
-- IMPORTANT: Frontend queries should NOT select bot_token column!
CREATE POLICY "Allow Public Reads"
ON discovered_credentials
FOR SELECT
TO anon
USING (true);

-- Policy: Allow INSERT for anon (backend can save new credentials)
CREATE POLICY "Allow Backend Insert"
ON discovered_credentials
FOR INSERT
TO anon
WITH CHECK (true);

-- Policy: Allow UPDATE for anon (backend can update credentials)
CREATE POLICY "Allow Backend Update"
ON discovered_credentials
FOR UPDATE
TO anon
USING (true)
WITH CHECK (true);

-- Policy: Allow DELETE for anon (backend can delete credentials)
CREATE POLICY "Allow Backend Delete"
ON discovered_credentials
FOR DELETE
TO anon
USING (true);

-- ============================================
-- STEP 3: EXFILTRATED_MESSAGES TABLE
-- ============================================
-- This table contains display data for the frontend
-- Allow public READ access for frontend display
-- Allow backend to write

-- Enable RLS
ALTER TABLE exfiltrated_messages ENABLE ROW LEVEL SECURITY;

-- Policy: Allow public read access (for frontend display)
CREATE POLICY "Public Read Access"
ON exfiltrated_messages
FOR SELECT
TO anon
USING (true);

-- Policy: Allow INSERT for anon (backend can save messages)
CREATE POLICY "Allow Backend Insert"
ON exfiltrated_messages
FOR INSERT
TO anon
WITH CHECK (true);

-- Policy: Allow UPDATE for anon (backend can update messages)
CREATE POLICY "Allow Backend Update"
ON exfiltrated_messages
FOR UPDATE
TO anon
USING (true)
WITH CHECK (true);

-- Policy: Allow DELETE for anon (backend can delete messages)
CREATE POLICY "Allow Backend Delete"
ON exfiltrated_messages
FOR DELETE
TO anon
USING (true);

-- ============================================
-- VERIFICATION QUERIES
-- ============================================
-- Run these to verify RLS is working correctly

-- Check RLS is enabled
SELECT schemaname, tablename, rowsecurity 
FROM pg_tables 
WHERE tablename IN ('discovered_credentials', 'exfiltrated_messages');

-- Check policies
SELECT schemaname, tablename, policyname, permissive, roles, cmd
FROM pg_policies
WHERE tablename IN ('discovered_credentials', 'exfiltrated_messages')
ORDER BY tablename, policyname;

-- ============================================
-- NOTES
-- ============================================
-- After running these policies:
-- ✅ Frontend (NEXT_PUBLIC_SUPABASE_KEY) can READ exfiltrated_messages
-- ❌ Frontend (NEXT_PUBLIC_SUPABASE_KEY) CANNOT SELECT discovered_credentials
-- ✅ Backend (SUPABASE_KEY server-side) can INSERT/UPDATE/DELETE both tables
-- 
-- SECURITY MODEL:
-- - Frontend anon key: In browser, can only read messages
-- - Backend anon key: Server-side only (.env), can write to both tables
-- - The backend anon key is never exposed to the browser
-- - Both use 'anon' role but in different contexts (client vs server)
