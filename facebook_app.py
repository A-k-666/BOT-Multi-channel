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
        "recipient_id": recipient_id,
        "message_text": text,  # Facebook toolkit requires "message_text" (not "text")
    }

    logger.debug(
        "Executing FACEBOOK_SEND_MESSAGE with args: %s, org_id=%s, connected_account_id=%s",
        arguments,
        org_id,
        connected_account_id,
    )
    
    try:
        result = composio_client.tools.execute(
            slug="FACEBOOK_SEND_MESSAGE",
            arguments=arguments,
            user_id=org_id,
            connected_account_id=connected_account_id,
            version="latest",
            dangerously_skip_version_check=True,
        )
        return result
    except Exception as e:
        logger.error(f"Exception during tool execution: {type(e).__name__}: {e}")
        # Try to get more details from the exception
        if hasattr(e, 'response'):
            logger.error(f"Exception response: {e.response}")
        if hasattr(e, 'body'):
            logger.error(f"Exception body: {e.body}")
        raise


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
    logger.info("=" * 60)
    logger.info("Facebook Webhook Verification Request Received")
    logger.info("=" * 60)
    logger.info(f"hub.mode: {hub_mode}")
    logger.info(f"hub.verify_token: {hub_verify_token}")
    logger.info(f"hub.challenge: {hub_challenge}")
    logger.info(f"Expected FACEBOOK_VERIFY_TOKEN: {'SET' if FACEBOOK_VERIFY_TOKEN else 'NOT SET'}")
    logger.info(f"Token length: {len(FACEBOOK_VERIFY_TOKEN) if FACEBOOK_VERIFY_TOKEN else 0}")
    
    # Check if verify token is set
    if not FACEBOOK_VERIFY_TOKEN:
        logger.error("FACEBOOK_VERIFY_TOKEN environment variable is not set!")
        raise HTTPException(
            status_code=500, 
            detail="Webhook verification token not configured. Please set FACEBOOK_VERIFY_TOKEN environment variable."
        )
    
    # Check mode
    if hub_mode != "subscribe":
        logger.warning(f"Invalid hub.mode: {hub_mode} (expected 'subscribe')")
        raise HTTPException(status_code=403, detail=f"Invalid mode: {hub_mode}")
    
    # Check token match
    if hub_verify_token != FACEBOOK_VERIFY_TOKEN:
        logger.warning(
            f"Token mismatch! Received: '{hub_verify_token}', Expected: '{FACEBOOK_VERIFY_TOKEN}'"
        )
        logger.warning("Make sure FACEBOOK_VERIFY_TOKEN in Render matches the token in Meta Developer Portal")
        raise HTTPException(status_code=403, detail="Verification token mismatch")
    
    # Success
    logger.info("✅ Webhook verified successfully! Returning challenge.")
    logger.info("=" * 60)
    return PlainTextResponse(hub_challenge)


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

    # Handle only Facebook page events (object: "page")
    # Instagram events are handled by instagram_app.py
    if payload.get("object") != "page":
        logger.info("Skipping non-page event (object: %s). Instagram events should go to /instagram/webhook", payload.get("object"))
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
                # Ensure recipient_id is string (Facebook Graph API requires string)
                recipient_id_str = str(sender_id)
                page_id_str = str(page_id)
                
                logger.info(
                    "Sending Facebook message via Composio: org_id=%s, connected_account_id=%s, page_id=%s, recipient_id=%s",
                    org_id,
                    connected_account_id,
                    page_id_str,
                    recipient_id_str,
                )
                response = send_facebook_message(
                    org_id=org_id,
                    connected_account_id=connected_account_id,
                    page_id=page_id_str,
                    recipient_id=recipient_id_str,
                    text=reply,
                )
                logger.info("Sent response via Composio: %s", response)
                if not response.get("successful"):
                    error_msg = response.get("error", "Unknown error")
                    logger.error(
                        "Composio returned unsuccessful response: %s",
                        error_msg,
                    )
                    # Log full response for debugging
                    logger.error("Full Composio response: %s", json.dumps(response, indent=2))
            except Exception as send_error:
                logger.exception("Failed to send message via Composio: %s", send_error)
                logger.error("Exception details: %s", str(send_error))

    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)

