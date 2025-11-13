"""Facebook Messenger webhook handler that routes messages to DeepAgent and replies via Composio."""

from __future__ import annotations
import json
from collections import deque
import logging
import os
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from composio import Composio
from composio_langchain import LangchainProvider
from DeepAgent import run_agent

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("facebook_app")
logger.setLevel(logging.INFO)
logger.propagate = True
logger.info("Facebook app module loaded")

FACEBOOK_VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN", "")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET", "")

# Single page setup - direct from environment variables
FACEBOOK_ORG_ID = os.getenv("FACEBOOK_ORG_ID") or os.getenv("COMPOSIO_USER_ID", "")
FACEBOOK_CONNECTED_ACCOUNT_ID = os.getenv("FACEBOOK_CONNECTED_ACCOUNT_ID", "")

_processed_message_ids: deque[str] = deque()
_processed_message_index: set[str] = set()

FACEBOOK_ACCOUNTS_PATH = Path("facebook_accounts.json")
DEFAULT_RESPONSE_TEXT = os.getenv(
    "FACEBOOK_DEFAULT_RESPONSE",
    "Hi! I'm still connecting. Please try again later.",
)


def load_facebook_mapping() -> dict[str, Any]:
    """Load Facebook page_id to Composio account mapping (optional, for multi-page setup)."""
    if not FACEBOOK_ACCOUNTS_PATH.exists():
        return {}
    try:
        with FACEBOOK_ACCOUNTS_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, Exception) as e:
        logger.error("Failed to load facebook_accounts.json: %s", e)
        return {}


facebook_account_map = load_facebook_mapping()

composio_client = Composio(provider=LangchainProvider())

app = FastAPI(title="Composio Facebook Messenger Bridge")


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "Facebook Messenger Bot",
        "webhook_url": "/facebook/webhook",
        "verify_token_set": bool(FACEBOOK_VERIFY_TOKEN),
        "org_id_set": bool(FACEBOOK_ORG_ID),
        "connected_account_id_set": bool(FACEBOOK_CONNECTED_ACCOUNT_ID),
    }


def _is_duplicate(message_id: str) -> bool:
    """Check if message ID was already processed."""
    if message_id in _processed_message_index:
        return True
    _processed_message_ids.append(message_id)
    _processed_message_index.add(message_id)
    while len(_processed_message_ids) > 500:
        old = _processed_message_ids.popleft()
        _processed_message_index.discard(old)
    return False


def send_facebook_message(
    *,
    org_id: str,
    connected_account_id: str,
    page_id: str,
    recipient_id: str,
    text: str,
) -> dict[str, Any]:
    """Send a message via Composio's Facebook toolkit."""
    arguments: dict[str, Any] = {
        "page_id": page_id,
        "recipient_id": recipient_id,  # Fixed: was "recipient"
        "message_text": text,  # Fixed: was "message"
    }

    return composio_client.tools.execute(
        slug="FACEBOOK_SEND_MESSAGE",
        arguments=arguments,
        user_id=org_id,
        connected_account_id=connected_account_id,
        version="latest",
        dangerously_skip_version_check=True,
    )


def resolve_page(page_id: str) -> tuple[str, str]:
    """
    Resolve Facebook page_id to Composio org_id and connected_account_id.
    First checks JSON mapping (for multi-page), then falls back to env vars (single page).
    """
    # Check JSON mapping first (for multi-page setup)
    if facebook_account_map and page_id in facebook_account_map:
        entry = facebook_account_map[page_id]
        return entry["org_id"], entry["connected_account_id"]
    
    # Fallback to environment variables (single page setup)
    if FACEBOOK_ORG_ID and FACEBOOK_CONNECTED_ACCOUNT_ID:
        logger.info("Using environment variables for single page setup")
        return FACEBOOK_ORG_ID, FACEBOOK_CONNECTED_ACCOUNT_ID
    
    # If neither available, raise error
    raise HTTPException(
        status_code=400,
        detail=(
            f"Unknown page_id {page_id}. "
            "Either set FACEBOOK_ORG_ID and FACEBOOK_CONNECTED_ACCOUNT_ID env vars "
            "or add page to facebook_accounts.json"
        ),
    )


@app.get("/facebook/webhook")
async def facebook_webhook_verify(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
):
    """
    Facebook webhook verification endpoint.
    Facebook sends a GET request with hub.mode, hub.verify_token, and hub.challenge.
    """
    logger.info(
        "Webhook verification request: mode=%s, token=%s, challenge=%s",
        hub_mode,
        hub_verify_token,
        hub_challenge,
    )

    if hub_mode == "subscribe" and hub_verify_token == FACEBOOK_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return PlainTextResponse(hub_challenge)
    else:
        logger.warning("Webhook verification failed: invalid token or mode")
        raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/facebook/webhook")
async def facebook_webhook(request: Request):
    """
    Facebook webhook endpoint for receiving events.
    Facebook sends POST requests with message events.
    """
    logger.info("=== POST /facebook/webhook called ===")
    logger.info("Request headers: %s", dict(request.headers))
    
    try:
        raw_body = await request.body()
        logger.info("Raw body length: %d bytes", len(raw_body))
        payload = json.loads(raw_body.decode('utf-8'))
    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON: %s", e)
        logger.error("Raw body: %s", raw_body.decode('utf-8', errors='ignore')[:500])
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info("Received Facebook webhook payload: %s", json.dumps(payload, indent=2))

    # Facebook sends events in a specific format
    if payload.get("object") != "page":
        logger.warning("Received non-page event: %s", payload)
        return JSONResponse({"ok": True})

    entries = payload.get("entry", [])
    for entry in entries:
        page_id = entry.get("id")
        if not page_id:
            logger.warning("Entry missing page ID: %s", entry)
            continue

        messaging_events = entry.get("messaging", [])
        for event in messaging_events:
            # Skip if it's not a message event
            if "message" not in event:
                logger.info("Skipping non-message event: %s", event)
                continue

            message = event.get("message", {})
            sender = event.get("sender", {})
            recipient = event.get("recipient", {})

            sender_id = sender.get("id")
            recipient_id = recipient.get("id")
            message_text = message.get("text", "").strip()
            message_id = message.get("mid")  # Facebook message ID

            # Skip if message is empty or from a page (echo)
            if not message_text or event.get("message", {}).get("is_echo"):
                logger.info("Skipping empty or echo message")
                continue

            # Check for duplicates
            if message_id and _is_duplicate(message_id):
                logger.info("Duplicate message %s detected; ignoring", message_id)
                continue

            logger.info(
                "Received message from %s to page %s: %s",
                sender_id,
                page_id,
                message_text,
            )

            # Resolve page to Composio account
            try:
                org_id, connected_account_id = resolve_page(page_id)
            except HTTPException as exc:
                logger.error("Page lookup failed for %s: %s", page_id, exc.detail)
                continue

            # Process message with DeepAgent
            try:
                logger.info("Dispatching to DeepAgent with text: %s", message_text)
                reply = run_agent(message_text)
            except Exception as agent_error:
                logger.exception("DeepAgent invocation failed: %s", agent_error)
                reply = f"{DEFAULT_RESPONSE_TEXT}\n\n(Error: {agent_error})"

            # Send reply via Composio
            try:
                response = send_facebook_message(
                    org_id=org_id,
                    connected_account_id=connected_account_id,
                    page_id=page_id,
                    recipient_id=sender_id,
                    text=reply,
                )
                logger.info("Sent response via Composio: %s", response)
            except Exception as send_error:
                logger.exception("Failed to send message via Composio: %s", send_error)

    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)

