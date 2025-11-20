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
    timeout: float = 25.0,
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
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                RAG_CHAT_API_URL,
                json=payload,
                headers={"accept": "application/json", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        raise TimeoutError(f"RAG chat API request timed out after {timeout} seconds")
    except httpx.HTTPStatusError as e:
        raise Exception(f"RAG chat API returned error {e.response.status_code}: {e.response.text}")
    except Exception as e:
        raise Exception(f"Failed to call RAG chat API: {str(e)}")


async def get_rag_chat_response(
    message: str,
    conversation_id: str | None = None,
    timeout: float = 25.0,
) -> str:
    """
    Get a text response from the RAG Super Agent chat API.
    
    Args:
        message: The user's message
        conversation_id: Optional conversation ID for context tracking
        timeout: Request timeout in seconds (default 25s to avoid server timeout)
        
    Returns:
        The response message text
    """
    try:
        result = await call_rag_chat_api(message, conversation_id, timeout=timeout)
        return result.get("message", "Sorry, I couldn't get a response.")
    except TimeoutError:
        return "Sorry, the request took too long. Please try again with a shorter question."
    except Exception as e:
        return f"Sorry, I encountered an error: {str(e)}"


__all__ = [
    "call_rag_chat_api",
    "get_rag_chat_response",
    "RAG_CHAT_API_URL",
]

