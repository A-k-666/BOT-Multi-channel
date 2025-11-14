"""Instagram Direct Messages webhook handler that routes messages to DeepAgent and replies via Composio."""

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
logger = logging.getLogger("instagram_app")
logger.setLevel(logging.INFO)
logger.propagate = True
logger.info("Instagram app module loaded")

INSTAGRAM_VERIFY_TOKEN = os.getenv("INSTAGRAM_VERIFY_TOKEN", "")

# Single account setup - direct from environment variables
INSTAGRAM_ORG_ID = os.getenv("INSTAGRAM_ORG_ID") or os.getenv("COMPOSIO_USER_ID", "")
INSTAGRAM_CONNECTED_ACCOUNT_ID = os.getenv("INSTAGRAM_CONNECTED_ACCOUNT_ID", "")

_processed_message_ids: deque[str] = deque()
_processed_message_index: set[str] = set()

DEFAULT_RESPONSE_TEXT = os.getenv(
    "INSTAGRAM_DEFAULT_RESPONSE",
    "Hi! I'm still connecting. Please try again later.",
)

composio_client = Composio(provider=LangchainProvider())

app = FastAPI(title="Composio Instagram Direct Messages Bridge")


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "Instagram Direct Messages Bot",
        "webhook_url": "/instagram/webhook",
        "verify_token_set": bool(INSTAGRAM_VERIFY_TOKEN),
        "org_id_set": bool(INSTAGRAM_ORG_ID),
        "connected_account_id_set": bool(INSTAGRAM_CONNECTED_ACCOUNT_ID),
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


def send_instagram_message(
    *,
    org_id: str,
    connected_account_id: str,
    recipient_id: str,
    text: str,
) -> dict[str, Any]:
    """Send a message via Composio's Instagram toolkit."""
    arguments: dict[str, Any] = {
        "recipient_id": recipient_id,
        "message_text": text,
    }

    return composio_client.tools.execute(
        slug="INSTAGRAM_SEND_MESSAGE",
        arguments=arguments,
        user_id=org_id,
        connected_account_id=connected_account_id,
        version="latest",
        dangerously_skip_version_check=True,
    )


def get_composio_account() -> tuple[str, str]:
    """
    Get Composio org_id and connected_account_id from environment variables.
    Single account setup - no JSON needed.
    """
    if INSTAGRAM_ORG_ID and INSTAGRAM_CONNECTED_ACCOUNT_ID:
        return INSTAGRAM_ORG_ID, INSTAGRAM_CONNECTED_ACCOUNT_ID
    
    raise HTTPException(
        status_code=400,
        detail=(
            "Set INSTAGRAM_ORG_ID and INSTAGRAM_CONNECTED_ACCOUNT_ID env vars. "
            "Get connected_account_id by connecting Instagram account via Composio OAuth link."
        ),
    )


@app.get("/instagram/webhook")
async def instagram_webhook_verify(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
):
    """
    Instagram webhook verification endpoint.
    Instagram sends a GET request with hub.mode, hub.verify_token, and hub.challenge.
    """
    logger.info(
        "Webhook verification request: mode=%s, token=%s, challenge=%s",
        hub_mode,
        hub_verify_token,
        hub_challenge,
    )

    if hub_mode == "subscribe" and hub_verify_token == INSTAGRAM_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return PlainTextResponse(hub_challenge)
    else:
        logger.warning("Webhook verification failed: invalid token or mode")
        raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/instagram/webhook")
async def instagram_webhook(request: Request):
    """
    Instagram webhook endpoint for receiving events.
    Instagram sends POST requests with message events.
    """
    logger.info("=== POST /instagram/webhook called ===")
    logger.info("Request headers: %s", dict(request.headers))
    
    try:
        raw_body = await request.body()
        logger.info("Raw body length: %d bytes", len(raw_body))
        payload = json.loads(raw_body.decode('utf-8'))
    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON: %s", e)
        logger.error("Raw body: %s", raw_body.decode('utf-8', errors='ignore')[:500])
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info("Received Instagram webhook payload: %s", json.dumps(payload, indent=2))

    # Instagram sends events in a specific format
    # Instagram webhook format might be different, need to check actual payload structure
    # For now, handling common Instagram webhook formats
    
    # Check if it's an Instagram messaging event
    entry = payload.get("entry", [])
    if not entry:
        logger.warning("No entry in payload: %s", payload)
        return JSONResponse({"ok": True})

    for entry_item in entry:
        # Instagram webhook structure may vary
        # Common fields: messaging, changes, etc.
        messaging = entry_item.get("messaging", [])
        changes = entry_item.get("changes", [])
        
        # Handle messaging events (Direct Messages)
        for event in messaging:
            if "message" not in event:
                logger.info("Skipping non-message event: %s", event)
                continue

            message = event.get("message", {})
            sender = event.get("sender", {})
            recipient = event.get("recipient", {})

            sender_id = sender.get("id")
            recipient_id = recipient.get("id")
            message_text = message.get("text", "").strip()
            message_id = message.get("mid")  # Instagram message ID

            # Skip if message is empty or from a page (echo)
            if not message_text or message.get("is_echo"):
                logger.info("Skipping empty or echo message")
                continue

            # Check for duplicates
            if message_id and _is_duplicate(message_id):
                logger.info("Duplicate message %s detected; ignoring", message_id)
                continue

            logger.info(
                "Received message from %s: %s",
                sender_id,
                message_text,
            )

            # Get Composio account (single account setup)
            try:
                org_id, connected_account_id = get_composio_account()
            except HTTPException as exc:
                logger.error("Composio account lookup failed: %s", exc.detail)
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
                response = send_instagram_message(
                    org_id=org_id,
                    connected_account_id=connected_account_id,
                    recipient_id=sender_id,
                    text=reply,
                )
                logger.info("Sent response via Composio: %s", response)
            except Exception as send_error:
                logger.exception("Failed to send message via Composio: %s", send_error)

        # Handle changes events (if any)
        for change in changes:
            field = change.get("field")
            value = change.get("value", {})
            
            # Handle messaging field changes
            if field == "messages":
                # Instagram messaging webhook format
                message_data = value.get("message", {})
                if message_data:
                    sender_id = message_data.get("from", {}).get("id")
                    message_text = message_data.get("text", "").strip()
                    message_id = message_data.get("id")
                    
                    if not message_text or not sender_id:
                        continue
                    
                    if message_id and _is_duplicate(message_id):
                        logger.info("Duplicate message %s detected; ignoring", message_id)
                        continue
                    
                    logger.info(
                        "Received message from %s: %s",
                        sender_id,
                        message_text,
                    )
                    
                    # Get Composio account (single account setup)
                    try:
                        org_id, connected_account_id = get_composio_account()
                    except HTTPException as exc:
                        logger.error("Composio account lookup failed: %s", exc.detail)
                        continue
                    
                    # Process with DeepAgent
                    try:
                        logger.info("Dispatching to DeepAgent with text: %s", message_text)
                        reply = run_agent(message_text)
                    except Exception as agent_error:
                        logger.exception("DeepAgent invocation failed: %s", agent_error)
                        reply = f"{DEFAULT_RESPONSE_TEXT}\n\n(Error: {agent_error})"
                    
                    # Send reply
                    try:
                        response = send_instagram_message(
                            org_id=org_id,
                            connected_account_id=connected_account_id,
                            recipient_id=sender_id,
                            text=reply,
                        )
                        logger.info("Sent response via Composio: %s", response)
                    except Exception as send_error:
                        logger.exception("Failed to send message via Composio: %s", send_error)

    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)

