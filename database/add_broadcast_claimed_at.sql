-- Add broadcast_claimed_at column to exfiltrated_messages table
-- This column is used for cross-environment duplicate prevention
-- (works whether running from Railway, local Docker, or local script)

ALTER TABLE exfiltrated_messages 
ADD COLUMN IF NOT EXISTS broadcast_claimed_at TIMESTAMPTZ DEFAULT NULL;

-- Add an index to speed up queries for unclaimed messages
CREATE INDEX IF NOT EXISTS idx_exfiltrated_messages_claimed 
ON exfiltrated_messages (is_broadcasted, broadcast_claimed_at);

-- Clear any existing stale claims (in case of interrupted migrations)
UPDATE exfiltrated_messages 
SET broadcast_claimed_at = NULL 
WHERE broadcast_claimed_at IS NOT NULL 
AND is_broadcasted = FALSE;
