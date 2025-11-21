"""Script to sync all Instagram connected accounts from Composio to instagram_accounts.json"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from composio import Composio

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync active Instagram connected accounts to instagram_accounts.json"
    )
    parser.add_argument(
        "--user-ids",
        help="Comma-separated list of Composio user IDs. "
        "Falls back to env COMPOSIO_USER_ID. "
        "If none provided, all Instagram accounts are considered.",
    )
    parser.add_argument(
        "--output",
        default="instagram_accounts.json",
        help="Output JSON file path (default: instagram_accounts.json)",
    )
    parser.add_argument(
        "--auth-config-id",
        default="ac_KR6rNOI6h2ge",
        help="Instagram Auth Config ID (default: ac_KR6rNOI6h2ge)",
    )
    return parser.parse_args()


def resolve_user_ids(args: argparse.Namespace) -> list[str] | None:
    raw = args.user_ids or os.getenv("COMPOSIO_USER_ID")
    if raw:
        return [uid.strip() for uid in raw.split(",") if uid.strip()]
    return None


def get_instagram_business_account_id_from_account(account) -> str | None:
    """
    Try to extract Instagram Business Account ID from Composio account.
    This might be in metadata, state, or config.
    """
    # Try different possible locations
    state = getattr(account, "state", None)
    if state:
        val = getattr(state, "val", None)
        if isinstance(val, dict):
            # Check common fields
            for key in ["instagram_business_account_id", "ig_id", "business_account_id", "id"]:
                if key in val:
                    return str(val[key])
    
    # Check metadata
    metadata = getattr(account, "metadata", None)
    if metadata:
        if isinstance(metadata, dict):
            for key in ["instagram_business_account_id", "ig_id", "business_account_id"]:
                if key in metadata:
                    return str(metadata[key])
    
    # Check config
    config = getattr(account, "config", None)
    if config:
        if isinstance(config, dict):
            for key in ["instagram_business_account_id", "ig_id", "business_account_id"]:
                if key in config:
                    return str(config[key])
    
    return None


def main() -> None:
    args = parse_args()
    load_dotenv()

    client = Composio()
    user_ids = resolve_user_ids(args)

    # Fetch all Instagram accounts
    list_kwargs = {
        "toolkit_slugs": ["INSTAGRAM"],
        "auth_config_ids": [args.auth_config_id],
    }
    if user_ids:
        list_kwargs["user_ids"] = user_ids
    
    print(f"Fetching Instagram connected accounts...")
    accounts = client.connected_accounts.list(**list_kwargs)

    mapping: dict[str, dict[str, str]] = {}
    
    for account in accounts.items:
        # Only process ACTIVE accounts
        status = str(getattr(account, "status", "")).upper()
        if status != "ACTIVE":
            print(f"[SKIP] Skipping inactive account: {account.id} (status: {status})")
            continue
        
        org_id = getattr(account, "user_id", "")
        connected_account_id = account.id
        
        # Try to get Instagram Business Account ID
        ig_business_account_id = get_instagram_business_account_id_from_account(account)
        
        if not ig_business_account_id:
            print(f"[WARN] Account {connected_account_id} has no Instagram Business Account ID in metadata.")
            print(f"       This account will be added but you'll need to manually add the Instagram Business Account ID.")
            print(f"       The ID will be populated automatically when webhooks are received.")
            # Use a placeholder or skip - actually, let's skip for now and let webhooks populate it
            # Or we could use the connected_account_id as a temporary key
            continue
        
        mapping[ig_business_account_id] = {
            "org_id": org_id,
            "connected_account_id": connected_account_id,
        }
        print(f"[OK] Found account: IG ID {ig_business_account_id} -> {connected_account_id}")

    if not mapping:
        print("[WARN] No active Instagram accounts found with Instagram Business Account IDs.")
        print("       Accounts will be added automatically when webhooks are received.")
        return

    # Load existing JSON and merge
    target = Path(args.output)
    existing = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            print(f"[INFO] Loaded {len(existing)} existing entries from {target}")
        except (json.JSONDecodeError, Exception) as e:
            print(f"[WARN] Could not load existing JSON: {e}")
    
    # Merge: existing entries take precedence (don't overwrite manually added entries)
    # But update org_id and connected_account_id if they changed
    for ig_id, account_data in mapping.items():
        if ig_id in existing:
            # Update org_id and connected_account_id if they exist in new data
            existing[ig_id].update({
                "org_id": account_data["org_id"],
                "connected_account_id": account_data["connected_account_id"],
            })
        else:
            existing[ig_id] = account_data
    
    # Write back
    target.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"[OK] Updated {target} with {len(mapping)} entries (total: {len(existing)} entries).")


if __name__ == "__main__":
    main()

