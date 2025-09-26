# api/integrations/gmail_direct.py
import os
import base64
from pathlib import Path
from typing import Optional, Dict

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from email.message import EmailMessage

# Accept both env vars (preferred) and sensible defaults used in your stack
TOKEN_PATH = Path(os.getenv("GOOGLE_TOKEN_PATH", "/app/state/google_token.json"))
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]

def _get_creds() -> Credentials:
    if not TOKEN_PATH.exists():
        raise RuntimeError(f"Gmail token not found: {TOKEN_PATH}")
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # persist the refresh
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds

def _header(headers, name: str) -> str:
    low = name.lower()
    for h in headers or []:
        if (h.get("name") or "").lower() == low:
            return h.get("value") or ""
    return ""

def get_newest_message_info(query: Optional[str] = None) -> Dict[str, str]:
    """Return newest message with IDs + headers needed for threaded reply."""
    creds = _get_creds()
    svc = build("gmail", "v1", credentials=creds)

    q = query or 'in:inbox newer_than:14d -category:promotions'
    li = svc.users().messages().list(userId="me", q=q, maxResults=1).execute()
    msgs = li.get("messages", [])
    if not msgs:
        raise RuntimeError("No recent Gmail messages found with the query.")

    msg = svc.users().messages().get(
        userId="me",
        id=msgs[0]["id"],
        format="metadata",
        metadataHeaders=["From","Subject","Message-Id","References","In-Reply-To","To","Date"]
    ).execute()

    headers = msg.get("payload", {}).get("headers", [])
    return {
        "gmail_id": msg.get("id",""),
        "thread_id": msg.get("threadId",""),
        "from": _header(headers, "From"),
        "to": _header(headers, "To"),
        "subject": _header(headers, "Subject"),
        "hdr_msgid": _header(headers, "Message-Id"),
        "refs": _header(headers, "References") or _header(headers, "In-Reply-To"),
        "date": _header(headers, "Date"),   # <-- add this

    }

def create_reply_draft(
    body_text: str,
    thread_id: str,
    hdr_msgid: Optional[str],
    refs: Optional[str],
    subject: Optional[str],
    to: Optional[str],
) -> str:
    """Create a draft reply in the given thread. Returns draft ID."""
    creds = _get_creds()
    svc = build("gmail", "v1", credentials=creds)

    em = EmailMessage()
    # Use 'To' for safety; Gmail GUI infers, API drafts appreciate explicit headers
    if to:
        em["To"] = to
    # Subject prefixed if needed
    if subject:
        em["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if hdr_msgid:
        em["In-Reply-To"] = hdr_msgid
    if refs:
        em["References"] = refs

    em.set_content(body_text)

    raw = base64.urlsafe_b64encode(em.as_bytes()).decode("utf-8")
    draft = svc.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw, "threadId": thread_id}}
    ).execute()

    return draft.get("id", "")
    
def reply_to_newest(body_text: str, query: Optional[str] = None) -> str:
    """Fetch newest message and create a reply draft in its thread. Return draft ID."""
    info = get_newest_message_info(query=query)
    if not info.get("thread_id"):
        raise RuntimeError("Newest message has no thread_id (unexpected).")
    draft_id = create_reply_draft(
        body_text=body_text,
        thread_id=info["thread_id"],
        hdr_msgid=info.get("hdr_msgid"),
        refs=info.get("refs"),
        subject=info.get("subject"),
        to=info.get("from") or None,   # reply back to the sender
    )
    return draft_id

# --- Add below existing code in api/integrations/gmail_direct.py ---

import re
from html import unescape

def _decode_part_data(data_b64: str) -> str:
    if not data_b64:
        return ""
    try:
        import base64
        return base64.urlsafe_b64decode(data_b64.encode("utf-8")).decode("utf-8", errors="ignore")
    except Exception:
        return ""

def _html_to_text(html: str) -> str:
    # very light HTML->text: strip tags, unescape entities
    txt = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html or "")
    txt = re.sub(r"(?is)<br\s*/?>", "\n", txt)
    txt = re.sub(r"(?is)</p\s*>", "\n\n", txt)
    txt = re.sub(r"(?is)<.*?>", "", txt)
    return unescape(txt)

def get_message_body_text(message_id: str) -> str:
    """Return best-effort plain text body for a Gmail message."""
    creds = _get_creds()
    svc = build("gmail", "v1", credentials=creds)

    msg = svc.users().messages().get(
        userId="me",
        id=message_id,
        format="full"
    ).execute()

    payload = msg.get("payload", {}) or {}
    # strategy: collect text/plain; if none, fall back to text/html (stripped)
    def gather_parts(p):
        if not isinstance(p, dict):
            return []
        parts = p.get("parts")
        if parts:
            out = []
            for sp in parts:
                out.extend(gather_parts(sp))
            return out
        else:
            mime = p.get("mimeType", "")
            data = _decode_part_data((p.get("body") or {}).get("data", ""))
            return [(mime, data)] if data else []

    parts = gather_parts(payload)
    # prefer text/plain
    for mime, data in parts:
        if mime.startswith("text/plain") and data.strip():
            return data
    # else try text/html
    for mime, data in parts:
        if mime.startswith("text/html") and data.strip():
            return _html_to_text(data)

    # fallback: snippet
    snip = msg.get("snippet", "")
    return snip or ""

def reply_to_newest_with_meta(body_text: str, query: str | None = None,
                              add_quote: bool = False, quote_chars: int = 600):
    """
    Create a reply draft to the newest message and return (draft_id, meta_dict).
    meta_dict contains: from, subject, date, thread_id, gmail_id
    If add_quote=True, append a quoted snippet from the original message to body_text.
    """
    info = get_newest_message_info(query=query)
    # Extend info with date (we already return in get_newest_message_info if you added it)
    # If your get_newest_message_info doesn't return date yet, extend it:
    #   add metadataHeaders=["From","Subject","Message-Id","References","In-Reply-To","To","Date"]
    #   and include "date": _header(headers, "Date"),
    if "date" not in info:
        info["date"] = ""

    final_body = body_text
    if add_quote:
        orig = get_message_body_text(info["gmail_id"]) or ""
        if orig:
            # trim and quote
            trimmed = orig.strip()
            if quote_chars and len(trimmed) > quote_chars:
                trimmed = trimmed[:quote_chars] + "\n[...trimmed...]"
            quoted = "\n".join(["> " + line for line in trimmed.splitlines()])
            prefix = f"\n\nOn {info.get('date','')}, {info.get('from','')} wrote:\n"
            final_body = body_text.rstrip() + prefix + quoted

    draft_id = create_reply_draft(
        body_text=final_body,
        thread_id=info["thread_id"],
        hdr_msgid=info.get("hdr_msgid"),
        refs=info.get("refs"),
        subject=info.get("subject"),
        to=info.get("from"),
    )
    # Return both for better UI/telemetry
    return draft_id, {
        "from": info.get("from", ""),
        "subject": info.get("subject", ""),
        "date": info.get("date", ""),
        "thread_id": info.get("thread_id", ""),
        "gmail_id": info.get("gmail_id", "")
    }

