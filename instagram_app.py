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
import httpx

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("instagram_app")
logger.setLevel(logging.INFO)
logger.propagate = True
logger.info("Instagram app module loaded")

INSTAGRAM_VERIFY_TOKEN = os.getenv("INSTAGRAM_VERIFY_TOKEN", "")

# Single account setup - direct from environment variables (fallback)
INSTAGRAM_ORG_ID = os.getenv("INSTAGRAM_ORG_ID") or os.getenv("COMPOSIO_USER_ID", "")
INSTAGRAM_CONNECTED_ACCOUNT_ID = os.getenv("INSTAGRAM_CONNECTED_ACCOUNT_ID", "")

# Facebook Page Access Token for Instagram Graph API calls (fallback to env var if not in Composio)
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")

# Instagram Business Account ID (fallback to env var if not in Composio)
INSTAGRAM_BUSINESS_ACCOUNT_ID = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")

# Multi-account setup - JSON file for account mappings
INSTAGRAM_ACCOUNTS_PATH = Path("instagram_accounts.json")

_processed_message_ids: deque[str] = deque()
_processed_message_index: set[str] = set()

_processed_comment_ids: deque[str] = deque()
_processed_comment_index: set[str] = set()

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


def load_instagram_account_mapping() -> dict[str, Any]:
    """
    Load Instagram Business Account ID to Composio account mapping from JSON file.
    Returns empty dict if file doesn't exist.
    """
    if not INSTAGRAM_ACCOUNTS_PATH.exists():
        return {}
    try:
        with INSTAGRAM_ACCOUNTS_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, Exception) as e:
        logger.error("Failed to load instagram_accounts.json: %s", e)
        return {}


def get_composio_account_for_instagram(instagram_business_account_id: str) -> tuple[str, str, dict[str, Any]]:
    """
    Get Composio org_id and connected_account_id for a specific Instagram Business Account ID.
    First checks JSON mapping (multi-account), then falls back to env vars (single account).
    
    Returns:
        tuple: (org_id, connected_account_id, account_config)
        account_config contains: facebook_page_access_token, instagram_business_account_id, etc.
    """
    # Load account mappings
    account_map = load_instagram_account_mapping()
    
    # Check JSON mapping first (multi-account setup)
    if account_map and instagram_business_account_id in account_map:
        entry = account_map[instagram_business_account_id]
        org_id = entry.get("org_id")
        connected_account_id = entry.get("connected_account_id")
        
        if org_id and connected_account_id:
            logger.info(f"Found account mapping for Instagram ID {instagram_business_account_id}")
            return org_id, connected_account_id, entry
    
    # Fallback to environment variables (single account setup)
    if INSTAGRAM_ORG_ID and INSTAGRAM_CONNECTED_ACCOUNT_ID:
        logger.info("Using environment variables for single account setup")
        config = {
            "facebook_page_access_token": FACEBOOK_PAGE_ACCESS_TOKEN,
            "instagram_business_account_id": INSTAGRAM_BUSINESS_ACCOUNT_ID or instagram_business_account_id,
        }
        return INSTAGRAM_ORG_ID, INSTAGRAM_CONNECTED_ACCOUNT_ID, config
    
    # If neither available, raise error
    raise HTTPException(
        status_code=400,
        detail=(
            f"Unknown Instagram Business Account ID {instagram_business_account_id}. "
            "Either add it to instagram_accounts.json or set INSTAGRAM_ORG_ID and "
            "INSTAGRAM_CONNECTED_ACCOUNT_ID env vars."
        ),
    )


