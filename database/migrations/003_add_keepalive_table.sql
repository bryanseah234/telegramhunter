-- ============================================================
-- Migration: Add Keepalive Table
-- Purpose: Lightweight table for GitHub Actions to ping and prevent pause
-- Date: 2025-05-05
-- Safe to re-run: uses IF NOT EXISTS
-- ============================================================

-- Create keepalive_logs table
CREATE TABLE IF NOT EXISTS keepalive_logs (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    pinged_at   TIMESTAMPTZ DEFAULT NOW(),
    source      TEXT        DEFAULT 'github-actions'
);

CREATE INDEX IF NOT EXISTS idx_keepalive_pinged_at ON keepalive_logs(pinged_at);

-- Enable RLS
ALTER TABLE keepalive_logs ENABLE ROW LEVEL SECURITY;

-- Allow anonymous access for keepalive pings
CREATE POLICY "Allow keepalive for anon"
    ON keepalive_logs
    FOR ALL
    TO anon
    USING (true)
    WITH CHECK (true);

-- Explicit grants
GRANT SELECT ON keepalive_logs TO anon;
GRANT INSERT ON keepalive_logs TO anon;
