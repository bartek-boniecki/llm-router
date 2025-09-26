"""
Slack Web API (chat.postMessage)
Docs:
- chat.postMessage: https://api.slack.com/methods/chat.postMessage
- conversations.list: https://api.slack.com/methods/conversations.list

This module:
- Accepts a human channel name like "#general" and resolves it to a channel ID.
- Posts a message (optionally in a thread).
- Raises helpful errors when Slack returns ok:false (HTTP 200 but failed).
"""

from __future__ import annotations
import os
from typing import Optional

import httpx

SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")  # set this in .env
SLACK_POST_URL = "https://slack.com/api/chat.postMessage"
SLACK_LIST_URL = "https://slack.com/api/conversations.list"


class SlackError(RuntimeError):
    """Raised when Slack returns ok:false with an 'error' string."""
    pass


async def _slack_client(timeout: int = 20) -> httpx.AsyncClient:
    # Small helper to build a client with auth headers
    headers = {
        "Authorization": f"Bearer {SLACK_TOKEN or ''}",
        "Content-Type": "application/json; charset=utf-8",
    }
    return httpx.AsyncClient(timeout=timeout, headers=headers)


async def _resolve_channel_id(channel: str) -> Optional[str]:
    """
    Convert '#general' or 'general' to a channel ID (e.g., 'C01234567').
    If already looks like an ID (starts with 'C'), return it as-is.
    Returns None if not found or not configured.
    """
    if not SLACK_TOKEN:
        return None

    ch = channel.strip()
    if ch.startswith("C") and len(ch) >= 9:
        # Looks like an ID already.
        return ch
    if ch.startswith("#"):
        ch = ch[1:]

    # Iterate pages until found or exhausted
    async with await _slack_client() as client:
        cursor = None
        while True:
            params = {"limit": 1000}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(SLACK_LIST_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok", False):
                # conversations.list also returns ok:false on auth errors
                raise SlackError(f"conversations.list failed: {data.get('error', 'unknown_error')}")

            for c in data.get("channels", []) or []:
                if (c.get("name") or "").lower() == ch.lower():
                    return c.get("id")

            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break

    return None


async def post_message(channel: str, text: str, thread_ts: Optional[str] = None) -> dict:
    """
    Post a message to Slack.
    - channel: '#general', 'general', or a real channel ID like 'C01234567'
    - text: the message text
    - thread_ts: optional, if you want to reply in a thread
    Returns the Slack response JSON (with 'ok': true) or raises SlackError.
    """
    if not SLACK_TOKEN:
        # No token configured => do a safe no-op so local demos don't crash.
        return {"ok": False, "error": "no_token_configured"}

    channel_id = await _resolve_channel_id(channel) or channel

    payload = {"channel": channel_id, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts

    async with await _slack_client() as client:
        resp = await client.post(SLACK_POST_URL, json=payload)
        # HTTP 200 does NOT mean success; Slack uses ok:true/false.
        resp.raise_for_status()
        data = resp.json()

    if not data.get("ok", False):
        err = data.get("error", "unknown_error")
        # Make common errors actionable for newcomers:
        if err == "not_in_channel":
            raise SlackError(
                "Slack says the bot is not a member of that channel. "
                "Open Slack and run `/invite @<your-bot-name>` in #general (or chosen channel)."
            )
        if err == "channel_not_found":
            raise SlackError(
                "Channel not found. Double-check the channel name or use the channel ID (starts with 'C')."
            )
        if err == "invalid_auth" or err == "not_authed":
            raise SlackError(
                "Invalid or missing Slack token. Re-check SLACK_BOT_TOKEN in your .env and restart Docker."
            )
        raise SlackError(f"Slack API returned ok:false error='{err}'")

    return data
