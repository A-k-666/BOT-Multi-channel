"""
Create Supabase table using database connection string.
Requires DATABASE_URL in .env file.
"""

import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")


def main():
    if not DATABASE_URL:
        print("[ERROR] DATABASE_URL not found in .env file")
        print("\n[INFO] Add to .env file:")
        print("DATABASE_URL=postgresql://postgres:[PASSWORD]@[HOST]:5432/postgres")
        print("\n[INFO] Get connection string from:")
        print("Supabase Dashboard > Settings > Database > Connection string")
        return False
    
    try:
        import psycopg2
        from psycopg2 import sql
        
        print("[INFO] Connecting to database...")
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        print("[INFO] Creating table and indexes...")
        
        # Read SQL from file if exists, else use inline
        sql_file = "supabase_schema.sql"
        if os.path.exists(sql_file):
            with open(sql_file, 'r', encoding='utf-8') as f:
                sql_content = f.read()
        else:
            sql_content = """
CREATE TABLE IF NOT EXISTS slack_accounts (
    team_id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    connected_account_id TEXT NOT NULL,
    auth_config_id TEXT NOT NULL,
    bot_user_id TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_slack_accounts_org_id ON slack_accounts(org_id);
CREATE INDEX IF NOT EXISTS idx_slack_accounts_connected_account_id ON slack_accounts(connected_account_id);

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_slack_accounts_updated_at ON slack_accounts;
CREATE TRIGGER update_slack_accounts_updated_at
    BEFORE UPDATE ON slack_accounts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""
        
        # Execute SQL
        cursor.execute(sql_content)
        conn.commit()
        
        print("[SUCCESS] Table 'slack_accounts' created successfully!")
        print("[SUCCESS] Indexes created!")
        print("[SUCCESS] Trigger created!")
        
        # Verify table exists
        cursor.execute("""
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = 'slack_accounts'
        """)
        exists = cursor.fetchone()[0] > 0
        
        if exists:
            print("\n[VERIFIED] Table exists in database!")
        
        cursor.close()
        conn.close()
        
        print("\n[INFO] Now you can run: python migrate_json_to_supabase.py")
        return True
        
    except ImportError:
        print("[ERROR] psycopg2 not installed")
        print("[INFO] Install with: pip install psycopg2-binary")
        return False
    except psycopg2.OperationalError as e:
        print(f"[ERROR] Connection failed: {e}")
        print("[INFO] Check your DATABASE_URL is correct")
        return False
    except Exception as e:
        print(f"[ERROR] Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("=" * 70)
    print("Supabase Table Creation (via Database Connection)")
    print("=" * 70)
    print()
    success = main()
    if not success:
        print("\n" + "=" * 70)
        print("ALTERNATIVE: Ask friend to run SQL manually")
        print("See QUICK_SETUP.md for instructions")
        print("=" * 70)

