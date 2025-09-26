"""
Microsoft Graph minimal calls (read messages, draft reply placeholder)
Docs: List messages https://learn.microsoft.com/graph/api/user-list-messages
Requires GRAPH_ACCESS_TOKEN env var with Mail.Read permission.
"""

import httpx
import os

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN = os.getenv("GRAPH_ACCESS_TOKEN")


async def ms_read_latest():
    if not TOKEN:
        return
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{GRAPH}/me/messages?$top=1", headers=headers)
        r.raise_for_status()
        return r.json()


async def ms_draft_reply(message_id: str, body_text: str):
    # Placeholder: We'd call /me/messages/{id}/createReply and then /send
    # For demo we no-op if not configured properly.
    return
