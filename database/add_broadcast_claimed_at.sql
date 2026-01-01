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
-- CLEANUP EXISTING DATA
-- =================================================

-- 3. Clear ALL existing claims to start fresh
UPDATE exfiltrated_messages 
SET broadcast_claimed_at = NULL;

-- 4. OPTIONAL: If you have duplicates in DB (same telegram_msg_id + credential_id)
-- This marks duplicates as broadcasted so they won't be sent again
-- Keep only the oldest row for each unique combo
WITH duplicates AS (
    SELECT id 
    FROM (
        SELECT id, 
               ROW_NUMBER() OVER (
                   PARTITION BY telegram_msg_id, credential_id 
                   ORDER BY created_at ASC
               ) as rn
        FROM exfiltrated_messages
    ) sub
    WHERE rn > 1
)
UPDATE exfiltrated_messages 
SET is_broadcasted = TRUE 
WHERE id IN (SELECT id FROM duplicates);

-- 5. Show current status
SELECT 
    is_broadcasted,
    COUNT(*) as count
FROM exfiltrated_messages
GROUP BY is_broadcasted;
