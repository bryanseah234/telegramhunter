-- ============================================
-- Row Level Security (RLS) Policies
-- Execute these in Supabase SQL Editor
-- ============================================

-- ============================================
-- 1. DISCOVERED_CREDENTIALS TABLE
-- ============================================
-- This table contains sensitive bot tokens (even encrypted)
-- Only backend with service_role key should access this table
-- Frontend should NEVER query this table directly

-- Enable RLS
ALTER TABLE discovered_credentials ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if any
DROP POLICY IF EXISTS "Backend Only Access" ON discovered_credentials;
DROP POLICY IF EXISTS "Deny All Public Access" ON discovered_credentials;

-- Policy: Deny ALL public access (using anon key)
-- The service_role key bypasses RLS, so backend workers can still access
CREATE POLICY "Deny All Public Access"
ON discovered_credentials
FOR ALL
TO anon
USING (false)
WITH CHECK (false);

-- ============================================
-- 2. EXFILTRATED_MESSAGES TABLE
-- ============================================
-- This table contains display data for the frontend
-- Allow public READ-ONLY access for frontend display
-- Backend uses service_role key for writes (bypasses RLS)

-- Enable RLS
ALTER TABLE exfiltrated_messages ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if any
DROP POLICY IF EXISTS "Public Read Access" ON exfiltrated_messages;
DROP POLICY IF EXISTS "Deny Public Modifications" ON exfiltrated_messages;

-- Policy: Allow public read access (for frontend display)
CREATE POLICY "Public Read Access"
ON exfiltrated_messages
FOR SELECT
TO anon
USING (true);

-- Policy: Deny public write/update/delete
CREATE POLICY "Deny Public Modifications"
ON exfiltrated_messages
FOR INSERT
TO anon
WITH CHECK (false);

CREATE POLICY "Deny Public Updates"
ON exfiltrated_messages
FOR UPDATE
TO anon
USING (false)
WITH CHECK (false);

CREATE POLICY "Deny Public Deletes"
ON exfiltrated_messages
FOR DELETE
TO anon
USING (false);

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
