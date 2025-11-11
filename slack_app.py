"""Slack event handler that routes mentions to DeepAgent and replies via Composio."""

from __future__ import annotations
import hmac
import json
import os
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from composio import Composio
from composio_langchain import LangchainProvider

from DeepAgent import run_agent

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("slack_app")

SLACK_BOT_USER_ID = os.getenv("SLACK_BOT_USER_ID")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
SLACK_ACCOUNTS_PATH = Path("slack_accounts.json")
DEFAULT_RESPONSE_TEXT = os.getenv(
    "SLACK_DEFAULT_RESPONSE",
    "Hi! I'm still connecting. Please try again later.",
)


def load_slack_mapping() -> dict[str, Any]:
    if not SLACK_ACCOUNTS_PATH.exists():
        raise RuntimeError("slack_accounts.json not found.")
    with SLACK_ACCOUNTS_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


slack_account_map = load_slack_mapping()

composio_client = Composio(provider=LangchainProvider())

app = FastAPI(title="Composio Slack Bridge")


def verify_slack_signature(request: Request, raw_body: bytes) -> None:
    if not SLACK_SIGNING_SECRET:
        return
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Missing Slack signature headers")

    if abs(time.time() - int(timestamp)) > 60 * 5:
        raise HTTPException(status_code=401, detail="Slack request timestamp too old")

    base_string = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    my_signature = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(), base_string.encode(), sha256
    ).hexdigest()

    if not hmac.compare_digest(my_signature, signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")


def send_slack_message(
    *,
    org_id: str,
    connected_account_id: str,
    channel: str,
    text: str,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "channel": channel,
        "text": text,
    }
    if thread_ts:
        arguments["thread_ts"] = thread_ts

    return composio_client.tools.execute(
        slug="SLACKBOT_CHAT_POST_MESSAGE",
        arguments=arguments,
        user_id=org_id,
        connected_account_id=connected_account_id,
        version="latest",
        dangerously_skip_version_check=True,
    )


def resolve_workspace(team_id: str) -> tuple[str, str]:
    if team_id not in slack_account_map:
        raise HTTPException(status_code=400, detail=f"Unknown team_id {team_id}")
    entry = slack_account_map[team_id]
    return entry["org_id"], entry["connected_account_id"]


def is_bot_mention(event: dict[str, Any]) -> bool:
    if event.get("type") == "app_mention":
        return True
    text = event.get("text", "")
    if SLACK_BOT_USER_ID and f"<@{SLACK_BOT_USER_ID}>" in text:
        return True
    return False


@app.post("/slack/events")
async def slack_events(request: Request):
    raw_body = await request.body()
    logger.info("Incoming headers: %s", dict(request.headers))
    verify_slack_signature(request, raw_body)

    payload = await request.json()
    logger.info("Received payload: %s", payload)

    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge")})

    event = payload.get("event", {})
    if not event:
        return JSONResponse({"ok": True})

    if event.get("bot_id"):
        logger.info("Ignoring bot event: %s", event)
        return JSONResponse({"ok": True})

    if not is_bot_mention(event):
        logger.info("Event is not a mention: %s", event)
        return JSONResponse({"ok": True})

    team_id = payload.get("team_id")
    channel = event.get("channel")
    thread_ts = event.get("thread_ts")
    user_text = event.get("text", "").strip()

    if not team_id or not channel:
        logger.warning("Missing team_id/channel in event: %s", event)
        return JSONResponse({"ok": True})

    try:
        org_id, connected_account_id = resolve_workspace(team_id)
    except HTTPException as exc:
        logger.error("Team lookup failed for %s: %s", team_id, exc.detail)
        return JSONResponse({"ok": False, "error": exc.detail})

    try:
        cleaned_text = user_text
        if SLACK_BOT_USER_ID:
            cleaned_text = cleaned_text.replace(f"<@{SLACK_BOT_USER_ID}>", "").strip()
        logger.info("Dispatching to DeepAgent with text: %s", cleaned_text)
        reply = run_agent(cleaned_text or user_text)
    except Exception as agent_error:  # pragma: no cover
        logger.exception("DeepAgent invocation failed: %s", agent_error)
        reply = f"{DEFAULT_RESPONSE_TEXT}\n\n(Error: {agent_error})"

    response = send_slack_message(
        org_id=org_id,
        connected_account_id=connected_account_id,
        channel=channel,
        text=reply,
        thread_ts=thread_ts,
    )
    logger.info("Sent response via Composio: %s", response)

    return JSONResponse({"ok": True, "composio": response})

