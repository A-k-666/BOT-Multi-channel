"""Slack event handler that routes mentions to DeepAgent and replies via Composio."""

from __future__ import annotations
import hmac
import json
from collections import deque
import logging
import os
import time
from contextlib import asynccontextmanager
from hashlib import sha256
from typing import Any
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from composio import Composio
from composio_langchain import LangchainProvider
from DeepAgent import run_agent
from supabase_helpers import load_slack_mapping_from_supabase, bulk_upsert_slack_accounts
from scripts.sync_slack_accounts import pick_latest_account

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("slack_app")
logger.setLevel(logging.INFO)
logger.propagate = True
logger.info("Slack app module loaded")

SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_BOT_FALLBACK = os.getenv("SLACK_BOT_USER_ID", "")

# Sync interval in seconds (default: 5 minutes, can be overridden via env)
SYNC_INTERVAL = int(os.getenv("SLACK_SYNC_INTERVAL_SECONDS", "300"))

_processed_event_ids: deque[str] = deque()
_processed_event_index: set[str] = set()

# Cache for Slack account mappings (refreshed periodically)
_slack_account_map_cache: dict[str, Any] = {}
_cache_last_updated: float = 0
_cache_ttl: int = 60  # Cache TTL in seconds


def _is_duplicate(event_id: str) -> bool:
    if event_id in _processed_event_index:
        return True
    _processed_event_ids.append(event_id)
    _processed_event_index.add(event_id)
    while len(_processed_event_ids) > 500:
        old = _processed_event_ids.popleft()
        _processed_event_index.discard(old)
    return False

DEFAULT_RESPONSE_TEXT = os.getenv(
    "SLACK_DEFAULT_RESPONSE",
    "Hi! I'm still connecting. Please try again later.",
)


def get_slack_account_map() -> dict[str, Any]:
    """
    Get Slack account mapping from cache or Supabase.
    Uses caching to avoid frequent database calls.
    """
    global _slack_account_map_cache, _cache_last_updated
    
    current_time = time.time()
    
    # Refresh cache if expired
    if current_time - _cache_last_updated > _cache_ttl:
        try:
            _slack_account_map_cache = load_slack_mapping_from_supabase()
            _cache_last_updated = current_time
            logger.info(f"Refreshed Slack account cache with {len(_slack_account_map_cache)} entries")
        except Exception as e:
            logger.error(f"Failed to load Slack accounts from Supabase: {e}")
            if not _slack_account_map_cache:
                raise RuntimeError(f"Failed to load Slack accounts and cache is empty: {e}")
    
    return _slack_account_map_cache


def sync_slack_accounts_to_supabase() -> None:
    """
    Background task to sync Slack accounts from Composio to Supabase.
    Fetches all active Slackbot accounts and updates Supabase.
    """
    try:
        logger.info("Starting background sync of Slack accounts to Supabase...")
        
        client = Composio(provider=LangchainProvider())
        
        # Fetch all Slackbot accounts (no user_ids filter means fetch all)
        accounts = client.connected_accounts.list(toolkit_slugs=["SLACKBOT"])
        mapping = pick_latest_account(accounts)
        
        if mapping:
            count = bulk_upsert_slack_accounts(mapping)
            logger.info(f"Synced {count} Slack accounts to Supabase")
            
            # Invalidate cache to force refresh on next access
            global _cache_last_updated
            _cache_last_updated = 0
        else:
            logger.info("No new Slack accounts to sync")
    except Exception as e:
        logger.error(f"Background sync failed: {e}", exc_info=True)


# Initialize cache on startup
try:
    _slack_account_map_cache = load_slack_mapping_from_supabase()
    _cache_last_updated = time.time()
    logger.info(f"Loaded {len(_slack_account_map_cache)} Slack accounts from Supabase on startup")
except Exception as e:
    logger.warning(f"Failed to load Slack accounts on startup: {e}. Will retry on first request.")

