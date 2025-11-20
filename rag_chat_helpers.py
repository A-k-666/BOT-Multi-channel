"""Helper functions for integrating RAG Super Agent chat API."""

from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

RAG_CHAT_API_URL = os.getenv(
    "RAG_CHAT_API_URL", "https://rag-super-agent.onrender.com/chat/"
)


async def call_rag_chat_api(
    message: str,
    conversation_id: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """
    Call the RAG Super Agent chat API with a user message.
    
    Args:
        message: The user's message (can be a question, URL, or text to ingest)
        conversation_id: Optional conversation ID for context tracking
        timeout: Request timeout in seconds
        
    Returns:
        Response dictionary with message, type, data, conversation_id, and suggestions
    """
    payload: dict[str, Any] = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            RAG_CHAT_API_URL,
            json=payload,
            headers={"accept": "application/json", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()


async def get_rag_chat_response(
    message: str,
    conversation_id: str | None = None,
) -> str:
    """
    Get a text response from the RAG Super Agent chat API.
    
    Args:
        message: The user's message
        conversation_id: Optional conversation ID for context tracking
        
    Returns:
        The response message text
    """
    result = await call_rag_chat_api(message, conversation_id)
    return result.get("message", "Sorry, I couldn't get a response.")


__all__ = [
    "call_rag_chat_api",
    "get_rag_chat_response",
    "RAG_CHAT_API_URL",
]

