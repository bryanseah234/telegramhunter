-- ============================================
-- FIX RLS FOR FRONTEND DISPLAY
-- ============================================

-- 1. Enable RLS on discovered_credentials (just in case)
ALTER TABLE discovered_credentials ENABLE ROW LEVEL SECURITY;

-- 2. Drop existing conflicting policies to avoid errors or duplicates
DROP POLICY IF EXISTS "Allow Public Reads" ON discovered_credentials;
DROP POLICY IF EXISTS "Public Read Access" ON discovered_credentials;
DROP POLICY IF EXISTS "Deny Public Reads" ON discovered_credentials;

-- 3. Create the permissive policy for the sidebar
-- This allows the frontend (anon role) to read credential metadata
-- The frontend is responsible for NOT selecting sensitive columns (like bot_token)
-- but RLS is row-level, not column-level usually (unless using views).
-- Since we need to join, we allow access to the rows.
CREATE POLICY "Allow Public Reads"
ON discovered_credentials
FOR SELECT
TO anon
USING (true);

-- 4. Verify the policy creation (Optional, for output)
SELECT * FROM pg_policies WHERE tablename = 'discovered_credentials';
