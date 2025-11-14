"""
Migration script to transfer existing slack_accounts.json data to Supabase.
Run this once to migrate your existing data.
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv
from supabase_helpers import bulk_upsert_slack_accounts, load_slack_mapping_from_supabase

load_dotenv()


def main():
    json_file = Path("slack_accounts.json")
    
    if not json_file.exists():
        print("[ERROR] slack_accounts.json file not found!")
        return
    
    print(f"[INFO] Reading {json_file}...")
    try:
        with json_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to read JSON file: {e}")
        return
    
    if not data:
        print("[WARN] JSON file is empty. Nothing to migrate.")
        return
    
    print(f"[SUCCESS] Found {len(data)} team entries in JSON")
    print("\n[INFO] Data preview:")
    for team_id, details in list(data.items())[:3]:
        print(f"  - {team_id}: org_id={details.get('org_id')}, bot_user_id={details.get('bot_user_id')}")
    if len(data) > 3:
        print(f"  ... and {len(data) - 3} more")
    
    print("\n[INFO] Migrating to Supabase...")
    try:
        count = bulk_upsert_slack_accounts(data)
        print(f"[SUCCESS] Successfully migrated {count} accounts to Supabase!")
        
        # Verify migration
        print("\n[INFO] Verifying migration...")
        supabase_data = load_slack_mapping_from_supabase()
        print(f"[SUCCESS] Verified: {len(supabase_data)} accounts in Supabase")
        
        # Check for missing entries
        missing = set(data.keys()) - set(supabase_data.keys())
        if missing:
            print(f"[WARN] Warning: {len(missing)} entries not found in Supabase: {missing}")
        else:
            print("[SUCCESS] All entries successfully migrated!")
            
    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print("\n[SUCCESS] Migration complete!")
    print("\n[TIP] You can now delete slack_accounts.json if you want (backup recommended)")


if __name__ == "__main__":
    main()

