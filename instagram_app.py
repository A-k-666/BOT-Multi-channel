"""Instagram webhook handler for DMs and comments using Composio and RAG chat API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from composio import Composio

from rag_chat_helpers import get_rag_chat_response

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("Instagram app module loaded")

# Path to Instagram accounts JSON
INSTAGRAM_ACCOUNTS_PATH = Path(os.getenv("INSTAGRAM_ACCOUNTS_PATH", "instagram_accounts.json"))

# Hardcoded message for comments
COMMENT_REPLY_MESSAGE = "Thank you for commenting!"

# Composio client
composio_client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))


def load_instagram_account_mapping() -> dict[str, dict[str, str]]:
    """Load Instagram account mapping from JSON file."""
    if not INSTAGRAM_ACCOUNTS_PATH.exists():
        logger.warning("instagram_accounts.json not found at %s", INSTAGRAM_ACCOUNTS_PATH)
        return {}
    
    try:
        with open(INSTAGRAM_ACCOUNTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Failed to load instagram_accounts.json: %s", e)
        return {}


def get_composio_account_for_instagram(instagram_business_account_id: str) -> dict[str, str] | None:
    """
    Get Composio account details for an Instagram Business Account ID.
    
    Returns:
        dict with 'org_id' and 'connected_account_id', or None if not found
    """
    accounts = load_instagram_account_mapping()
    return accounts.get(instagram_business_account_id)


app = FastAPI(title="Instagram Webhook Handler")


@app.get("/")
async def root() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "instagram-webhook"}


@app.get("/instagram/webhook")
async def instagram_webhook_verify(
    request: Request,
    mode: str = Query(..., alias="hub.mode"),
    token: str = Query(..., alias="hub.verify_token"),
    challenge: str = Query(..., alias="hub.challenge"),
) -> PlainTextResponse:
    """Instagram webhook verification."""
    verify_token = os.getenv("INSTAGRAM_VERIFY_TOKEN", "your_verify_token")
    
    if mode == "subscribe" and token == verify_token:
        logger.info("Instagram webhook verified successfully")
        return PlainTextResponse(challenge)
    
    logger.warning("Instagram webhook verification failed: mode=%s, token=%s", mode, token)
    raise HTTPException(status_code=403, detail="Verification failed")


async def process_instagram_message(
    sender_id: str,
    message_text: str,
    instagram_business_account_id: str,
    org_id: str,
    connected_account_id: str,
) -> None:
    """Process Instagram DM and send reply via /chat API."""
    try:
        logger.info(
            "Processing Instagram DM from %s to account %s: %s",
            sender_id,
            instagram_business_account_id,
            message_text[:100],
        )
        
        # Get reply from /chat API
        reply = await get_rag_chat_response(message_text)
        logger.info("Got reply from /chat API: %s", reply[:100])
        
        # Send DM via Composio
        result = composio_client.tools.execute(
            slug="INSTAGRAM_SEND_TEXT_MESSAGE",
            arguments={
                "ig_user_id": sender_id,
                "message": reply,
            },
            user_id=org_id,
            connected_account_id=connected_account_id,
            dangerously_skip_version_check=True,
        )
        
        if result.get("successful"):
            logger.info("✅ Successfully sent DM reply to %s", sender_id)
        else:
            logger.error("❌ Failed to send DM: %s", result.get("error"))
    
    except Exception as e:
        logger.exception("Error processing Instagram message: %s", e)


async def process_instagram_comment(
    comment_id: str,
    instagram_business_account_id: str,
    org_id: str,
    connected_account_id: str,
) -> None:
    """Process Instagram comment and send hardcoded reply."""
    try:
        logger.info(
            "Processing Instagram comment %s on account %s",
            comment_id,
            instagram_business_account_id,
        )
        
        # Send hardcoded reply via Composio
        result = composio_client.tools.execute(
            slug="INSTAGRAM_REPLY_TO_COMMENT",
            arguments={
                "ig_comment_id": comment_id,
                "message": COMMENT_REPLY_MESSAGE,
            },
            user_id=org_id,
            connected_account_id=connected_account_id,
            dangerously_skip_version_check=True,
        )
        
        if result.get("successful"):
            logger.info("✅ Successfully replied to comment %s", comment_id)
        else:
            logger.error("❌ Failed to reply to comment: %s", result.get("error"))
    
    except Exception as e:
        logger.exception("Error processing Instagram comment: %s", e)


@app.post("/instagram/webhook")
async def instagram_webhook(request: Request) -> JSONResponse:
    """
    Handle Instagram webhook events (DMs and comments).
    Returns 200 OK immediately and processes in background.
    """
    try:
        payload = await request.json()
        logger.info("Received Instagram webhook payload: %s", json.dumps(payload, indent=2))
        
        # Return 200 OK immediately to avoid timeout
        asyncio.create_task(handle_instagram_webhook(payload))
        return JSONResponse({"ok": True})
    
    except Exception as e:
        logger.exception("Error handling Instagram webhook: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def handle_instagram_webhook(payload: dict[str, Any]) -> None:
    """Process Instagram webhook payload in background."""
    try:
        if payload.get("object") != "instagram":
            logger.warning("Received non-Instagram webhook: %s", payload.get("object"))
            return
        
        entries = payload.get("entry", [])
        for entry in entries:
            # Get Instagram Business Account ID from entry
            instagram_business_account_id = entry.get("id")
            if not instagram_business_account_id:
                logger.warning("Entry missing Instagram Business Account ID: %s", entry)
                continue
            
            # Get Composio account details
            account_info = get_composio_account_for_instagram(instagram_business_account_id)
            if not account_info:
                logger.warning(
                    "No account mapping found for Instagram Business Account ID: %s",
                    instagram_business_account_id,
                )
                continue
            
            org_id = account_info["org_id"]
            connected_account_id = account_info["connected_account_id"]
            
            # Process changes (comments or messages)
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                field = change.get("field")
                
                # Handle comments
                if field == "comments":
                    comment_id = value.get("id")
                    if comment_id:
                        await process_instagram_comment(
                            comment_id=comment_id,
                            instagram_business_account_id=instagram_business_account_id,
                            org_id=org_id,
                            connected_account_id=connected_account_id,
                        )
                
                # Handle messages (DMs)
                elif field == "messages":
                    messages = value.get("messages", [])
                    for message in messages:
                        sender_id = message.get("from", {}).get("id")
                        message_text = message.get("text", "").strip()
                        
                        if sender_id and message_text:
                            await process_instagram_message(
                                sender_id=sender_id,
                                message_text=message_text,
                                instagram_business_account_id=instagram_business_account_id,
                                org_id=org_id,
                                connected_account_id=connected_account_id,
                            )
    
    except Exception as e:
        logger.exception("Error in handle_instagram_webhook: %s", e)


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
