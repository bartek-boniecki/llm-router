"""
Google Workspace (Gmail minimal demo)
Docs: users.messages.list (2025-08-04) https://developers.google.com/workspace/gmail/api/guides/list-messages
Requires an OAuth access token in env: GMAIL_ACCESS_TOKEN
"""

import httpx
import os

BASE = "https://gmail.googleapis.com/gmail/v1"
TOKEN = os.getenv("GMAIL_ACCESS_TOKEN")


async def gmail_read_latest():
    if not TOKEN:
        return
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{BASE}/users/me/messages?maxResults=1", headers=headers)
        r.raise_for_status()
        return r.json()


async def gmail_draft_reply(thread_id: str, body_text: str):
    if not TOKEN:
        return
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    # Simplest possible: create a draft MIME with threadId (left as exercise in a real app)
    # Here we no-op for demo (to avoid complex MIME steps).
    return
