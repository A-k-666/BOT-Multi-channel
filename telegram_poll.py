"""Long-poll Telegram using Composio tools and respond with RAG Super Agent chat API."""

from __future__ import annotations
import asyncio
import logging
from typing import Any

from composio_helpers import (
    get_telegram_updates_via_composio,
    send_telegram_message_via_composio,
)
from rag_chat_helpers import get_rag_chat_response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2


async def handle_update(update: dict[str, Any]) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")

    if chat_id is None or text is None:
        logger.info("Skipping update without text/chat_id: %s", update)
        return

    logger.info("Received message from %s: %s", chat_id, text)
    
    try:
        # Call RAG Super Agent chat API
        reply = await get_rag_chat_response(text)
        logger.info("Reply: %s", reply)
        send_telegram_message_via_composio(chat_id=chat_id, text=reply)
    except Exception as exc:
        logger.exception("Error processing message: %s", exc)
        error_msg = "Sorry, I encountered an error processing your message. Please try again."
        send_telegram_message_via_composio(chat_id=chat_id, text=error_msg)


async def poll_loop() -> None:
    offset: int | None = None
    while True:
        try:
            result = get_telegram_updates_via_composio(
                offset=offset,
                timeout=30,
                limit=20,
            )
            if not result.get("successful"):
                logger.warning("Composio get updates failed: %s", result.get("error"))
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            updates = result.get("data", {}).get("result", [])
            if not updates:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            for update in updates:
                await handle_update(update)
                update_id = update.get("update_id")
                if update_id is not None:
                    offset = update_id + 1
        except Exception as exc:  # pragma: no cover - safety loop
            logger.exception("Polling error: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    logger.info("Starting Telegram polling loop via Composio...")
    asyncio.run(poll_loop())


if __name__ == "__main__":
    main()

