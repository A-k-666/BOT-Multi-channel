-- Supabase table schema for Slack accounts
-- Run this in your Supabase SQL editor

CREATE TABLE IF NOT EXISTS slack_accounts (
    team_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    connected_account_id TEXT NOT NULL,
    auth_config_id TEXT NOT NULL,
    bot_user_id TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_slack_accounts_org_id ON slack_accounts(org_id);
CREATE INDEX IF NOT EXISTS idx_slack_accounts_connected_account_id ON slack_accounts(connected_account_id);

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to auto-update updated_at
CREATE TRIGGER update_slack_accounts_updated_at
    BEFORE UPDATE ON slack_accounts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Enable Row Level Security (optional - you can disable if needed)
ALTER TABLE slack_accounts ENABLE ROW LEVEL SECURITY;

-- Policy to allow service role to do everything (adjust based on your needs)
CREATE POLICY "Service role can manage slack_accounts"
    ON slack_accounts
    FOR ALL
    USING (true)
    WITH CHECK (true);

