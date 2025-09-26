# scripts/g_gmail_check.py
# Simple Gmail smoke test: list newest message, fetch snippet, and print a tiny LLM-friendly summary prompt.
# This does not call the router; it's a quick direct proof your token works.

import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

def load_creds():
    secrets_dir = Path(os.getenv("GOOGLE_SECRETS_DIR", "/app/secrets"))
    token_path = secrets_dir / "google_token.json"
    if not token_path.exists():
        print("❌ Token not found. Run: docker compose exec api python scripts/g_auth.py", file=sys.stderr)
        sys.exit(1)
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            print("❌ Token invalid and cannot refresh. Rerun g_auth.py.", file=sys.stderr)
            sys.exit(1)
    return creds

def main():
    creds = load_creds()
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    # List the latest message in INBOX
    msgs = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], maxResults=1)
        .execute()
        .get("messages", [])
    )
    if not msgs:
        print("No messages found in Inbox.")
        return

    msg_id = msgs[0]["id"]
    msg = service.users().messages().get(userId="me", id=msg_id, format="metadata", metadataHeaders=["From","Subject","Date"]).execute()
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    snippet = msg.get("snippet", "")

    print("Latest message:")
    print("From   :", headers.get("From", ""))
    print("Subject:", headers.get("Subject", ""))
    print("Date   :", headers.get("Date", ""))
    print("Snippet:", snippet[:200])

    # Show a tiny LLM prompt example you could feed to your router later
    print("\n--- Suggested prompt to summarize ---")
    print(f"Summarize and propose a polite reply.\nFrom: {headers.get('From','')}\nSubject: {headers.get('Subject','')}\nSnippet: {snippet[:500]}")

if __name__ == "__main__":
    main()