def get_composio_account() -> tuple[str, str]:
    """
    Get Composio org_id and connected_account_id from environment variables.
    Single account setup - no JSON needed.
    (Kept for backward compatibility)
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


def get_instagram_access_token_from_composio(
    org_id: str | None = None,
    connected_account_id: str | None = None,
    account_config: dict[str, Any] | None = None,
) -> str:
    """
    Get Facebook Page Access Token from Composio connected account or account config.
    Falls back to environment variable if not found in Composio.
    """
    # First check account_config (from JSON mapping)
    if account_config and account_config.get("facebook_page_access_token"):
        token = account_config.get("facebook_page_access_token")
        if token:
            logger.info("✅ Using access token from account config (JSON mapping)")
            return str(token)
    
    # Then try to get from Composio connected account
    try:
        resolved_org_id = org_id or INSTAGRAM_ORG_ID
        resolved_account_id = connected_account_id or INSTAGRAM_CONNECTED_ACCOUNT_ID
        
        if resolved_org_id and resolved_account_id:
            # Get connected account details from Composio
            # Try to get account by ID directly (without user_id parameter)
            try:
                account = composio_client.connected_accounts.get(
                    connected_account_id=resolved_account_id,
                )
            except (TypeError, AttributeError):
                # If that doesn't work, list and filter
                accounts = composio_client.connected_accounts.list(
                    user_ids=[resolved_org_id],
                )
                account = None
                for acc in accounts.items:
                    if acc.id == resolved_account_id:
                        account = acc
                        break
                if not account:
                    raise ValueError(f"Connected account {resolved_account_id} not found")
            
            logger.info(f"Retrieved Composio account: {account.id}, type: {type(account)}")
            
            # Try to extract access token from account metadata/config
            # Composio stores tokens in different places depending on the account type
            token = None
            
            # Check metadata
            if hasattr(account, 'metadata') and account.metadata:
                metadata = account.metadata if isinstance(account.metadata, dict) else {}
                logger.debug(f"Account metadata keys: {list(metadata.keys()) if isinstance(metadata, dict) else 'N/A'}")
                token = (
                    metadata.get("access_token") or
                    metadata.get("page_access_token") or
                    metadata.get("token") or
                    metadata.get("facebook_page_access_token")
                )
            
            # Check config
            if not token and hasattr(account, 'config') and account.config:
                config = account.config if isinstance(account.config, dict) else {}
                logger.debug(f"Account config keys: {list(config.keys()) if isinstance(config, dict) else 'N/A'}")
                if isinstance(config, dict):
                    token = (
                        config.get("access_token") or
                        config.get("page_access_token") or
                        config.get("token") or
                        config.get("facebook_page_access_token")
                    )
            
            # Check account attributes directly
            if not token:
                token = (
                    getattr(account, 'access_token', None) or
                    getattr(account, 'page_access_token', None) or
                    getattr(account, 'token', None)
                )
            
            if token:
                logger.info("✅ Successfully retrieved access token from Composio connected account")
                return str(token)
            else:
                logger.warning("Access token not found in Composio account metadata/config/attributes")
                
    except Exception as e:
        logger.warning(f"Failed to get access token from Composio: {e}. Falling back to env var.")
        logger.debug(f"Exception details: {type(e).__name__}: {str(e)}")
    
    # Fallback to environment variable
    if FACEBOOK_PAGE_ACCESS_TOKEN:
        logger.info("Using access token from environment variable")
        return FACEBOOK_PAGE_ACCESS_TOKEN
    
    raise ValueError(
        "Facebook Page Access Token not found. "
        "Either connect Instagram account via Composio or set FACEBOOK_PAGE_ACCESS_TOKEN in .env file."
    )


def get_instagram_business_account_id_from_composio(
    org_id: str | None = None,
    connected_account_id: str | None = None,
    account_config: dict[str, Any] | None = None,
) -> str:
    """
    Get Instagram Business Account ID from Composio connected account or account config.
    Falls back to environment variable if not found in Composio.
    """
    # First check account_config (from JSON mapping)
    if account_config and account_config.get("instagram_business_account_id"):
        ig_id = account_config.get("instagram_business_account_id")
        if ig_id:
            logger.info(f"✅ Using Instagram Business Account ID from account config: {ig_id}")
            return str(ig_id)
    
    # Then try to get from Composio connected account
    try:
        resolved_org_id = org_id or INSTAGRAM_ORG_ID
        resolved_account_id = connected_account_id or INSTAGRAM_CONNECTED_ACCOUNT_ID
        
        if resolved_org_id and resolved_account_id:
            # Get connected account details from Composio
            # Try to get account by ID directly (without user_id parameter)
            try:
                account = composio_client.connected_accounts.get(
                    connected_account_id=resolved_account_id,
                )
            except (TypeError, AttributeError):
                # If that doesn't work, list and filter
                accounts = composio_client.connected_accounts.list(
                    user_ids=[resolved_org_id],
                )
                account = None
                for acc in accounts.items:
                    if acc.id == resolved_account_id:
                        account = acc
                        break
                if not account:
                    raise ValueError(f"Connected account {resolved_account_id} not found")
            
            ig_id = None
            
            # Try to extract Instagram Business Account ID from account metadata
            if hasattr(account, 'metadata') and account.metadata:
                metadata = account.metadata if isinstance(account.metadata, dict) else {}
                logger.debug(f"Account metadata keys: {list(metadata.keys()) if isinstance(metadata, dict) else 'N/A'}")
                # Check common ID field names
                ig_id = (
                    metadata.get("instagram_business_account_id") or
                    metadata.get("ig_business_account_id") or
                    metadata.get("instagram_account_id") or
                    metadata.get("ig_account_id") or
                    metadata.get("account_id")
                )
            
            # Check config
            if not ig_id and hasattr(account, 'config') and account.config:
                config = account.config if isinstance(config, dict) else {}
                logger.debug(f"Account config keys: {list(config.keys()) if isinstance(config, dict) else 'N/A'}")
                ig_id = (
                    config.get("instagram_business_account_id") or
                    config.get("ig_business_account_id") or
                    config.get("instagram_account_id") or
                    config.get("ig_account_id") or
                    config.get("account_id")
                )
            
            # Try to get from account attributes
            if not ig_id:
                ig_id = (
                    getattr(account, 'instagram_business_account_id', None) or
                    getattr(account, 'ig_business_account_id', None) or
                    getattr(account, 'instagram_account_id', None) or
                    getattr(account, 'account_id', None)
                )
            
            if ig_id:
                logger.info(f"✅ Successfully retrieved Instagram Business Account ID from Composio: {ig_id}")
                return str(ig_id)
            else:
                logger.warning("Instagram Business Account ID not found in Composio account metadata/config/attributes")
                
    except Exception as e:
        logger.warning(f"Failed to get Instagram Business Account ID from Composio: {e}. Falling back to env var.")
        logger.debug(f"Exception details: {type(e).__name__}: {str(e)}")
    
    # Fallback to environment variable
    if INSTAGRAM_BUSINESS_ACCOUNT_ID:
        logger.info("Using Instagram Business Account ID from environment variable")
        return INSTAGRAM_BUSINESS_ACCOUNT_ID
    
    raise ValueError(
        "Instagram Business Account ID not found. "
        "Either connect Instagram account via Composio or set INSTAGRAM_BUSINESS_ACCOUNT_ID in .env file."
    )


def _is_duplicate_comment(comment_id: str) -> bool:
    """Check if comment ID was already processed."""
    if comment_id in _processed_comment_index:
        return True
    _processed_comment_ids.append(comment_id)
    _processed_comment_index.add(comment_id)
    while len(_processed_comment_ids) > 500:
        old = _processed_comment_ids.popleft()
        _processed_comment_index.discard(old)
    return False


def reply_to_instagram_comment(
    comment_id: str,
    reply_text: str = "thank you for commenting",
    access_token: str | None = None,
    instagram_account_id: str | None = None,
    org_id: str | None = None,
    connected_account_id: str | None = None,
    account_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Reply to an Instagram comment using Facebook Graph API.
    
    Uses POST /<IG_ID>/mentions endpoint to reply to comments.
    
    Args:
        comment_id: The Instagram comment ID to reply to
        reply_text: The reply message text (default: "thank you for commenting")
        access_token: Facebook Page Access Token (if not provided, fetches from Composio or env var)
        instagram_account_id: Instagram Business Account ID (if not provided, fetches from Composio or env var)
        org_id: Composio org_id (optional, for fetching from Composio)
        connected_account_id: Composio connected_account_id (optional, for fetching from Composio)
    
    Returns:
        dict with response from Facebook Graph API
    """
    # Get access token from Composio/account_config if not provided
    if not access_token:
        try:
            access_token = get_instagram_access_token_from_composio(org_id, connected_account_id, account_config)
        except Exception as e:
            logger.error(f"Failed to get access token: {e}")
            raise ValueError(f"Facebook Page Access Token not found: {e}")
    
    # Get Instagram Business Account ID from Composio/account_config if not provided
    if not instagram_account_id:
        try:
            instagram_account_id = get_instagram_business_account_id_from_composio(org_id, connected_account_id, account_config)
        except Exception as e:
            logger.error(f"Failed to get Instagram Business Account ID: {e}")
            raise ValueError(f"Instagram Business Account ID not found: {e}")
    
    # Facebook Graph API endpoint for replying to Instagram comments
    # POST /<IG_ID>/mentions
    url = f"https://graph.instagram.com/v18.0/{instagram_account_id}/mentions"
    
    # Request payload
    payload = {
        "comment_id": comment_id,
        "message": reply_text,
        "access_token": access_token,
    }
    
    try:
        response = httpx.post(url, data=payload, timeout=10.0)
        response.raise_for_status()
        result = response.json()
        logger.info(f"Instagram comment reply sent successfully: {result}")
        return {"data": result, "successful": True, "error": None}
    except httpx.HTTPStatusError as e:
        error_data = e.response.json() if e.response else {}
        error_message = error_data.get("error", {}).get("message", str(error_data))
        error_code = error_data.get("error", {}).get("code", "unknown")
        
        logger.error(f"Facebook Graph API error: {e.response.status_code} - {error_message}")
        
        return {"data": {}, "successful": False, "error": error_message}
    except Exception as e:
        logger.error(f"Failed to reply to Instagram comment: {e}")
        return {"data": {}, "successful": False, "error": str(e)}


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

    # Return 200 OK immediately and process in background
    import asyncio
    
    # Check for comment events - Instagram comment webhooks come with object: "instagram" and field: "comments"
    if payload.get("object") == "instagram":
        # Check if it's a comment event
        entries = payload.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                if change.get("field") == "comments":
                    # It's a comment event, process it
                    asyncio.create_task(handle_instagram_comment_webhook(payload))
                    return JSONResponse({"ok": True})
    
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