composio_client = Composio(provider=LangchainProvider())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI.
    Starts background sync task and stops it on shutdown.
    """
    import asyncio
    
    # Start background sync task
    sync_task = asyncio.create_task(background_sync_loop())
    logger.info(f"Started background sync task (interval: {SYNC_INTERVAL}s)")
    
    yield
    
    # Cancel sync task on shutdown
    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        logger.info("Background sync task stopped")


async def background_sync_loop() -> None:
    """Background task that syncs Slack accounts periodically."""
    import asyncio
    
    while True:
        try:
            await asyncio.sleep(SYNC_INTERVAL)
            # Run sync in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, sync_slack_accounts_to_supabase)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Background sync loop error: {e}", exc_info=True)


app = FastAPI(
    title="Composio Slack Bridge",
    lifespan=lifespan
)


def verify_slack_signature(request: Request, raw_body: bytes) -> None:
    if not SLACK_SIGNING_SECRET:
        return
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Missing Slack signature headers")

    if abs(time.time() - int(timestamp)) > 60 * 5:
        raise HTTPException(status_code=401, detail="Slack request timestamp too old")

    base_string = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    my_signature = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(), base_string.encode(), sha256
    ).hexdigest()

    if not hmac.compare_digest(my_signature, signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")


def send_slack_message(
    *,
    org_id: str,
    connected_account_id: str,
    channel: str,
    text: str,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "channel": channel,
        "text": text,
    }
    if thread_ts:
        arguments["thread_ts"] = thread_ts

    return composio_client.tools.execute(
        slug="SLACKBOT_CHAT_POST_MESSAGE",
        arguments=arguments,
        user_id=org_id,
        connected_account_id=connected_account_id,
        version="latest",
        dangerously_skip_version_check=True,
    )


def resolve_workspace(team_id: str) -> tuple[str, str, str]:
    """Resolve workspace details from team_id using Supabase cache."""
    slack_account_map = get_slack_account_map()
    if team_id not in slack_account_map:
        raise HTTPException(status_code=400, detail=f"Unknown team_id {team_id}")
    entry = slack_account_map[team_id]
    bot_user_id = entry.get("bot_user_id") or SLACK_BOT_FALLBACK
    return entry["org_id"], entry["connected_account_id"], bot_user_id


def is_bot_mention(event: dict[str, Any], bot_user_id: str) -> bool:
    event_type = event.get("type")
    if event_type == "app_mention":
        return True
    if event_type == "message":
        text = event.get("text", "")
        return bool(bot_user_id and f"<@{bot_user_id}>" in text)
    return False


@app.post("/slack/events")
async def slack_events(request: Request):
    raw_body = await request.body()
    headers = dict(request.headers)
    logger.info("Incoming headers: %s", headers)
    print("Incoming headers:", headers)
    
    # Parse JSON from raw_body (don't read body twice)
    try:
        payload = json.loads(raw_body.decode('utf-8'))
    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    logger.info("Received payload: %s", payload)
    print("Received payload:", payload)

    # Handle URL verification BEFORE signature verification
    # Slack's URL verification doesn't require signature verification
    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge")
        logger.info("URL verification challenge received: %s", challenge)
        return JSONResponse({"challenge": challenge})
    
    # For all other requests, verify signature
    verify_slack_signature(request, raw_body)

    event = payload.get("event", {})
    if not event:
        return JSONResponse({"ok": True})

    event_id = payload.get("event_id") or event.get("client_msg_id")
    if event_id and _is_duplicate(event_id):
        logger.info("Duplicate event %s detected; ignoring", event_id)
        return JSONResponse({"ok": True})

    team_id = payload.get("team_id") or event.get("team")
    channel = event.get("channel")
    thread_ts = event.get("thread_ts")
    user_text = event.get("text", "").strip()
    user_id = event.get("user")

    if event.get("bot_id"):
        logger.info("Ignoring bot event: %s", event)
        print("Ignoring bot event:", event)
        return JSONResponse({"ok": True})

    if not team_id or not channel:
        logger.warning("Missing team_id/channel in event: %s", event)
        print("Missing team/channel:", event)
        return JSONResponse({"ok": True})

    try:
        org_id, connected_account_id, bot_user_id = resolve_workspace(team_id)
    except HTTPException as exc:
        logger.error("Team lookup failed for %s: %s", team_id, exc.detail)
        print("Lookup failed:", team_id, exc.detail)
        return JSONResponse({"ok": False, "error": exc.detail})

    if bot_user_id and user_id == bot_user_id:
        logger.info("Ignoring self message for bot %s", bot_user_id)
        return JSONResponse({"ok": True})

    if not is_bot_mention(event, bot_user_id):
        logger.info("Event is not a mention: %s", event)
        print("Not a mention:", event)
        return JSONResponse({"ok": True})

    try:
        cleaned_text = user_text
        if bot_user_id:
            cleaned_text = cleaned_text.replace(f"<@{bot_user_id}>", "").strip()
        logger.info("Dispatching to DeepAgent with text: %s", cleaned_text)
        print("Dispatching text:", cleaned_text)
        reply = run_agent(cleaned_text or user_text)
    except Exception as agent_error:  # pragma: no cover
        logger.exception("DeepAgent invocation failed: %s", agent_error)
        print("DeepAgent failed:", agent_error)
        reply = f"{DEFAULT_RESPONSE_TEXT}\n\n(Error: {agent_error})"

    response = send_slack_message(
        org_id=org_id,
        connected_account_id=connected_account_id,
        channel=channel,
        text=reply,
        thread_ts=thread_ts,
    )
    logger.info("Sent response via Composio: %s", response)
    print("Composio response:", response)

    return JSONResponse({"ok": True, "composio": response})

