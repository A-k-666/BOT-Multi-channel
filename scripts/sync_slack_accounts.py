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
        description="Sync active Slackbot connected accounts into slack_accounts.json"
    )
    parser.add_argument(
        "--user-ids",
        help="Comma-separated list of Composio user IDs. "
        "Falls back to env SLACK_COMPOSIO_USER_IDS or COMPOSIO_USER_ID. "
        "If none provided, all Slackbot accounts are considered.",
    )
    parser.add_argument(
        "--output",
        default="slack_accounts.json",
        help="Path of the JSON mapping file (default: slack_accounts.json).",
    )
    return parser.parse_args()


def resolve_user_ids(args: argparse.Namespace) -> list[str] | None:
    raw = args.user_ids or os.getenv("SLACK_COMPOSIO_USER_IDS")
    if raw:
        return [uid.strip() for uid in raw.split(",") if uid.strip()]
    default_uid = os.getenv("COMPOSIO_USER_ID")
    return [default_uid] if default_uid else None


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

    accounts = client.connected_accounts.list(
        toolkit_slugs=["SLACKBOT"],
        user_ids=user_ids,
    )

    mapping = pick_latest_account(accounts)

    target = Path(args.output)
    target.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    print(f"Updated {target} with {len(mapping)} entries.")


if __name__ == "__main__":
    main()
