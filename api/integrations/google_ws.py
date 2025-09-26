# api/integrations/google_ws.py
# Google Workspace helpers: Gmail + Google Docs
from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Optional, Tuple

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SECRETS_DIR = Path("/app/secrets")  # inside container
TOKEN_PATH = SECRETS_DIR / "google_token.json"

# Scopes expected to already be in your token
SCOPES_GMAIL = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]
SCOPES_GDOCS = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]


class GoogleAuthError(RuntimeError):
    pass


def _load_google_creds(required_scopes: list[str]) -> Credentials:
    """
    Loads token from /app/secrets/google_token.json and silently refreshes if needed.
    Raises a readable error if token is missing or lacks required scopes.
    """
    if not TOKEN_PATH.exists():
        raise GoogleAuthError(
            "Google token not found. Run the host helper first:\n"
            "  python .\\scripts\\g_auth_host.py"
        )

    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), required_scopes)
    except Exception as e:
        raise GoogleAuthError(f"Bad or unreadable google_token.json: {e}")

    # If the token was created earlier without the new scopes, Google libs mark it invalid here.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
            except Exception as e:
                raise GoogleAuthError(f"Failed to refresh token: {e}")
        else:
            # Missing scopes or no refresh_token
            raise GoogleAuthError(
                "Token is invalid for the required scopes. "
                "Delete secrets/google_token.json and re-run: python .\\scripts\\g_auth_host.py"
            )

    # Final scope check (defensive)
    granted = set(creds.scopes or [])
    missing = [s for s in required_scopes if s not in granted]
    if missing:
        raise GoogleAuthError(
            "Token missing scopes: " + ", ".join(missing) +
            "\nDelete secrets/google_token.json and re-run: python .\\scripts\\g_auth_host.py"
        )
    return creds


# ---------------- Gmail helpers already used elsewhere ----------------
def gmail_fetch_newest_thread(n: int = 3) -> str:
    creds = _load_google_creds(SCOPES_GMAIL)
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    # Minimal sample: list N recent messages and pull headers/snippets
    resp = svc.users().messages().list(userId="me", maxResults=n).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    parts = []
    for i, mid in enumerate(ids, 1):
        m = svc.users().messages().get(userId="me", id=mid, format="metadata", metadataHeaders=["From","Subject","Date"]).execute()
        hdrs = {h["name"]: h["value"] for h in m.get("payload",{}).get("headers", [])}
        snippet = m.get("snippet","").replace("\n"," ").strip()
        parts.append(f"[{i}] From: {hdrs.get('From','?')} | Date: {hdrs.get('Date','?')}\n"
                     f"    Subject: {hdrs.get('Subject','(no subject)')}\n"
                     f"    Preview: {snippet[:200]}")
    return "\n".join(parts) if parts else "(no messages)"


def gmail_create_draft_reply(thread_id: str, text: str) -> str:
    # Simple placeholder – in your earlier flows you used a different helper.
    creds = _load_google_creds(SCOPES_GMAIL)
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    body = {
        "message": {
            "threadId": thread_id,
            "raw": "",  # You could construct RFC822 if needed
        }
    }
    draft = svc.users().drafts().create(userId="me", body=body).execute()
    return f"https://mail.google.com/mail/u/0/#drafts?compose={draft.get('id')}"


# ---------------- Google Docs: create a doc from text ----------------
def gdocs_create_from_text(title: str, text: str) -> str:
    """
    Creates a new Google Doc with 'title' and inserts 'text' as the document body.
    Returns a human-friendly URL to the new doc.
    Requires Docs + Drive.file scopes.
    """
    creds = _load_google_creds(SCOPES_GDOCS)

    # 1) Create an empty doc with the title
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    try:
        doc = docs.documents().create(body={"title": title}).execute()
        doc_id = doc["documentId"]
    except HttpError as e:
        raise GoogleAuthError(f"Google Docs create failed: {e}")

    # 2) Insert text at the end (index 1 is just after the start)
    try:
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {"insertText": {"location": {"index": 1}, "text": text}}
                ]
            },
        ).execute()
    except HttpError as e:
        raise GoogleAuthError(f"Google Docs update failed: {e}")

    # 3) Build a shareable editor URL
    return f"https://docs.google.com/document/d/{doc_id}/edit"
