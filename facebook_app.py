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
from rag_chat_helpers import get_rag_chat_response
import httpx

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("facebook_app")
logger.setLevel(logging.INFO)
logger.propagate = True
logger.info("Facebook app module loaded")

FACEBOOK_VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN", "")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET", "")

# Single page setup - direct from environment variables
# Same connection works for both Facebook page and Instagram (since Instagram is linked to Facebook page)
FACEBOOK_ORG_ID = os.getenv("FACEBOOK_ORG_ID") or os.getenv("COMPOSIO_USER_ID", "")
FACEBOOK_CONNECTED_ACCOUNT_ID = os.getenv("FACEBOOK_CONNECTED_ACCOUNT_ID", "")

# Facebook Page Access Token for direct Graph API calls (for Instagram messages)
# Get this from: Facebook Developer Dashboard → Your App → Messenger → Settings → Access Tokens
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")

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
        "service": "Facebook Messenger & Instagram Bot",
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


def split_message_for_social_media(text: str, max_length: int = 1900) -> list[str]:
    """
    Split a long message into chunks that fit Facebook/Instagram's 2000 character limit.
    Tries to split at sentence boundaries when possible.
    
    Args:
        text: The message text to split
        max_length: Maximum length per chunk (default 1900 to be safe)
    
    Returns:
        List of message chunks
    """
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    current_chunk = ""
    
    # Try to split by sentences first
    sentences = text.split('. ')
    
    for sentence in sentences:
        # If adding this sentence would exceed limit, save current chunk and start new
        if current_chunk and len(current_chunk) + len(sentence) + 2 > max_length:
            chunks.append(current_chunk.strip())
            current_chunk = sentence
        else:
            if current_chunk:
                current_chunk += ". " + sentence
            else:
                current_chunk = sentence
    
    # Add the last chunk
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    # If any chunk is still too long, split by newlines
    final_chunks = []
    for chunk in chunks:
        if len(chunk) <= max_length:
            final_chunks.append(chunk)
        else:
            # Split by newlines
            lines = chunk.split('\n')
            temp_chunk = ""
            for line in lines:
                if len(temp_chunk) + len(line) + 1 > max_length:
                    if temp_chunk:
                        final_chunks.append(temp_chunk.strip())
                    temp_chunk = line
                else:
                    if temp_chunk:
                        temp_chunk += "\n" + line
                    else:
                        temp_chunk = line
            if temp_chunk:
                final_chunks.append(temp_chunk.strip())
    
    # Final fallback: if still too long, hard split
    really_final_chunks = []
    for chunk in final_chunks:
        if len(chunk) <= max_length:
            really_final_chunks.append(chunk)
        else:
            # Hard split at max_length
            for i in range(0, len(chunk), max_length):
                really_final_chunks.append(chunk[i:i + max_length])
    
    return really_final_chunks


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


def send_instagram_message_direct(
    *,
    instagram_account_id: str,
    recipient_id: str,
    text: str,
    access_token: str | None = None,
) -> dict[str, Any]:
    """
    Send Instagram message directly using Facebook Graph API (without Composio).
    Uses Facebook Graph API endpoint: POST /{ig-user-id}/messages
    
    Args:
        instagram_account_id: Instagram Business Account ID (e.g., "17841476750803735")
        recipient_id: Instagram user ID who will receive the message
        text: Message text to send
        access_token: Facebook Page Access Token (if not provided, uses FACEBOOK_PAGE_ACCESS_TOKEN env var)
    
    Returns:
        dict with response from Facebook Graph API
    """
    if not access_token:
        access_token = FACEBOOK_PAGE_ACCESS_TOKEN
    
    if not access_token:
        raise ValueError(
            "Facebook Page Access Token not found. "
            "Set FACEBOOK_PAGE_ACCESS_TOKEN in .env file. "
            "Get it from: Facebook Developer Dashboard → Your App → Messenger → Settings → Access Tokens"
        )
    
    # Facebook Graph API endpoint for Instagram messages
    # Try v18.0 first, fallback to latest if needed
    url = f"https://graph.facebook.com/v18.0/{instagram_account_id}/messages"
    
    # Request payload - Instagram messaging format
    payload = {
        "recipient": json.dumps({"id": recipient_id}),
        "message": json.dumps({"text": text}),
        "access_token": access_token,
    }
    
    try:
        response = httpx.post(url, data=payload, timeout=10.0)
        response.raise_for_status()
        result = response.json()
        logger.info(f"Instagram message sent successfully via Graph API: {result}")
        return {"data": result, "successful": True, "error": None}
    except httpx.HTTPStatusError as e:
        error_data = e.response.json() if e.response else {}
        error_message = error_data.get("error", {}).get("message", str(error_data))
        error_code = error_data.get("error", {}).get("code", "unknown")
        
        logger.error(f"Facebook Graph API error: {e.response.status_code} - {error_message}")
        
        # If error is about permissions, provide helpful message
        if error_code == 3 or "capability" in error_message.lower():
            logger.error(
                "Instagram messaging permission not enabled. "
                "Please enable Instagram messaging in Facebook Developer Dashboard:\n"
                "1. Go to your Facebook App → Products → Instagram\n"
                "2. Enable 'Instagram Messaging' product\n"
                "3. Add 'instagram_basic' and 'instagram_manage_messages' permissions\n"
                "4. Make sure your app has access to Instagram Business Account"
            )
        
        return {"data": {}, "successful": False, "error": error_message}
    except Exception as e:
        logger.error(f"Failed to send Instagram message via Graph API: {e}")
        return {"data": {}, "successful": False, "error": str(e)}


