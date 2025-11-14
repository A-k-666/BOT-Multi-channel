"""Sync active Instagram connected accounts into instagram_accounts.json"""

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from composio import Composio
from composio_langchain import LangchainProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync active Instagram connected accounts into instagram_accounts.json"
    )
    parser.add_argument(
        "--user-ids",
        help="Comma-separated list of Composio user IDs. "
        "Falls back to env INSTAGRAM_COMPOSIO_USER_IDS or COMPOSIO_USER_ID. "
        "If none provided, all Instagram accounts are considered.",
    )
    parser.add_argument(
        "--output",
        default="instagram_accounts.json",
        help="Path of the JSON mapping file (default: instagram_accounts.json).",
    )
    return parser.parse_args()


def resolve_user_ids(args: argparse.Namespace) -> list[str] | None:
    raw = args.user_ids or os.getenv("INSTAGRAM_COMPOSIO_USER_IDS")
    if raw:
        return [uid.strip() for uid in raw.split(",") if uid.strip()]
    # If no user_ids specified, return None to fetch ALL Instagram accounts
    return None


def pick_latest_account(accounts):
    """Pick the latest active account for each Instagram account."""
    buckets = defaultdict(list)
    for acc in accounts.items:
        state = getattr(acc, "state", None)
        val = getattr(state, "val", None) if state else None
        if not val:
            continue
        
        # Extract account_id from state
        account_id = None
        if isinstance(val, dict):
            account_id = val.get("account_id") or val.get("id") or val.get("instagram_business_account_id")
        elif hasattr(val, "account_id"):
            account_id = getattr(val, "account_id", None)
        elif hasattr(val, "id"):
            account_id = getattr(val, "id", None)
        
        if not account_id:
            continue
        
        if str(getattr(acc, "status", "")).upper() != "ACTIVE":
            continue
        
        buckets[account_id].append(acc)

    mapping: dict[str, dict[str, str]] = {}
    for account_id, accs in buckets.items():
        latest = max(accs, key=lambda a: getattr(a, "updated_at", ""))  # pick most recent
        mapping[str(account_id)] = {
            "org_id": getattr(latest, "user_id", ""),
            "connected_account_id": latest.id,
            "auth_config_id": latest.auth_config.id if latest.auth_config else "",
        }
    return mapping


def main() -> None:
    args = parse_args()
    load_dotenv()

    client = Composio(provider=LangchainProvider())
    user_ids = resolve_user_ids(args)

    # Fetch all Instagram accounts (filter by user_ids only if provided)
    list_kwargs = {"toolkit_slugs": ["INSTAGRAM"]}
    if user_ids:
        list_kwargs["user_ids"] = user_ids
    
    accounts = client.connected_accounts.list(**list_kwargs)

    mapping = pick_latest_account(accounts)

    # Merge with existing JSON to preserve any manually added entries
    target = Path(args.output)
    existing = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            pass
    
    # Update existing with new mappings (new entries will be added, existing will be updated)
    existing.update(mapping)
    
    # Write back the merged data
    target.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"Updated {target} with {len(mapping)} entries (total: {len(existing)} entries).")


if __name__ == "__main__":
    main()


