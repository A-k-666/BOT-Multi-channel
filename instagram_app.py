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
from rag_chat_helpers import get_rag_chat_response

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


def split_message_for_instagram(text: str, max_length: int = 1900) -> list[str]:
    """
    Split a long message into chunks that fit Instagram's 2000 character limit.
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


def send_instagram_message(
    *,
    org_id: str,
    connected_account_id: str,
    recipient_id: str,
    text: str,
) -> dict[str, Any]:
    """Send a message via Composio's Instagram toolkit."""
    arguments: dict[str, Any] = {
        "recipient_id": str(recipient_id),  # Ensure it's a string
        "text": str(text),  # Ensure it's a string
    }

    logger.info(
        f"Executing INSTAGRAM_SEND_TEXT_MESSAGE with args: {arguments}, "
        f"org_id={org_id}, connected_account_id={connected_account_id}"
    )
    
    try:
        result = composio_client.tools.execute(
            slug="INSTAGRAM_SEND_TEXT_MESSAGE",
            arguments=arguments,
            user_id=org_id,
            connected_account_id=connected_account_id,
            version="latest",
            dangerously_skip_version_check=True,
        )
        logger.info(f"Composio tool execution result: {json.dumps(result, indent=2)}")
        return result
    except Exception as e:
        logger.error(f"Exception during tool execution: {type(e).__name__}: {e}")
        # Try to get more details from the exception
        if hasattr(e, 'response'):
            logger.error(f"Exception response: {e.response}")
        if hasattr(e, 'body'):
            logger.error(f"Exception body: {e.body}")
        raise


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
        
        logger.info(f"Processing entry: messaging events={len(messaging)}, changes={len(changes)}")
        
        # Handle messaging events (Direct Messages)
        for event in messaging:
            logger.info(f"Processing messaging event: {json.dumps(event, indent=2)}")
            
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
            is_echo = message.get("is_echo", False)

            logger.info(
                f"Message details: sender_id={sender_id}, recipient_id={recipient_id}, "
                f"text='{message_text}', message_id={message_id}, is_echo={is_echo}"
            )

            # Skip if message is empty or from a page (echo)
            if not message_text:
                logger.info("Skipping empty message")
                continue
                
            if is_echo:
                logger.info("Skipping echo message (sent by page itself)")
                continue

            # Check for duplicates
            if message_id and _is_duplicate(message_id):
                logger.info("Duplicate message %s detected; ignoring", message_id)
                continue

            logger.info(
                "✅ Processing user message from %s: %s",
                sender_id,
                message_text,
            )

            # Get Composio account (single account setup)
            try:
                org_id, connected_account_id = get_composio_account()
                logger.info(f"Using Composio account: org_id={org_id}, connected_account_id={connected_account_id}")
            except HTTPException as exc:
                logger.error("Composio account lookup failed: %s", exc.detail)
                continue

            # Process message with RAG Super Agent chat API
            try:
                logger.info("Dispatching to RAG chat API with text: %s", message_text)
                reply = await get_rag_chat_response(message_text)
                logger.info("RAG chat API reply: %s", reply)
            except Exception as agent_error:
                logger.exception("RAG chat API invocation failed: %s", agent_error)
                reply = f"{DEFAULT_RESPONSE_TEXT}\n\n(Error: {agent_error})"

            # Send reply via Composio (split into chunks if too long)
            try:
                logger.info(f"Sending reply to {sender_id} via Composio...")
                message_chunks = split_message_for_instagram(reply)
                logger.info(f"Message split into {len(message_chunks)} chunks (total length: {len(reply)} chars)")
                
                for i, chunk in enumerate(message_chunks):
                    logger.info(f"Sending chunk {i+1}/{len(message_chunks)} ({len(chunk)} chars)")
                    response = send_instagram_message(
                        org_id=org_id,
                        connected_account_id=connected_account_id,
                        recipient_id=str(sender_id),  # Ensure it's a string
                        text=chunk,
                    )
                    if not response.get("successful"):
                        logger.error(f"Failed to send chunk {i+1}: {response.get('error')}")
                    else:
                        logger.info(f"✅ Successfully sent chunk {i+1}/{len(message_chunks)}")
                    
                    # Small delay between chunks to avoid rate limiting
                    if i < len(message_chunks) - 1:
                        import asyncio
                        await asyncio.sleep(0.5)
                
                logger.info("✅ Successfully sent all response chunks via Composio")
            except Exception as send_error:
                logger.exception("❌ Failed to send message via Composio: %s", send_error)
                logger.error("Error type: %s, Error details: %s", type(send_error).__name__, str(send_error))

        # Handle changes events (if any)
        for change in changes:
            field = change.get("field")
            value = change.get("value", {})
            
            logger.info(f"Processing change event: field={field}, value={json.dumps(value, indent=2)}")
            
            # Handle messaging field changes
            if field == "messages":
                # Instagram messaging webhook format
                message_data = value.get("message", {})
                if message_data:
                    sender_id = message_data.get("from", {}).get("id")
                    message_text = message_data.get("text", "").strip()
                    message_id = message_data.get("id")
                    is_echo = message_data.get("is_echo", False)
                    
                    logger.info(
                        f"Change message details: sender_id={sender_id}, "
                        f"text='{message_text}', message_id={message_id}, is_echo={is_echo}"
                    )
                    
                    if not message_text or not sender_id:
                        logger.info("Skipping change event: empty text or missing sender_id")
                        continue
                    
                    if is_echo:
                        logger.info("Skipping echo message in change event")
                        continue
                    
                    if message_id and _is_duplicate(message_id):
                        logger.info("Duplicate message %s detected; ignoring", message_id)
                        continue
                    
                    logger.info(
                        "✅ Processing user message from changes: %s: %s",
                        sender_id,
                        message_text,
                    )
                    
                    # Get Composio account (single account setup)
                    try:
                        org_id, connected_account_id = get_composio_account()
                        logger.info(f"Using Composio account: org_id={org_id}, connected_account_id={connected_account_id}")
                    except HTTPException as exc:
                        logger.error("Composio account lookup failed: %s", exc.detail)
                        continue
                    
                    # Process with RAG Super Agent chat API
                    try:
                        logger.info("Dispatching to RAG chat API with text: %s", message_text)
                        reply = await get_rag_chat_response(message_text)
                        logger.info("RAG chat API reply: %s", reply)
                    except Exception as agent_error:
                        logger.exception("RAG chat API invocation failed: %s", agent_error)
                        reply = f"{DEFAULT_RESPONSE_TEXT}\n\n(Error: {agent_error})"
                    
                    # Send reply (split into chunks if too long)
                    try:
                        logger.info(f"Sending reply to {sender_id} via Composio...")
                        message_chunks = split_message_for_instagram(reply)
                        logger.info(f"Message split into {len(message_chunks)} chunks (total length: {len(reply)} chars)")
                        
                        for i, chunk in enumerate(message_chunks):
                            logger.info(f"Sending chunk {i+1}/{len(message_chunks)} ({len(chunk)} chars)")
                            response = send_instagram_message(
                                org_id=org_id,
                                connected_account_id=connected_account_id,
                                recipient_id=str(sender_id),  # Ensure it's a string
                                text=chunk,
                            )
                            if not response.get("successful"):
                                logger.error(f"Failed to send chunk {i+1}: {response.get('error')}")
                            else:
                                logger.info(f"✅ Successfully sent chunk {i+1}/{len(message_chunks)}")
                            
                            # Small delay between chunks to avoid rate limiting
                            if i < len(message_chunks) - 1:
                                import asyncio
                                await asyncio.sleep(0.5)
                        
                        logger.info("✅ Successfully sent all response chunks via Composio")
                    except Exception as send_error:
                        logger.exception("❌ Failed to send message via Composio: %s", send_error)
                        logger.error("Error type: %s, Error details: %s", type(send_error).__name__, str(send_error))

    return JSONResponse({"ok": True})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
