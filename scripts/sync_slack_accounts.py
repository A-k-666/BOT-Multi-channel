import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from composio import Composio
from composio_langchain import LangchainProvider

# Add parent directory to path to import supabase_helpers
sys.path.insert(0, str(Path(__file__).parent.parent))
from supabase_helpers import bulk_upsert_slack_accounts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync active Slackbot connected accounts to Supabase"
    )
    parser.add_argument(
        "--user-ids",
        help="Comma-separated list of Composio user IDs. "
        "Falls back to env SLACK_COMPOSIO_USER_IDS or COMPOSIO_USER_ID. "
        "If none provided, all Slackbot accounts are considered.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional: Also write to JSON file (for backup/compatibility).",
    )
    parser.add_argument(
        "--supabase-only",
        action="store_true",
        help="Only sync to Supabase, don't write JSON file.",
    )
    return parser.parse_args()


def resolve_user_ids(args: argparse.Namespace) -> list[str] | None:
    raw = args.user_ids or os.getenv("SLACK_COMPOSIO_USER_IDS")
    if raw:
        return [uid.strip() for uid in raw.split(",") if uid.strip()]
    # If no user_ids specified, return None to fetch ALL Slackbot accounts
    return None


def pick_latest_account(accounts):
    buckets = defaultdict(list)
    for acc in accounts.items:
        state = getattr(acc, "state", None)
        val = getattr(state, "val", None) if state else None
        if not val:
            continue
        team = val.get("team") if isinstance(val, dict) else getattr(val, "team", None)
        team_id = team.get("id") if team else None
        if not team_id:
            continue
        if str(getattr(acc, "status", "")).upper() != "ACTIVE":
            continue
        buckets[team_id].append(acc)

    mapping: dict[str, dict[str, str]] = {}
    for team_id, accs in buckets.items():
        latest = max(accs, key=lambda a: getattr(a, "updated_at", ""))  # pick most recent
        bot_user_id = ""
        state = getattr(latest, "state", None)
        val = getattr(state, "val", None) if state else None
        if isinstance(val, dict):
            bot_user_id = val.get("bot_user_id", "") or ""
        elif val is not None:
            bot_user_id = getattr(val, "bot_user_id", "") or ""
        mapping[team_id] = {
            "org_id": getattr(latest, "user_id", ""),
            "connected_account_id": latest.id,
            "auth_config_id": latest.auth_config.id if latest.auth_config else "",
            "bot_user_id": bot_user_id,
        }
    return mapping


def main() -> None:
    args = parse_args()
    load_dotenv()

    client = Composio(provider=LangchainProvider())
    user_ids = resolve_user_ids(args)

    # Fetch all Slackbot accounts (filter by user_ids only if provided)
    list_kwargs = {"toolkit_slugs": ["SLACKBOT"]}
    if user_ids:
        list_kwargs["user_ids"] = user_ids
    
    accounts = client.connected_accounts.list(**list_kwargs)

    mapping = pick_latest_account(accounts)

    if not mapping:
        print("No active Slack accounts found to sync.")
        return

    # Sync to Supabase
    try:
        count = bulk_upsert_slack_accounts(mapping)
        print(f"✅ Synced {count} Slack accounts to Supabase")
    except Exception as e:
        print(f"❌ Failed to sync to Supabase: {e}")
        sys.exit(1)

    # Optionally write to JSON file as backup
    if args.output and not args.supabase_only:
        target = Path(args.output)
        # Merge with existing JSON if it exists
        existing = {}
        if target.exists():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception):
                pass
        
        existing.update(mapping)
        target.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        print(f"✅ Also updated {target} with {len(mapping)} entries (total: {len(existing)} entries).")


if __name__ == "__main__":
    main()
