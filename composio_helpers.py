"""Utility helpers for integrating Composio Telegram tools with DeepAgent."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Sequence

from dotenv import load_dotenv
from composio import Composio
from composio.core.models.connected_accounts import ConnectionRequest, auth_scheme
from composio_langchain import LangchainProvider
from langchain_core.tools import BaseTool

load_dotenv()

COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY")
if not COMPOSIO_API_KEY:
    raise RuntimeError("COMPOSIO_API_KEY not set in environment variables or .env file.")

COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID")
if not COMPOSIO_USER_ID:
    raise RuntimeError("COMPOSIO_USER_ID not set in environment variables or .env file.")

TELEGRAM_AUTH_CONFIG_ID = os.getenv("COMPOSIO_TELEGRAM_AUTH_CONFIG_ID", "ac_QyTN_qte45U9")
TELEGRAM_BOT_TOKEN = os.getenv("COMPOSIO_TELEGRAM_BOT_TOKEN")
TELEGRAM_CONNECTED_ACCOUNT_ID = os.getenv("COMPOSIO_TELEGRAM_CONNECTED_ACCOUNT_ID")

DEFAULT_TELEGRAM_TOOLS: tuple[str, ...] = ("TELEGRAM_SEND_MESSAGE",)


@lru_cache(maxsize=1)
def get_composio_client() -> Composio:
    """Instantiate a Composio SDK client configured for LangChain providers."""
    return Composio(api_key=COMPOSIO_API_KEY, provider=LangchainProvider())


def initiate_telegram_link(callback_url: str | None = None) -> ConnectionRequest:
    """
    Start the linking flow for the Telegram toolkit.

    For API-key based auth configs, you can skip this and call
    `create_telegram_api_key_connection` instead.
    """
    client = get_composio_client()
    return client.connected_accounts.link(
        user_id=COMPOSIO_USER_ID,
        auth_config_id=TELEGRAM_AUTH_CONFIG_ID,
        callback_url=callback_url,
    )


def create_telegram_api_key_connection(
    *,
    api_key: str | None = None,
    allow_multiple: bool = False,
    wait_timeout: float | None = 10.0,
) -> str:
    """
    Create (or replace) a Telegram connection using an API key.

    Returns the connected-account ID once the connection becomes active.
    """
    client = get_composio_client()
    token = api_key or TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError(
            "Telegram bot token not provided. Set COMPOSIO_TELEGRAM_BOT_TOKEN or pass api_key."
        )

    connection_request = client.connected_accounts.initiate(
        user_id=COMPOSIO_USER_ID,
        auth_config_id=TELEGRAM_AUTH_CONFIG_ID,
        allow_multiple=allow_multiple,
        config=auth_scheme.api_key(
            {
                "status": "ACTIVE",
                "api_key": token,
                "generic_api_key": token,
            }
        ),
    )
    connected_account = connection_request.wait_for_connection(timeout=wait_timeout)
    return connected_account.id


@lru_cache(maxsize=1)
def get_default_connected_account_id() -> str:
    if TELEGRAM_CONNECTED_ACCOUNT_ID:
        return TELEGRAM_CONNECTED_ACCOUNT_ID

    client = get_composio_client()
    accounts = client.connected_accounts.list(
        user_ids=[COMPOSIO_USER_ID],
        auth_config_ids=[TELEGRAM_AUTH_CONFIG_ID],
    )
    for account in accounts.items:
        status: str | None = getattr(account, "status", None)
        if status and status.upper() == "ACTIVE":
            return account.id
    raise RuntimeError(
        "No active Telegram connected account found. "
        "Create one via create_telegram_api_key_connection() or set "
        "COMPOSIO_TELEGRAM_CONNECTED_ACCOUNT_ID."
    )


def get_telegram_tools(
    *,
    tool_slugs: Sequence[str] | None = None,
    user_id: str | None = None,
) -> list[BaseTool]:
    """
    Fetch Telegram toolkit tools wrapped for LangChain/DeepAgent consumption.
    """
    client = get_composio_client()
    requested_tools = list(tool_slugs) if tool_slugs else list(DEFAULT_TELEGRAM_TOOLS)
    tools = client.tools.get(
        user_id=user_id or COMPOSIO_USER_ID,
        tools=requested_tools,
    )
    # `tools` can be a single tool or a list depending on provider implementation.
    if isinstance(tools, BaseTool):
        return [tools]
    return list(tools)


def get_default_composio_tools() -> list[BaseTool]:
    """
    Convenience helper to pull the default Telegram toolset.
    """
    return get_telegram_tools()


def send_telegram_message_via_composio(
    *,
    chat_id: int | str,
    text: str,
    user_id: str | None = None,
    connected_account_id: str | None = None,
    extra_arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute the TELEGRAM_SEND_MESSAGE tool through Composio.
    """
    client = get_composio_client()
    resolved_user_id = user_id or COMPOSIO_USER_ID
    resolved_account_id = connected_account_id or get_default_connected_account_id()
    arguments: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if extra_arguments:
        arguments.update(extra_arguments)

    result = client.tools.execute(
        slug="TELEGRAM_SEND_MESSAGE",
        arguments=arguments,
        user_id=resolved_user_id,
        connected_account_id=resolved_account_id,
        version="latest",
        dangerously_skip_version_check=True,
    )
    return result


def get_telegram_updates_via_composio(
    *,
    offset: int | None = None,
    limit: int | None = None,
    timeout: int | None = None,
    allowed_updates: list[str] | None = None,
    user_id: str | None = None,
    connected_account_id: str | None = None,
) -> dict[str, Any]:
    """
    Fetch updates from Telegram via Composio.
    """
    client = get_composio_client()
    resolved_user_id = user_id or COMPOSIO_USER_ID
    resolved_account_id = connected_account_id or get_default_connected_account_id()

    arguments: dict[str, Any] = {}
    if offset is not None:
        arguments["offset"] = offset
    if limit is not None:
        arguments["limit"] = limit
    if timeout is not None:
        arguments["timeout"] = timeout
    if allowed_updates is not None:
        arguments["allowed_updates"] = allowed_updates

    result = client.tools.execute(
        slug="TELEGRAM_GET_UPDATES",
        arguments=arguments,
        user_id=resolved_user_id,
        connected_account_id=resolved_account_id,
        version="latest",
        dangerously_skip_version_check=True,
    )
    return result


__all__ = [
    "get_composio_client",
    "initiate_telegram_link",
    "create_telegram_api_key_connection",
    "get_telegram_tools",
    "get_default_composio_tools",
    "get_default_connected_account_id",
    "send_telegram_message_via_composio",
    "get_telegram_updates_via_composio",
]

