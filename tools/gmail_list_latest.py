import os, sys, json
from datetime import datetime, timedelta
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# Paths from your .env mapping
TOKEN_PATH = Path("./state/google_token.json").resolve()
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]

def _get_creds():
    if not TOKEN_PATH.exists():
        print(f"Token not found: {TOKEN_PATH}")
        sys.exit(2)
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # write back refreshed token
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds

def _header(headers, name):
    low = name.lower()
    for h in headers:
        if h.get("name", "").lower() == low:
            return h.get("value") or ""
    return ""

def main():
    creds = _get_creds()
    svc = build("gmail", "v1", credentials=creds)

    # Change query if you want. This is safe & useful.
    query = 'in:inbox newer_than:14d -category:promotions'
    r = svc.users().messages().list(userId="me", q=query, maxResults=10).execute()
    msgs = r.get("messages", [])
    if not msgs:
        print("No messages found with the query.")
        return

    print("\nRecent messages (copy IDs for your JSON):\n")
    for i, m in enumerate(msgs, 1):
        msg = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From","Subject","Message-Id","References","In-Reply-To"]
        ).execute()
        headers = msg.get("payload", {}).get("headers", [])
        frm = _header(headers, "From")
        subj = _header(headers, "Subject")
        msgid = _header(headers, "Message-Id")
        refs = _header(headers, "References") or _header(headers, "In-Reply-To")
        print(f"[{i}]")
        print(f"  thread_id:   {msg.get('threadId','')}")
        print(f"  message_id:  {msg.get('id','')}")
        print(f"  hdr_msgid:   {msgid}")
        print(f"  from:        {frm}")
        print(f"  subject:     {subj}")
        print(f"  refs:        {refs}\n")

    # Show a ready-to-paste JSON template
    last = msgs[0]["id"]
    m = svc.users().messages().get(userId="me", id=last, format="metadata",
        metadataHeaders=["Subject","Message-Id","References","In-Reply-To"]).execute()
    headers = m.get("payload", {}).get("headers", [])
    subj = _header(headers, "Subject")
    msgid = _header(headers, "Message-Id")
    refs = _header(headers, "References") or _header(headers, "In-Reply-To")

    print("Example snippet for req-gmail-draft.json:\n")
    print(json.dumps({
      "user_id":"u1",
      "task_type":"g.gmail_draft_reply",
      "prompt":"Reply briefly to the latest message in the thread.",
      "quality_floor":3,
      "expected_output_tokens":180,
      "integration":{
        "kind":"g.gmail_draft_reply",
        "extra":{
          "thread_id": m.get("threadId",""),
          "in_reply_to_msgid": msgid,
          "refs": refs,
          "subject": f"Re: {subj}" if subj else "Re:"
        }
      }
    }, indent=2, ensure_ascii=False))
    print("\nPaste the snippet above into your req-gmail-draft.json and run it.")
    print("Tip: You can replace thread_id/in_reply_to_msgid/refs with values from any listed message.")
    
if __name__ == "__main__":
    main()
