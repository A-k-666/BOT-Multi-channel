"""Supabase helper functions for Slack accounts management."""

from __future__ import annotations

import os
from typing import Any
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://fksfhzwhywvtctgbeeoq.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZrc2ZoendoeXd2dGN0Z2JlZW9xIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MzExMjc3MCwiZXhwIjoyMDc4Njg4NzcwfQ.ymEvBZP5RzpJS0E8cTxPeJ1ls0x_FCzX3PVj0mC2gck"
)

_table_name = "slack_accounts"


def get_supabase_client() -> Client:
    """Create and return Supabase client."""
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def load_slack_mapping_from_supabase() -> dict[str, dict[str, Any]]:
    """
    Load all Slack account mappings from Supabase.
    Returns a dictionary with team_id as key and account details as value.
    """
    try:
        client = get_supabase_client()
        response = client.table(_table_name).select("*").execute()
        
        mapping: dict[str, dict[str, Any]] = {}
        for row in response.data:
            team_id = row.get("team_id")
            if team_id:
                mapping[team_id] = {
                    "org_id": row.get("org_id", ""),
                    "connected_account_id": row.get("connected_account_id", ""),
                    "auth_config_id": row.get("auth_config_id", ""),
                    "bot_user_id": row.get("bot_user_id"),
                }
        return mapping
    except Exception as e:
        raise RuntimeError(f"Failed to load Slack accounts from Supabase: {e}") from e


def upsert_slack_account(
    team_id: str,
    org_id: str,
    connected_account_id: str,
    auth_config_id: str,
    bot_user_id: str | None = None,
) -> dict[str, Any]:
    """
    Insert or update a Slack account in Supabase.
    Returns the inserted/updated record.
    """
    try:
        client = get_supabase_client()
        data = {
            "team_id": team_id,
            "org_id": org_id,
            "connected_account_id": connected_account_id,
            "auth_config_id": auth_config_id,
        }
        if bot_user_id:
            data["bot_user_id"] = bot_user_id
        
        response = client.table(_table_name).upsert(
            data,
            on_conflict="team_id"
        ).execute()
        
        if response.data:
            return response.data[0] if isinstance(response.data, list) else response.data
        return {}
    except Exception as e:
        raise RuntimeError(f"Failed to upsert Slack account in Supabase: {e}") from e


def bulk_upsert_slack_accounts(
    accounts: dict[str, dict[str, Any]]
) -> int:
    """
    Bulk insert/update multiple Slack accounts.
    Input format: {team_id: {org_id, connected_account_id, auth_config_id, bot_user_id}}
    Returns the number of accounts upserted.
    """
    try:
        client = get_supabase_client()
        data_list = []
        
        for team_id, details in accounts.items():
            record = {
                "team_id": team_id,
                "org_id": details.get("org_id", ""),
                "connected_account_id": details.get("connected_account_id", ""),
                "auth_config_id": details.get("auth_config_id", ""),
            }
            if details.get("bot_user_id"):
                record["bot_user_id"] = details["bot_user_id"]
            data_list.append(record)
        
        if not data_list:
            return 0
        
        response = client.table(_table_name).upsert(
            data_list,
            on_conflict="team_id"
        ).execute()
        
        return len(data_list)
    except Exception as e:
        raise RuntimeError(f"Failed to bulk upsert Slack accounts in Supabase: {e}") from e


def get_slack_account(team_id: str) -> dict[str, Any] | None:
    """Get a single Slack account by team_id."""
    try:
        client = get_supabase_client()
        response = client.table(_table_name).select("*").eq("team_id", team_id).execute()
        
        if response.data:
            row = response.data[0]
            return {
                "team_id": row.get("team_id"),
                "org_id": row.get("org_id", ""),
                "connected_account_id": row.get("connected_account_id", ""),
                "auth_config_id": row.get("auth_config_id", ""),
                "bot_user_id": row.get("bot_user_id"),
            }
        return None
    except Exception as e:
        raise RuntimeError(f"Failed to get Slack account from Supabase: {e}") from e


def delete_slack_account(team_id: str) -> bool:
    """Delete a Slack account by team_id."""
    try:
        client = get_supabase_client()
        response = client.table(_table_name).delete().eq("team_id", team_id).execute()
        return True
    except Exception as e:
        raise RuntimeError(f"Failed to delete Slack account from Supabase: {e}") from e