def send_instagram_message(
    *,
    org_id: str,
    connected_account_id: str,
    instagram_account_id: str,
    recipient_id: str,
    text: str,
) -> dict[str, Any]:
    """
    Send Instagram message using direct Facebook Graph API (bypasses Composio).
    This function uses send_instagram_message_direct() which calls Facebook Graph API directly.
    """
    logger.info("Sending Instagram message via direct Facebook Graph API")
    return send_instagram_message_direct(
        instagram_account_id=instagram_account_id,
        recipient_id=recipient_id,
        text=text,
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

    # Handle Instagram events (object: "instagram")
    if payload.get("object") == "instagram":
        return await handle_instagram_webhook(payload)
    
    # Handle Facebook page events (object: "page")
    if payload.get("object") != "page":
        logger.warning("Received unknown event type: %s", payload.get("object"))
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

            # Process message with RAG Super Agent chat API
            try:
                logger.info("Dispatching to RAG chat API with text: %s", message_text)
                reply = await get_rag_chat_response(message_text)
            except Exception as agent_error:
                logger.exception("RAG chat API invocation failed: %s", agent_error)
                reply = f"{DEFAULT_RESPONSE_TEXT}\n\n(Error: {agent_error})"

            # Send reply via Composio (split into chunks if too long)
            try:
                message_chunks = split_message_for_social_media(reply)
                logger.info(f"Facebook message split into {len(message_chunks)} chunks (total length: {len(reply)} chars)")
                
                for i, chunk in enumerate(message_chunks):
                    logger.info(f"Sending Facebook chunk {i+1}/{len(message_chunks)} ({len(chunk)} chars)")
                    response = send_facebook_message(
                        org_id=org_id,
                        connected_account_id=connected_account_id,
                        page_id=page_id,
                        recipient_id=sender_id,
                        text=chunk,
                    )
                    if not response.get("successful"):
                        logger.error(f"Failed to send Facebook chunk {i+1}: {response.get('error')}")
                    else:
                        logger.info(f"✅ Successfully sent Facebook chunk {i+1}/{len(message_chunks)}")
                    
                    # Small delay between chunks to avoid rate limiting
                    if i < len(message_chunks) - 1:
                        import asyncio
                        await asyncio.sleep(0.5)
                
                logger.info("✅ Successfully sent all Facebook response chunks")
            except Exception as send_error:
                logger.exception("Failed to send message via Composio: %s", send_error)

    return JSONResponse({"ok": True})


async def handle_instagram_webhook(payload: dict[str, Any]) -> JSONResponse:
    """
    Handle Instagram webhook events.
    Instagram messages come through Facebook webhook with object: "instagram".
    """
    entries = payload.get("entry", [])
    for entry in entries:
        instagram_account_id = entry.get("id")  # Instagram account ID
        if not instagram_account_id:
            logger.warning("Entry missing Instagram account ID: %s", entry)
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

            sender_id = sender.get("id")  # User who sent the message
            recipient_id = recipient.get("id")  # Instagram account ID
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
                "Received Instagram message from %s to account %s: %s",
                sender_id,
                instagram_account_id,
                message_text,
            )

            # Use same Facebook connection for Instagram (since Instagram is linked to Facebook page)
            try:
                org_id, connected_account_id = resolve_page(instagram_account_id)
            except HTTPException:
                # If Instagram account ID not in mapping, use env vars (single page setup)
                if FACEBOOK_ORG_ID and FACEBOOK_CONNECTED_ACCOUNT_ID:
                    logger.info("Using Facebook connection for Instagram (linked accounts)")
                    org_id = FACEBOOK_ORG_ID
                    connected_account_id = FACEBOOK_CONNECTED_ACCOUNT_ID
                else:
                    logger.error(
                        "Facebook env vars not set. Set FACEBOOK_ORG_ID and FACEBOOK_CONNECTED_ACCOUNT_ID."
                    )
                    continue

            # Process message with RAG Super Agent chat API
            try:
                logger.info("Dispatching to RAG chat API with text: %s", message_text)
                reply = await get_rag_chat_response(message_text)
            except Exception as agent_error:
                logger.exception("RAG chat API invocation failed: %s", agent_error)
                reply = f"{DEFAULT_RESPONSE_TEXT}\n\n(Error: {agent_error})"

            # Send reply via Facebook Graph API (Instagram is linked to Facebook page)
            # Split into chunks if message is too long
            try:
                message_chunks = split_message_for_social_media(reply)
                logger.info(f"Instagram message split into {len(message_chunks)} chunks (total length: {len(reply)} chars)")
                
                for i, chunk in enumerate(message_chunks):
                    logger.info(f"Sending Instagram chunk {i+1}/{len(message_chunks)} ({len(chunk)} chars)")
                    response = send_instagram_message(
                        org_id=org_id,
                        connected_account_id=connected_account_id,
                        instagram_account_id=instagram_account_id,
                        recipient_id=sender_id,
                        text=chunk,
                    )
                    if not response.get("successful"):
                        logger.error(f"Failed to send Instagram chunk {i+1}: {response.get('error')}")
                    else:
                        logger.info(f"✅ Successfully sent Instagram chunk {i+1}/{len(message_chunks)}")
                    
                    # Small delay between chunks to avoid rate limiting
                    if i < len(message_chunks) - 1:
                        import asyncio
                        await asyncio.sleep(0.5)
                
                logger.info("✅ Successfully sent all Instagram response chunks")
            except Exception as send_error:
                logger.exception("Failed to send Instagram message: %s", send_error)

    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)

