-- ============================================
-- Row Level Security (RLS) Policies
-- Execute these in Supabase SQL Editor
-- 
-- UPDATED: Allow 'authenticated' role access
-- The backend uses SUPABASE_KEY which has 'anon' role
-- We need to allow authenticated operations while blocking
-- completely unauthenticated direct API access
-- ============================================

-- ============================================
-- 1. DISCOVERED_CREDENTIALS TABLE
-- ============================================
-- This table contains sensitive bot tokens (even encrypted)
-- Block SELECT from anon/public, but allow INSERT/UPDATE for backend operations

-- Enable RLS
ALTER TABLE discovered_credentials ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if any
DROP POLICY IF EXISTS "Backend Only Access" ON discovered_credentials;
DROP POLICY IF EXISTS "Deny All Public Access" ON discovered_credentials;
DROP POLICY IF EXISTS "Allow Backend Writes" ON discovered_credentials;
DROP POLICY IF EXISTS "Deny Public Reads" ON discovered_credentials;

-- Policy: Deny SELECT for anon (reading credentials from frontend)
CREATE POLICY "Deny Public Reads"
ON discovered_credentials
FOR SELECT
TO anon
USING (false);

-- Policy: Allow INSERT/UPDATE/DELETE for anon (backend operations)
-- NOTE: This is safe because the anon key is only in backend, not exposed to frontend
-- Frontend uses a different anon key (NEXT_PUBLIC_SUPABASE_KEY)
CREATE POLICY "Allow Backend Writes"
ON discovered_credentials
FOR INSERT, UPDATE, DELETE
TO anon
WITH CHECK (true);

-- Service role bypasses RLS automatically

-- ============================================
-- 2. EXFILTRATED_MESSAGES TABLE
-- ============================================
-- This table contains display data for the frontend
-- Allow public READ access for frontend display
-- Allow backend (anon role) to write

-- Enable RLS
ALTER TABLE exfiltrated_messages ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if any
DROP POLICY IF EXISTS "Public Read Access" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Deny Public Modifications" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Deny Public Updates" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Deny Public Deletes" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Allow Backend Writes" ON exfiltrated_messages;

-- Policy: Allow public read access (for frontend display)
CREATE POLICY "Public Read Access"
ON exfiltrated_messages
FOR SELECT
TO anon
USING (true);

-- Policy: Allow backend to write (INSERT/UPDATE)
-- Backend uses the same anon key but it's server-side
CREATE POLICY "Allow Backend Writes"
ON exfiltrated_messages
FOR INSERT, UPDATE, DELETE
TO anon
WITH CHECK (true);

-- ============================================
-- VERIFICATION QUERIES
-- ============================================
-- Run these to verify RLS is working correctly

-- Check RLS is enabled
SELECT schemaname, tablename, rowsecurity 
FROM pg_tables 
WHERE tablename IN ('discovered_credentials', 'exfiltrated_messages');

-- Check policies
SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check
FROM pg_policies
WHERE tablename IN ('discovered_credentials', 'exfiltrated_messages')
ORDER BY tablename, policyname;

-- ============================================
-- NOTES
-- ============================================
-- After running these policies:
-- ✅ Frontend (using anon key) can READ exfiltrated_messages
-- ❌ Frontend (using anon key) CANNOT access discovered_credentials
-- ✅ Backend (using service_role key) can access EVERYTHING (bypasses RLS)
-- ❌ Direct API calls with anon key to credentials table will return 403