async def handle_instagram_comment_webhook(payload: dict[str, Any]) -> None:
    """
    Handle Instagram comment webhook events.
    Processes comments in background and replies automatically.
    """
    entries = payload.get("entry", [])
    for entry in entries:
        instagram_account_id = entry.get("id")  # Instagram Business Account ID
        if not instagram_account_id:
            logger.warning("Entry missing Instagram account ID: %s", entry)
            continue

        # Handle comment events
        changes = entry.get("changes", [])
        for change in changes:
            field = change.get("field")
            value = change.get("value", {})
            
            # Check if it's a comment event
            if field == "comments":
                # Instagram webhook format: comment data is directly in value, not nested under "comment"
                # Format: {"value": {"id": "comment_id", "text": "comment text", "from": {...}, "media": {...}}}
                comment_id = value.get("id")  # Comment ID is directly in value
                comment_text = value.get("text", "")
                media_id = value.get("media", {}).get("id") if isinstance(value.get("media"), dict) else None
                from_user = value.get("from", {})
                from_user_id = from_user.get("id") if isinstance(from_user, dict) else None
                
                if not comment_id:
                    logger.warning("Comment event missing comment ID: %s", change)
                    logger.debug(f"Value keys: {list(value.keys()) if isinstance(value, dict) else 'N/A'}")
                    continue
                
                # Skip if already processed
                if _is_duplicate_comment(comment_id):
                    logger.info("Duplicate comment %s detected; ignoring", comment_id)
                    continue
                
                logger.info(
                    "Received Instagram comment: id=%s, text='%s', media_id=%s, IG Account: %s",
                    comment_id,
                    comment_text[:100],  # Log first 100 chars
                    media_id,
                    instagram_account_id,
                )
                
                # Process comment reply in background (account routing happens inside)
                import asyncio
                asyncio.create_task(process_instagram_comment(
                    comment_id=comment_id,
                    instagram_business_account_id=instagram_account_id,
                ))


async def process_instagram_comment(
    comment_id: str,
    instagram_business_account_id: str,
    org_id: str | None = None,
    connected_account_id: str | None = None,
    account_config: dict[str, Any] | None = None,
) -> None:
    """
    Process an Instagram comment and reply automatically.
    Uses account mapping to get correct account details.
    """
    try:
        logger.info(f"Processing comment reply for comment_id: {comment_id}, IG Account: {instagram_business_account_id}")
        reply_text = "thank you for commenting"
        
        # Get account details from mapping if not provided
        if not org_id or not connected_account_id or not account_config:
            org_id, connected_account_id, account_config = get_composio_account_for_instagram(
                instagram_business_account_id
            )
        
        response = reply_to_instagram_comment(
            comment_id=comment_id,
            reply_text=reply_text,
            org_id=org_id,
            connected_account_id=connected_account_id,
            account_config=account_config,
        )
        
        if response.get("successful"):
            logger.info(f"✅ Successfully replied to comment {comment_id}")
        else:
            logger.error(f"❌ Failed to reply to comment {comment_id}: {response.get('error')}")
    except Exception as e:
        logger.exception(f"Error processing Instagram comment {comment_id}: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
