# api/main.py
# FastAPI Router: picks a cheap local model via Ollama and runs optional integrations.
# Hardened for low-spec laptops:
# - Configurable Ollama timeouts
# - Retry with backoff + fallback model
# - Triage prompt uses strict format AND trimmed context
# - Warmup endpoint to pre-load model weights so first call doesn't time out
# - Prefetch errors are caught and returned as readable errors (no silent 500s)

import os
import time
import importlib
from typing import Any, Dict, Optional, Callable

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import yaml

from googleapiclient.errors import HttpError
from api.integrations.google_ws import gmail_fetch_newest_thread, gmail_create_draft_reply, gdocs_create_from_text

from api.integrations.pipedrive import (
    stalled_deals_report,
    inbox_lead_actions_prefetch,   # already used for inbox triage
    pd_mail_lead_prefetch,         # NEW: PD mailbox lead decision prefetch
    pd_thread_context_prefetch,    # NEW: PD mailbox thread transcript prefetch
    summarize_pdmail_thread_and_note,  # NEW: PD note writer for thread summary
)


# ---- Model registry + cost-aware selection (reads config/price_table.yaml) ----
PRICE_TABLE_PATH = os.getenv("PRICE_TABLE_PATH", "config/price_table.yaml")

def _load_price_table() -> dict:
    try:
        with open(PRICE_TABLE_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {"models": [], "policy": {"cache_seconds": 0, "retry": {"max_attempts": 2, "initial_backoff_ms": 400, "multiplier": 2.0}}}

_PRICE_TABLE = _load_price_table()
_MODELS = _PRICE_TABLE.get("models", [])
_POLICY = _PRICE_TABLE.get("policy", {}) or {}

def _has_key_for(provider: str) -> bool:
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    if provider == "mistral":
        return bool(os.getenv("MISTRAL_API_KEY"))
    if provider == "google":
        return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_GENAI_API_KEY"))
    return False

def _chars_to_tokens(chars: int) -> int:
    # cheap heuristic; safe enough for budgeting (≈ 4 chars/token)
    return max(1, int(chars / 4))

def _estimate_tokens(prompt: str, expected_out_tokens: int) -> tuple[int,int]:
    ptoks = _chars_to_tokens(len(prompt))
    otoks = max(1, int(expected_out_tokens))
    return ptoks, otoks

def _estimate_cost(model_row: dict, in_tokens: int, out_tokens: int) -> float:
    pi = float(model_row.get("price_in_per_1k", 0.0))
    po = float(model_row.get("price_out_per_1k", 0.0))
    return (in_tokens/1000.0)*pi + (out_tokens/1000.0)*po

def _pick_model_by_policy(prompt: str, expected_out_tokens: int, quality_floor: int) -> dict:
    """
    Decide the cheapest viable model that meets quality_floor and available API keys.
    Always keep local (ollama) options in play (free).
    Return the selected model row (dict). Callers decide which caller shim to use.
    """
    in_toks, out_toks = _estimate_tokens(prompt, expected_out_tokens)

    # step 1: gather viable models (quality >= floor and has key if hosted)
    viable = []
    for m in _MODELS:
        q = int(m.get("baseline_quality", 1))
        if q < quality_floor:
            continue
        if m.get("provider") != "ollama" and not _has_key_for(m.get("provider")):
            continue
        # annotate with estimated cost now
        est = _estimate_cost(m, in_toks, out_toks)
        viable.append((est, m))

    # if nothing viable (e.g., floor too high), fall back to ANY local
    if not viable:
        fallbacks = [m for m in _MODELS if m.get("provider") == "ollama"]
        if fallbacks:
            return fallbacks[0]
        # last ditch: return a tiny local default
        return {"provider": "ollama", "model": "tinyllama", "baseline_quality": 2}

    # choose the cheapest among viable (cost estimate only guides selection)
    viable.sort(key=lambda x: x[0])
    return viable[0][1]





# ---- Debug helper for Gmail shapes (enabled when DEBUG_GMAIL=1) ----
def _gmail_debug_shape(tag: str, data: Any) -> None:
    if os.getenv("DEBUG_GMAIL", "0") != "1":
        return
    try:
        import json
        def skim(x, depth=0):
            if depth > 2:  # avoid huge dumps
                return type(x).__name__
            if isinstance(x, dict):
                return {k: skim(v, depth+1) for k, v in list(x.items())[:10]}
            if isinstance(x, list):
                return [skim(v, depth+1) for v in x[:5]]
            return x if isinstance(x, (str, int, float, bool)) else type(x).__name__
        print(f"[GMAIL-DEBUG] {tag}: {json.dumps(skim(data))[:1200]}")
    except Exception:
        pass

def _extract_email(addr: str) -> str:
    """Return the email address from 'Name <email@x>' or raw email if no angle brackets."""
    if not addr:
        return ""
    s = addr.strip()
    if "<" in s and ">" in s:
        try:
            return s.split("<", 1)[1].split(">", 1)[0].strip()
        except Exception:
            pass
    return s


def _gmail_create_draft_flexible(
    body_text: str,
    *,
    to: str | list[str] | None = None,
    subject: str | None = None,
    thread_id: str | None = None,
    in_reply_to_msgid: str | None = None,
    refs: str | list[str] | None = None,
):
    """
    Try many signatures for gmail_create_draft_reply; if not compatible, try gmail_create_draft.
    Accepts recipient as str or list; returns a string draft id.
    """
    last_err = None

    # normalize recipients
    to_list: list[str] | None = None
    if isinstance(to, str) and to.strip():
        to_list = [_extract_email(to)]
    elif isinstance(to, list) and to:
        to_list = [_extract_email(t) for t in to if t and str(t).strip()]

    def _normalize_return(x):
        if isinstance(x, str) and x.strip():
            return x.strip()
        if isinstance(x, dict):
            return x.get("draft_id") or x.get("id") or x.get("draftId") or x.get("messageId")
        return None

    # --- 1) Try gmail_create_draft_reply with many kwarg/positional forms ---
    try:
        # ID-based reply (best when thread exists)
        if thread_id or in_reply_to_msgid:
            for kw in (
                {"thread_id": thread_id, "subject": subject, "body_text": body_text, "refs": refs},
                {"in_reply_to_msgid": in_reply_to_msgid, "subject": subject, "body_text": body_text, "refs": refs},
                {"thread_id": thread_id, "subject": subject, "body": body_text, "refs": refs},
                {"in_reply_to_msgid": in_reply_to_msgid, "subject": subject, "body": body_text, "refs": refs},
            ):
                kw = {k: v for k, v in kw.items() if v}
                if kw:
                    try:
                        rid = _normalize_return(gmail_create_draft_reply(**kw))
                        if rid:
                            return rid
                    except Exception as e:
                        last_err = e

        # Recipient-based (no thread ids)
        if to_list:
            for kw in (
                {"to": to_list[0], "subject": subject, "body_text": body_text},
                {"recipient": to_list[0], "subject": subject, "body_text": body_text},
                {"to_email": to_list[0], "subject": subject, "body_text": body_text},
                {"to_addrs": to_list, "subject": subject, "body_text": body_text},
                {"to": to_list[0], "subject": subject, "body": body_text},
                {"recipient": to_list[0], "subject": subject, "body": body_text},
                {"to_email": to_list[0], "subject": subject, "body": body_text},
                {"to_addrs": to_list, "subject": subject, "body": body_text},
            ):
                try:
                    rid = _normalize_return(gmail_create_draft_reply(**kw))
                    if rid:
                        return rid
                except Exception as e:
                    last_err = e

        # Some helpers accept positional (subject, body)
        if subject:
            try:
                rid = _normalize_return(gmail_create_draft_reply(subject, body_text))  # positional
                if rid:
                    return rid
            except Exception as e:
                last_err = e
    except Exception as e:
        last_err = e

    # --- 2) Try a different function name if present: gmail_create_draft ---
    try:
        # import here to avoid failing earlier if function is absent
        from api.integrations.google_ws import gmail_create_draft  # type: ignore
        # recipient-based
        if to_list:
            for kw in (
                {"to": to_list[0], "subject": subject, "body_text": body_text},
                {"recipient": to_list[0], "subject": subject, "body_text": body_text},
                {"to_email": to_list[0], "subject": subject, "body_text": body_text},
                {"to_addrs": to_list, "subject": subject, "body_text": body_text},
                {"to": to_list[0], "subject": subject, "body": body_text},
                {"recipient": to_list[0], "subject": subject, "body": body_text},
                {"to_email": to_list[0], "subject": subject, "body": body_text},
                {"to_addrs": to_list, "subject": subject, "body": body_text},
            ):
                try:
                    rid = _normalize_return(gmail_create_draft(**kw))
                    if rid:
                        return rid
                except Exception as e:
                    last_err = e
        # positional variants
        if subject and to_list:
            try:
                rid = _normalize_return(gmail_create_draft(to_list[0], subject, body_text))
                if rid:
                    return rid
            except Exception as e:
                last_err = e
        if subject:
            try:
                rid = _normalize_return(gmail_create_draft(subject, body_text))
                if rid:
                    return rid
            except Exception as e:
                last_err = e
    except Exception as e:
        # module may not provide gmail_create_draft; ignore
        last_err = e

    raise RuntimeError(f"gmail_create_draft(_reply) not compatible with known signatures; last error: {last_err}")


# ---- Gmail adapters (signature- & shape-tolerant) ----
def _gmail_fetch_newest_thread_flexible(n_threads: int = 1, lookback_days: int = 14):
    """
    Call google_ws.gmail_fetch_newest_thread with whatever signature it supports.
    Tries multiple kwarg spellings, then positional, then no-args. Accepts tuple returns.
    """
    variants = [
        {"n_threads": n_threads, "lookback_days": lookback_days},
        {"limit": n_threads, "days": lookback_days},
        {"limit": n_threads},
        {"n": n_threads, "days": lookback_days},
        {"count": n_threads, "days": lookback_days},
        {"max_threads": n_threads, "lookback_days": lookback_days},
    ]
    last_exc = None
    for kwargs in variants:
        try:
            out = gmail_fetch_newest_thread(**kwargs)
            # accept tuple returns (data, meta)
            out = out[0] if isinstance(out, tuple) and out else out
            _gmail_debug_shape(f"fetch kwargs={kwargs}", out)
            return out
        except Exception as e:
            last_exc = e

    # positional try
    try:
        out = gmail_fetch_newest_thread(n_threads, lookback_days)
        out = out[0] if isinstance(out, tuple) and out else out
        _gmail_debug_shape("fetch positional", out)
        return out
    except Exception as e:
        last_exc = e

    # last resort: no-arg
    try:
        out = gmail_fetch_newest_thread()
        out = out[0] if isinstance(out, tuple) and out else out
        _gmail_debug_shape("fetch no-args", out)
        return out
    except Exception as e:
        last_exc = e

    raise RuntimeError(f"gmail_fetch_newest_thread() incompatible with tried signatures; last error: {last_exc}")


def _gmail_pick_last_message(data: dict | list | str) -> dict:
    """
    Pick the newest/last message from many possible shapes returned by gmail_fetch_newest_thread.
    Returns: {from, subject, message_id, thread_id, references}
    Strategy:
      0) If `data` is a pre-rendered string (like g.gmail_summarize output), parse the last item.
      1) Try known containers (threads[0].messages, messages, items, data).
      2) If empty, recursively deep-scan for a plausible "message-like" dict with headers.
      3) Extract From/Subject/Message-Id/ThreadId from payload.headers or known keys.
    """
    # 0) Handle pre-rendered string format (e.g., lines like "[1] From: ... | Subject: ... | Date: ...")
    if isinstance(data, str):
        import re
        lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
        cand = None
        for ln in reversed(lines):
            if re.match(r"^\[\d+\]\s+From:", ln):
                cand = ln
                break
        if cand:
            parts = [p.strip() for p in cand.split("|")]
            frm, subj = "", ""
            for p in parts:
                if p.lower().startswith("from:"):
                    frm = p.split(":", 1)[1].strip()
                elif p.lower().startswith("subject:"):
                    subj = p.split(":", 1)[1].strip()
            return {"from": frm, "subject": subj, "message_id": "", "thread_id": "", "references": None}
        # fall through to generic logic if we couldn't parse the string

    def _as_list(x):
        return x if isinstance(x, list) else []

    def _pull_headers(obj: dict) -> list:
        if isinstance(obj.get("payload"), dict) and isinstance(obj["payload"].get("headers"), list):
            return obj["payload"]["headers"]
        if isinstance(obj.get("headers"), list):
            return obj["headers"]
        return []

    def _header(headers: list, name: str) -> Optional[str]:
        low = name.lower()
        for h in headers:
            try:
                if str(h.get("name", "")).lower() == low:
                    return h.get("value")
            except Exception:
                continue
        return None

    def _extract_msg(msg: dict) -> dict:
        headers = _pull_headers(msg)
        sender = msg.get("from") or (headers and _header(headers, "From")) or ""
        subject = msg.get("subject") or (headers and _header(headers, "Subject")) or ""
        msg_id = msg.get("message_id") or msg.get("id") or (headers and _header(headers, "Message-Id")) or ""
        thread_id = msg.get("thread_id") or msg.get("threadId") or ""
        refs = msg.get("references") or (headers and (_header(headers, "References") or _header(headers, "In-Reply-To"))) or None
        return {
            "from": sender or "",
            "subject": subject or "",
            "message_id": msg_id or "",
            "thread_id": thread_id or "",
            "references": refs,
        }

    # 1) “straight” shapes
    msgs = []
    if isinstance(data, dict):
        threads = _as_list(data.get("threads") or data.get("items") or data.get("data"))
        if threads:
            t0 = threads[0]
            msgs = _as_list(
                t0.get("messages")
                or t0.get("msgs")
                or (t0.get("message") if isinstance(t0.get("message"), list) else [])
                or (t0.get("payloads") if isinstance(t0.get("payloads"), list) else [])
            )
        if not msgs:
            msgs = _as_list(data.get("messages") or data.get("items") or data.get("data"))
    elif isinstance(data, list):
        msgs = _as_list(data)

    if msgs:
        last = msgs[-1]
        out = _extract_msg(last) if isinstance(last, dict) else {
            "from": "", "subject": "", "message_id": "", "thread_id": "", "references": None
        }
        _gmail_debug_shape("pick_last straight", out)
        return out

    # 2) deep scan for any message-like dict (payload.headers present or suggestive keys)
    found: Optional[dict] = None

    def _looks_like_message(obj: Any) -> bool:
        if not isinstance(obj, dict):
            return False
        if "payload" in obj and isinstance(obj.get("payload"), dict) and isinstance(obj["payload"].get("headers"), list):
            return True
        if any(k in obj for k in ("from", "subject", "message_id", "id", "thread_id", "threadId", "headers")):
            return True
        return False

    def _scan(x: Any):
        nonlocal found
        if found is not None:
            return
        if isinstance(x, dict):
            if _looks_like_message(x):
                found = x
                return
            for v in x.values():
                _scan(v)
        elif isinstance(x, list):
            for v in x:
                _scan(v)

    _scan(data)
    if found:
        out = _extract_msg(found)
        _gmail_debug_shape("pick_last deep", out)
        return out

    # 3) give up
    _gmail_debug_shape("pick_last empty", data)
    return {"from": "", "subject": "", "message_id": "", "thread_id": "", "references": None}


def _gmail_thread_to_text(data: Any, max_chars: int = 4000) -> str:
    """
    Normalize various gmail_fetch_newest_thread outputs into a compact text block.
    Accepts: str | dict | list | tuple. Returns a string (possibly trimmed).
    """
    # Unpack tuple returns (data, meta)
    if isinstance(data, tuple) and data:
        data = data[0]

    # If the helper already returns a plain string digest, use it directly
    if isinstance(data, str):
        text = data
        return text[:max_chars] + "\n[... trimmed ...]" if len(text) > max_chars else text

    # Otherwise, try to compact a dict/list structure into readable text
    def _safe_get(d, k, default=""):
        try:
            return d.get(k, default)
        except Exception:
            return default

    lines: list[str] = []

    # Common dict shape: {"threads":[{"messages":[{message}] }]}
    if isinstance(data, dict):
        threads = _safe_get(data, "threads") or _safe_get(data, "items") or _safe_get(data, "data") or []
        if isinstance(threads, list) and threads:
            t0 = threads[0]
            msgs = (
                _safe_get(t0, "messages")
                or _safe_get(t0, "msgs")
                or (t0.get("message") if isinstance(t0.get("message"), list) else [])
                or (t0.get("payloads") if isinstance(t0.get("payloads"), list) else [])
                or []
            )
        else:
            msgs = _safe_get(data, "messages") or _safe_get(data, "items") or _safe_get(data, "data") or []
        if isinstance(msgs, list) and msgs:
            for i, m in enumerate(msgs, 1):
                frm = (m.get("from") if isinstance(m, dict) else "") or ""
                subj = (m.get("subject") if isinstance(m, dict) else "") or ""
                date = (m.get("date") if isinstance(m, dict) else "") or ""
                snippet = (m.get("snippet") if isinstance(m, dict) else "") or ""
                body = (m.get("text") if isinstance(m, dict) else "") or ""
                lines.append(f"[{i}] From: {frm} | Subject: {subj} | Date: {date}")
                if snippet:
                    lines.append(f"Preview: {snippet}")
                if body:
                    lines.append(f"---\n{body[:1500]}")
        else:
            # Fallback: stringify compactly
            import json
            lines.append(json.dumps(data)[:max_chars])

    elif isinstance(data, list):
        # Treat as a list of messages
        for i, m in enumerate(data[:10], 1):
            if isinstance(m, dict):
                frm = m.get("from", "")
                subj = m.get("subject", "")
                date = m.get("date", "")
                snippet = m.get("snippet", "")
                body = m.get("text", "")
                lines.append(f"[{i}] From: {frm} | Subject: {subj} | Date: {date}")
                if snippet:
                    lines.append(f"Preview: {snippet}")
                if body:
                    lines.append(f"---\n{body[:1500]}")
            else:
                lines.append(str(m))

    else:
        # Unknown shape—stringify
        lines.append(str(data))

    text = "\n".join(lines).strip() or "(no Gmail messages found)"
    return text[:max_chars] + "\n[... trimmed ...]" if len(text) > max_chars else text


# ---- Direct Gmail API helpers (bypass unknown helper signatures) ----
def _gmail_build_service_from_token():
    """
    Build a Gmail service using the existing token JSON. Only reads the file.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token_path = os.getenv("GOOGLE_TOKEN_PATH") or os.getenv("GOOGLE_OAUTH_TOKEN_JSON")
    if not token_path:
        raise RuntimeError("GOOGLE_TOKEN_PATH not set")

    scopes = [
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.modify",
    ]
    creds = Credentials.from_authorized_user_file(token_path, scopes=scopes)
    if creds and creds.expired and creds.refresh_token:
        # refresh happens in-memory; no write to disk
        creds.refresh(Request())

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _gmail_create_draft_via_api(
    *,
    to: str,
    subject: str,
    body_text: str,
    thread_id: str | None = None,
    in_reply_to_msgid: str | None = None,
    refs: str | list[str] | None = None,
) -> str:
    """
    Create a Gmail draft directly via Gmail API. Returns draft id.
    """
    from email.mime.text import MIMEText
    import base64

    svc = _gmail_build_service_from_token()

    msg = MIMEText(body_text, "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if in_reply_to_msgid:
        msg["In-Reply-To"] = in_reply_to_msgid
    if refs:
        msg["References"] = refs if isinstance(refs, str) else " ".join(refs)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    payload = {"message": {"raw": raw}}
    if thread_id:
        payload["message"]["threadId"] = thread_id

    draft = svc.users().drafts().create(userId="me", body=payload).execute()
    return draft.get("id") or draft.get("draft", {}).get("id") or ""


def _gmail_fetch_newest_thread_direct(n_threads: int = 1, lookback_days: int = 14) -> dict:
    """
    Fetch newest Gmail thread(s) directly via Gmail API using the stored token.
    Returns a dict shaped like {"threads": [{"messages": [ ... ]} ]} which
    _gmail_thread_to_text already understands.
    """
    svc = _gmail_build_service_from_token()

    # Safe default query: last X days, inbox only, skip promotions to keep noise low
    q = f"newer_than:{int(lookback_days)}d -category:promotions"
    res = svc.users().messages().list(userId="me", q=q, labelIds=["INBOX"], maxResults=max(1, n_threads)).execute()
    msg_list = res.get("messages", [])
    if not msg_list:
        return {"threads": []}

    threads_out = []
    for msg_meta in msg_list[:n_threads]:
        # Get the full message to learn the threadId
        full_msg = svc.users().messages().get(userId="me", id=msg_meta["id"], format="full").execute()
        thread_id = full_msg.get("threadId")
        if not thread_id:
            continue

        # Pull the whole thread
        th = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        msgs_norm = []

        for m in th.get("messages", []):
            # Extract headers we need
            hdrs = {h.get("name",""): h.get("value","") for h in (m.get("payload",{}) or {}).get("headers", [])}
            frm = hdrs.get("From", "")
            subj = hdrs.get("Subject", "")
            date = hdrs.get("Date", "")
            snippet = m.get("snippet", "")

            # Extract plain text body (best-effort)
            def _walk_parts(p):
                if not p: return ""
                mime = p.get("mimeType","")
                body = p.get("body", {})
                data = body.get("data")
                if data and ("text/plain" in mime or (not mime and data)):
                    import base64
                    try:
                        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")
                    except Exception:
                        return ""
                for ch in p.get("parts",[]) or []:
                    txt = _walk_parts(ch)
                    if txt: return txt
                return ""

            text_body = _walk_parts(m.get("payload", {}))
            msgs_norm.append({
                "from": frm,
                "subject": subj,
                "date": date,
                "snippet": snippet,
                "text": text_body,
                "id": m.get("id",""),
                "threadId": thread_id,
            })

        if msgs_norm:
            threads_out.append({"messages": msgs_norm})

    return {"threads": threads_out}


def _gmail_create_draft_reply_flexible(
    *, subject: str, body_text: str,
    to: str | None = None,
    thread_id: str | None = None,
    in_reply_to_msgid: str | None = None,
    refs: str | list[str] | None = None,
):
    """
    Call google_ws.gmail_create_draft_reply using many common signatures.
    Tries id-based first (thread_id / in_reply_to_msgid), then recipient-based
    with multiple kwname variants, and body_text/body variants.
    Returns draft_id (string) or raises the last error.
    """
    last_exc = None

    # 1) Prefer thread-id based replies (most robust)
    for kw in (
        {"thread_id": thread_id, "subject": subject, "body_text": body_text},
        {"threadId": thread_id, "subject": subject, "body_text": body_text},
        {"in_reply_to_msgid": in_reply_to_msgid, "refs": refs, "subject": subject, "body_text": body_text},
        {"in_reply_to_msgid": in_reply_to_msgid, "subject": subject, "body_text": body_text},  # no refs
        # body_text -> body fallbacks
        {"thread_id": thread_id, "subject": subject, "body": body_text},
        {"threadId": thread_id, "subject": subject, "body": body_text},
        {"in_reply_to_msgid": in_reply_to_msgid, "refs": refs, "subject": subject, "body": body_text},
        {"in_reply_to_msgid": in_reply_to_msgid, "subject": subject, "body": body_text},
    ):
        if not any(kw.get(k) for k in ("thread_id", "threadId", "in_reply_to_msgid")):
            continue
        try:
            # drop None values
            clean = {k: v for k, v in kw.items() if v not in (None, "", [])}
            return gmail_create_draft_reply(**clean)
        except TypeError as e:
            last_exc = e
        except Exception as e:
            last_exc = e

    # 2) Recipient-based variants (if we have a 'to')
    if to:
        for kw in (
            {"to": to, "subject": subject, "body_text": body_text},
            {"recipient": to, "subject": subject, "body_text": body_text},
            {"to_addr": to, "subject": subject, "body_text": body_text},
            {"to_email": to, "subject": subject, "body_text": body_text},
            # body_text -> body fallbacks
            {"to": to, "subject": subject, "body": body_text},
            {"recipient": to, "subject": subject, "body": body_text},
            {"to_addr": to, "subject": subject, "body": body_text},
            {"to_email": to, "subject": subject, "body": body_text},
            # positional (defensive)
            {"_positional": (to, subject, body_text)},
        ):
            try:
                if "_positional" in kw:
                    a = kw["_positional"]
                    return gmail_create_draft_reply(*a)
                clean = {k: v for k, v in kw.items() if k != "_positional" and v not in (None, "", [])}
                return gmail_create_draft_reply(**clean)
            except TypeError as e:
                last_exc = e
            except Exception as e:
                last_exc = e

    # 3) As a last resort, some helpers accept positional (subject, body) only
    try:
        return gmail_create_draft_reply(subject, body_text)  # positional
    except Exception as e:
        last_exc = e

    raise RuntimeError(
        f"gmail_create_draft_reply failed with all known signatures; last error: {last_exc}"
    )


# ---- Direct Gmail API helpers (bypass unknown helper signatures) ----
def _gmail_build_service_from_token():
    """
    Build a Gmail service using the existing token JSON. Only reads the file.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token_path = os.getenv("GOOGLE_TOKEN_PATH") or os.getenv("GOOGLE_OAUTH_TOKEN_JSON")
    if not token_path:
        raise RuntimeError("GOOGLE_TOKEN_PATH not set")

    scopes = [
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.modify",
    ]
    creds = Credentials.from_authorized_user_file(token_path, scopes=scopes)
    if creds and creds.expired and creds.refresh_token:
        # refresh happens in-memory; no write to disk required
        creds.refresh(Request())

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _gmail_create_draft_via_api(
    *,
    to: str,
    subject: str,
    body_text: str,
    thread_id: str | None = None,
    in_reply_to_msgid: str | None = None,
    refs: str | list[str] | None = None,
) -> str:
    """
    Create a Gmail draft directly via Gmail API. Returns the draft id.
    """
    from email.mime.text import MIMEText
    import base64

    svc = _gmail_build_service_from_token()

    msg = MIMEText(body_text, "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if in_reply_to_msgid:
        msg["In-Reply-To"] = in_reply_to_msgid
    if refs:
        msg["References"] = refs if isinstance(refs, str) else " ".join(refs)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    payload = {"message": {"raw": raw}}
    if thread_id:
        payload["message"]["threadId"] = thread_id

    draft = svc.users().drafts().create(userId="me", body=payload).execute()
    return draft.get("id") or draft.get("draft", {}).get("id") or ""


def _build_gmail_summary_prompt(context: str, user_prompt: str) -> str:
    return f"""
You are given a Gmail thread below. Summarize crisply for a busy person.

[THREAD]
{context}
[/THREAD]

Requirements:
- 5–8 bullets covering decisions, asks, key dates, blockers.
- Keep it terse and factual; no fluff.
- Preserve the original language of the thread.
- End with a final line: Next step: <one short actionable sentence>.

User note: {user_prompt}
"""


APP_NAME = "LLM Router API"

# ----- Runtime config (env or sane defaults) -----
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")

# Timeouts (seconds). On low-spec machines, first generation can take minutes.
READ_TIMEOUT_S = int(os.getenv("OLLAMA_READ_TIMEOUT_S", "600"))   # 10 minutes
CONNECT_TIMEOUT_S = int(os.getenv("OLLAMA_CONNECT_TIMEOUT_S", "10"))
WRITE_TIMEOUT_S = int(os.getenv("OLLAMA_WRITE_TIMEOUT_S", "30"))

# Retry policy
RETRIES = int(os.getenv("OLLAMA_RETRIES", "2"))
RETRY_BACKOFF_S = float(os.getenv("OLLAMA_RETRY_BACKOFF_S", "5.0"))

# Triage-specific trims
TRIAGE_DEFAULT_N = int(os.getenv("TRIAGE_DEFAULT_N", "3"))  # default fewer emails for slow CPUs
TRIAGE_LOOKBACK_DAYS = int(os.getenv("TRIAGE_LOOKBACK_DAYS", "30"))
TRIAGE_MAX_CONTEXT_CHARS = int(os.getenv("TRIAGE_MAX_CONTEXT_CHARS", "4000"))  # cap context length

# Model policy presets
MODEL_TRIAGE_PRIMARY = os.getenv("MODEL_TRIAGE_PRIMARY", "phi3:mini")
MODEL_TRIAGE_FALLBACK = os.getenv("MODEL_TRIAGE_FALLBACK", "tinyllama")


app = FastAPI(title=APP_NAME)


class IntegrationSpec(BaseModel):
    kind: str
    file_name: Optional[str] = None
    folder: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class RouteRequest(BaseModel):
    user_id: str
    task_type: str
    prompt: str
    quality_floor: int = 2
    cost_ceiling_usd: float = 0.05
    expected_output_tokens: int = 128
    integration: Optional[IntegrationSpec] = None


class RouteResponse(BaseModel):
    job_id: str
    provider: str
    model: str
    output_text: str
    estimated_cost_usd: float
    latency_ms: int
    cached: bool = False
    artifact_uri: Optional[str] = None
    integration_status: Optional[str] = None


@app.get("/healthz")
def health() -> str:
    return "ok"


@app.get("/warmup")
def warmup(kind: Optional[str] = None) -> Dict[str, Any]:
    """
    Pre-load a model into RAM so first "real" call is fast.
    - kind == 'ms.mail_triage' warms the triage model, else warm a tiny default.
    """
    model = choose_model(quality_floor=3, integration_kind=kind or "")
    t0 = time.time()
    try:
        _ = call_ollama(model, "ok", timeout=make_timeout(), retries=0)  # no retries for warmup
        return {"status": "ok", "model": model, "latency_ms": int((time.time() - t0) * 1000)}
    except HTTPException as e:
        return {"status": "error", "model": model, "detail": e.detail}


def choose_model(quality_floor: int, integration_kind: Optional[str]) -> str:
    """
    Deterministic cheap-but-viable policy.
    - Special case: structured email tasks (triage, Gmail summarize/draft) need a bit more capability
    - Else: tinyllama for <=2; phi3:mini otherwise
    """
    if integration_kind in (
        "ms.mail_triage",
        "pd.inbox_lead_actions",
        "pd.mail_lead",
        "pd.thread_summary_to_pd",
        "g.gmail_summarize",   # ← added
        "g.gmail_draft_reply", # ← added (helps consistency)
    ):
        return MODEL_TRIAGE_PRIMARY
    return "tinyllama" if quality_floor <= 2 else "phi3:mini"


def make_timeout() -> httpx.Timeout:
    # Separate connect/write/read timeouts give better resilience on slow first runs
    return httpx.Timeout(
        connect=CONNECT_TIMEOUT_S,
        write=WRITE_TIMEOUT_S,
        read=READ_TIMEOUT_S,
        pool=10.0,
    )


def call_ollama(model: str, prompt: str, timeout: httpx.Timeout, retries: int = RETRIES) -> str:
    payload = {"model": model, "prompt": prompt, "stream": False}
    last_err: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
                r.raise_for_status()
                data = r.json()
                return data.get("response", "")
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.HTTPError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(RETRY_BACKOFF_S * (attempt + 1))
                continue
            # On final failure: if we were using the triage primary model, try fallback once
            if model == MODEL_TRIAGE_PRIMARY and MODEL_TRIAGE_FALLBACK:
                try:
                    with httpx.Client(timeout=timeout) as client:
                        r = client.post(
                            f"{OLLAMA_BASE_URL}/api/generate",
                            json={"model": MODEL_TRIAGE_FALLBACK, "prompt": prompt, "stream": False},
                        )
                        r.raise_for_status()
                        data = r.json()
                        return data.get("response", "")
                except Exception as e2:
                    raise HTTPException(status_code=502, detail=f"Ollama error (fallback failed): {e2}") from e2
            raise HTTPException(status_code=502, detail=f"Ollama error: {last_err}") from last_err

    # Should not reach here
    raise HTTPException(status_code=502, detail="Ollama call fell through unexpectedly")


def _lazy_import(func_path: str) -> Callable[..., Any]:
    """
    Import a function lazily from a 'module:function' string.
    Example: 'api.integrations.ms365:word_upsert_docx'
    """
    try:
        module_name, func_name = func_path.split(":")
    except ValueError:
        raise HTTPException(status_code=500, detail=f"Bad integration binding: {func_path}")

    try:
        mod = importlib.import_module(module_name)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Integration module import failed: {module_name} ({e})"
        )

    try:
        fn = getattr(mod, func_name)
    except AttributeError:
        raise HTTPException(
            status_code=500,
            detail=f"Integration function '{func_name}' not found in {module_name}"
        )
    return fn


def _prefetch_context(kind: str, spec: IntegrationSpec) -> Optional[str]:
    """
    Returns extra context text to prepend to the prompt BEFORE calling the LLM.
    Used for:
      - ms.mail_triage  (reads last N messages + minimal sender history)
      - g.gmail_summarize (reads newest Gmail thread(s) and composes a compact context)
    Any errors are turned into HTTP 502 with clear details to avoid opaque 500s.
    """

    # --- Microsoft triage ---
    if kind == "ms.mail_triage":
        try:
            n = int(spec.extra.get("n", TRIAGE_DEFAULT_N)) if spec.extra else TRIAGE_DEFAULT_N
            lookback = int(spec.extra.get("lookback_days", TRIAGE_LOOKBACK_DAYS)) if spec.extra else TRIAGE_LOOKBACK_DAYS
            fn = _lazy_import("api.integrations.ms365:build_inbox_triage_context")
            raw = fn(n=n, lookback_days=lookback)  # may raise if token missing or Graph error
            # Trim to protect small models/timeouts
            if len(raw) > TRIAGE_MAX_CONTEXT_CHARS:
                raw = raw[:TRIAGE_MAX_CONTEXT_CHARS] + "\n[... trimmed ...]"
            return raw
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Triage context error: {e}")
    
    # --- Gmail summarize: build LLM context from newest Gmail threads ---
    if kind == "g.gmail_summarize":
        try:
            n = int(spec.extra.get("n", 1)) if spec and spec.extra else 1
            lookback_days = int(spec.extra.get("lookback_days", 14)) if spec and spec.extra else 14

            # 1) Try the flexible wrapper (back-compat with previous google_ws helpers)
            try:
                data = _gmail_fetch_newest_thread_flexible(n_threads=n, lookback_days=lookback_days)
            except Exception:
                data = {}

            # 2) If nothing useful came back, use the direct Gmail API fallback
            if not data or not isinstance(data, (dict, list, tuple)) or (
                isinstance(data, dict) and not (data.get("threads") or data.get("messages") or data.get("items") or data.get("data"))
            ):
                data = _gmail_fetch_newest_thread_direct(n_threads=n, lookback_days=lookback_days)

            # 3) Normalize ANY shape to compact text
            raw = _gmail_thread_to_text(data, max_chars=TRIAGE_MAX_CONTEXT_CHARS)
            return raw or "(no Gmail messages found)"
        except HttpError as he:
            raise HTTPException(status_code=502, detail=f"Gmail API error: {he}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Gmail prefetch error: {e}")



    # --- Pipedrive: stalled deals report (simple) ---
    if kind == "pd.stalled_report":
        try:
            extra = (spec.extra or {})
            return stalled_deals_report(
                days_stalled=int(extra.get("days_stalled", 10)),
                only_missing_next_step=bool(extra.get("only_missing_next_step", True)),
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Pipedrive stalled report error: {e}")

    # --- Pipedrive: newest email lead check (uses Gmail mirror as context) ---
    if kind == "pd.mail_lead":
        try:
            extra = spec.extra or {}
            lookback_days = int(extra.get("lookback_days", os.getenv("PD_LOOKBACK_DAYS", "7")))
            return pd_mail_lead_prefetch(lookback_days=lookback_days)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Pipedrive mail lead prefetch error: {e}")

    # --- Pipedrive: summarize newest Gmail thread THEN write PD note (prefetch phase just returns text) ---
    if kind == "pd.thread_summary_to_pd":
        try:
            extra = spec.extra or {}
            lookback_days = int(extra.get("lookback_days", os.getenv("PD_LOOKBACK_DAYS", "14")))
            return pd_thread_context_prefetch(lookback_days=lookback_days)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Pipedrive thread prefetch error: {e}")

    # --- Pipedrive: analyze Pipedrive-synced inbox for lead actions ---
    if kind == "pd.inbox_lead_actions":
        try:
            extra = spec.extra or {}
            lookback_days = int(extra.get("lookback_days", os.getenv("PD_LOOKBACK_DAYS", "7")))
            max_threads = int(extra.get("max_threads", os.getenv("PD_MAX_THREADS", "10")))
            consider_if_no_reply_hours = int(extra.get("consider_if_no_reply_hours", os.getenv("PD_NO_REPLY_HOURS", "36")))
            return inbox_lead_actions_prefetch(
                lookback_days=lookback_days,
                max_threads=max_threads,
                consider_if_no_reply_hours=consider_if_no_reply_hours,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Pipedrive inbox prefetch error: {e}")

    # --- ZOHO RECRUIT (lazy import) ---
    if kind in ("zoho.resume_summarize_prefetch", "zoho.resume_eval_prefetch"):
        extra = spec.extra or {}
        b64 = extra.get("resume_b64", "")
        fname = extra.get("filename", "resume.docx")
        fn = _lazy_import("api.integrations.zoho_recruit:prefetch_resume_b64")
        return fn(b64, fname)

    # --- ZOHO RECRUIT: shortlist, base64 resumes ---
    if kind == "zoho.shortlist_prefetch":
        try:
            extra = spec.extra or {}
            candidates = extra.get("candidates") or []
            job_criteria = extra.get("job_criteria") or {}
            fn = _lazy_import("api.integrations.zoho_recruit:shortlist_prefetch")
            return fn(candidates=candidates, job_criteria=job_criteria)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Zoho shortlist prefetch error: {e}")

    # --- ZOHO RECRUIT: shortlist, fetch resumes by Candidate IDs from Zoho ---
    if kind == "zoho.shortlist_prefetch_from_zoho":
        try:
            extra = spec.extra or {}
            candidate_ids = extra.get("candidate_ids") or []
            job_criteria = extra.get("job_criteria") or {}
            fn = _lazy_import("api.integrations.zoho_recruit:shortlist_prefetch_from_zoho")
            return fn(candidate_ids=candidate_ids, job_criteria=job_criteria)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Zoho shortlist-from-Zoho prefetch error: {e}")

    # --- ZOHO RECRUIT: resume summarize from Zoho Candidate ID ---
    if kind == "zoho.resume_summarize_from_zoho":
        try:
            extra = spec.extra or {}
            cid = extra.get("candidate_id") or ""
            fn = _lazy_import("api.integrations.zoho_recruit:resume_summarize_prefetch_from_zoho")
            return fn(candidate_id=cid)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Zoho resume-from-Zoho prefetch error: {e}")


    if kind == "zoho.create_candidate_from_email":
        # Nothing to prefetch; creation happens post LLM (optional)
        return None


    return None




def _build_triage_prompt(context: str, user_prompt: str) -> str:
    """
    Construct a strict, structured prompt so small local models produce the fields we need.
    We include a tiny example and require a fixed format and language rule.
    """
    return f"""
[BEGIN CONTEXT]
{context}
[END CONTEXT]

[INSTRUCTIONS]
You are an assistant that performs inbox triage ONLY based on the CONTEXT above.
For each message [i], you MUST produce the following exact fields:

- QuickAction: yes|no — one short reason.
- LeadPotential: high|medium|low — one short reason.
- ReplyDraft: 3–6 short sentences, in the same language as the message. If not appropriate, write: None.

Language rule: respond in the same language as the original message (Polish vs English, etc).

[EXAMPLE OUTPUT FORMAT]
- [1]
  QuickAction: yes — sender requested next steps.
  LeadPotential: medium — business tone and product interest.
  ReplyDraft: Dziękuję za wiadomość! Potwierdzam odbiór i proponuję krótkie spotkanie jutro...
- [2]
  QuickAction: no — purely informational.
  LeadPotential: low — no buying intent.
  ReplyDraft: None

[USER REQUEST]
{user_prompt}

[OUTPUT REQUIREMENTS]
- Use the exact bullet format as in the example.
- Keep answers concise.
- Do NOT add any sections other than the bullet list.
"""

def _build_pd_actions_prompt(context: str, user_prompt: str) -> str:
    return f"""
[CONTEXT]
{context}
[/CONTEXT]

[INSTRUCTIONS]
For each [DEAL] block above, produce exactly this format:

- [DealTitle]
  Action: reply | schedule | nudge | close | none — one short reason (max 20 words).
  SuggestedSubject: short subject (<= 8 words).
  ReplyDraft: 3–6 short sentences (same language as the emails). If action != reply, write: None.

Rules:
- Be concise and practical.
- Prefer "reply" if the last inbound email asks anything or expects a response.
- Prefer "schedule" if a meeting is needed to move forward.
- Prefer "nudge" only if prior reply was sent but no response; keep polite.
- Prefer "close" only if clearly no interest.
- If no action is needed, use "none".

[REQUEST]
{user_prompt}

[OUTPUT]
Use exactly the bullet format shown above. No extra sections.
"""

def _build_pd_mailtriage_prompt(context: str, user_prompt: str) -> str:
    """
    Email inbox triage prompt (ASCII-only). Matches pd.inbox_lead_actions context.
    Produces a compact checklist per email with sender details and conditional drafts.
    """
    max_items = int(os.getenv("PD_MAX_OUTPUT_ITEMS", "8"))
    return (
        "[BEGIN CONTEXT]\n"
        f"{context}\n"
        "[END CONTEXT]\n\n"
        "[INSTRUCTIONS]\n"
        "You perform EMAIL inbox triage ONLY based on the CONTEXT above.\n"
        f"Output AT MOST {max_items} items that clearly need action. Choose the most urgent ones.\n"
        "For each email item (e.g., lines starting with [EMAIL i] or - From:), produce exactly these fields:\n"
        "- Index: the numeric index you infer from CONTEXT (e.g., 1, 2, 3...)\n"
        "- From: copy the sender name and email exactly as shown in CONTEXT\n"
        "- Subject: copy the subject exactly as shown in CONTEXT (or (no subject))\n"
        "- Status: copy the status field from CONTEXT if present (e.g., unanswered_incoming / awaiting_their_reply / unknown)\n"
        "- QuickAction: yes|no - one short reason\n"
        "- LeadLikelihood: high|medium|low - one short reason\n"
        "- NextAction: reply now | schedule follow-up | ask for info | none\n"
        "- ReplyDraft: 3-6 short sentences in the same language as the email IF NextAction is 'reply now'; otherwise write: None\n\n"
        "[OUTPUT FORMAT]\n"
        "- [Index]\n"
        "  From: Name <email@domain>\n"
        "  Subject: subject text\n"
        "  Status: unanswered_incoming\n"
        "  QuickAction: yes - sender asked for next steps\n"
        "  LeadLikelihood: medium - shows buying intent\n"
        "  NextAction: reply now\n"
        "  ReplyDraft: Thank you for your message. I can propose a short call tomorrow to walk through...\n\n"
        "[USER REQUEST]\n"
        f"{user_prompt}\n\n"
        "[RULES]\n"
        "- Be concise and practical.\n"
        "- Use the exact bullet structure shown under OUTPUT FORMAT for each item.\n"
        "- Do not include more than the specified fields per item.\n"
        "- Do not add any extra sections.\n"
    )


def _build_pd_maillead_prompt(context: str, user_prompt: str) -> str:
    """
    Strict, ASCII-only prompt for deciding if newest PD mailbox thread is a lead.
    """
    return (
        "[BEGIN CONTEXT]\n"
        f"{context}\n"
        "[END CONTEXT]\n\n"
        "[INSTRUCTIONS]\n"
        "Decide if the sender is a potential sales lead based ONLY on the CONTEXT above.\n"
        "Output EXACTLY these lines:\n"
        "Lead: yes|no - one short reason (<=12 words)\n"
        "Category: inbound|partner|support|spam|other - best guess\n"
        "NextAction: reply now | ask for info | schedule follow-up | none\n"
        "ReplyDraft: 3-6 short sentences in the same language IF Lead is yes and NextAction is reply now; otherwise write: None\n\n"
        "[USER REQUEST]\n"
        f"{user_prompt}\n"
    )



def dispatch_integration(kind: str, output_text: str, spec: IntegrationSpec) -> Dict[str, Any]:
    """
    Map 'kind' to a lazily-imported function and execute it (post-LLM).
    For 'ms.mail_triage' and 'g.gmail_summarize' there is no post action; analysis is already in output_text.
    """
    bindings = {
        # Microsoft 365 (existing)
        "ms.word_upsert": "api.integrations.ms365:word_upsert_docx",
        "ms.mail_draft_reply": "api.integrations.ms365:mail_draft_reply_latest",
        "ms.calendar_create": "api.integrations.ms365:calendar_create",

        # Pipedrive (optional: not used by our custom branches below, but safe to keep)
        "pd.stalled_report": "api.integrations.pipedrive:stalled_deals_report",
        "pd.email_lead_from_gmail": "api.integrations.pipedrive:email_lead_from_gmail",
        "pd.thread_summary_to_pd": "api.integrations.pipedrive:summarize_gmail_thread_and_note",
        # (No direct bindings needed for Google helpers we imported directly below.)
    }

    # No post action needed — LLM output is the final artifact
    if kind in ("ms.mail_triage", "g.gmail_summarize", "pd.inbox_lead_actions",
                "zoho.resume_summarize_prefetch", "zoho.resume_eval_prefetch",
                "zoho.shortlist_prefetch", "zoho.shortlist_prefetch_from_zoho",
                "zoho.resume_summarize_from_zoho"):
        return {"artifact_uri": None, "integration_status": "ok"}

    # Google: create a Gmail draft reply using LLM output
    if kind == "g.gmail_draft_reply":
        try:
            extra = spec.extra or {}
            add_quote = bool(extra.get("add_quote", False))
            quote_chars = int(extra.get("quote_chars", 600))
            query = extra.get("query")  # optional Gmail search query override

            # If caller forces IDs, keep that path (back-compat); else auto-mode with meta
            force_thread_id = (extra.get("thread_id") or "").strip() or None
            force_hdr_msgid = (extra.get("in_reply_to_msgid") or "").strip() or None
            force_refs = extra.get("refs")
            force_subject = (extra.get("subject") or "").strip() or None
            force_to = (extra.get("to") or "").strip() or None

            if not (force_thread_id or force_hdr_msgid):
                # Auto mode with metadata (new)
                fn = _lazy_import("api.integrations.gmail_direct:reply_to_newest_with_meta")
                draft_id, meta = fn(
                    body_text=output_text,
                    query=query,
                    add_quote=add_quote,
                    quote_chars=quote_chars,
                )
                # Surface WHAT we replied to
                subj = (meta.get("subject") or "").strip()
                frm = (meta.get("from") or "").strip()
                # return status + keep original output_text (optionally add a footer you can see)
                return {
                    "artifact_uri": f"gmail-draft://{draft_id}",
                    "integration_status": f"ok — replying to '{subj}' from {frm}",
                    "output_override": output_text  # keep body as-is (quoted part already injected if add_quote=True)
                }

            # Back-compat: explicit IDs path using direct creator
            creator = _lazy_import("api.integrations.gmail_direct:create_reply_draft")
            # If thread missing but msg-id present, fetch newest meta to get thread fallback
            if not force_thread_id:
                info = _lazy_import("api.integrations.gmail_direct:get_newest_message_info")()
                force_thread_id = info.get("thread_id")
                if not force_to:
                    force_to = info.get("from")

            draft_id = creator(
                body_text=output_text,
                thread_id=force_thread_id,
                hdr_msgid=force_hdr_msgid,
                refs=force_refs,
                subject=force_subject,
                to=force_to,
            )
            return {"artifact_uri": f"gmail-draft://{draft_id}", "integration_status": "ok"}

        except HttpError as he:
            raise HTTPException(status_code=502, detail=f"Gmail API error: {he}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Gmail draft failed: {e}")



    # Google: create a Google Doc with LLM output
    if kind == "g.docs_create":
        try:
            title = (spec.extra.get("title") if spec and spec.extra else None) or "LLM Summary"
            doc_id = gdocs_create_from_text(title, output_text)
            return {"artifact_uri": f"docs://{doc_id}", "integration_status": "ok"}
        except HttpError as he:
            raise HTTPException(status_code=502, detail=f"Google Docs error: {he}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Google Docs failed: {e}")

    # --- Pipedrive: 3 flows ---

    # 1) Stalled deals report: override output with report text (no post action)
    if kind == "pd.stalled_report":
        try:
            report = stalled_deals_report(**(spec.extra or {}))
            return {"artifact_uri": None, "integration_status": "ok", "output_override": report}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Pipedrive stalled report failed: {e}")

    # 2) Decide if newest Gmail is a lead: we already gave the LLM the newest mail context
    #    Here we just pass through the LLM decision (no PD write in this minimal version)
    if kind == "pd.mail_lead":
        return {"artifact_uri": None, "integration_status": "ok"}

    # 3) Summarize newest Gmail thread and save as PD Note (post phase writes the note)
    if kind == "pd.thread_summary_to_pd":
        try:
            uri = summarize_pdmail_thread_and_note(llm_summary=output_text)
            return {"artifact_uri": uri, "integration_status": "ok"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Pipedrive note create failed: {e}")


    # --- Zoho Recruit (lazy import; resume flows use prefetch) ---

    if kind == "zoho.create_candidate_from_email":
        try:
            extra = spec.extra or {}
            fn = _lazy_import("api.integrations.zoho_recruit:create_candidate")
            link = fn(
                name=extra.get("name", "Unknown"),
                email=extra.get("email", ""),
                phone=extra.get("phone", None),
            )
            return {"artifact_uri": link, "integration_status": "ok"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Zoho create candidate failed: {e}")


    if kind in ("zoho.resume_summarize_prefetch", "zoho.resume_eval_prefetch"):
        # LLM output (summary/eval) is the artifact; nothing to write to Zoho here
        return {"artifact_uri": None, "integration_status": "ok"}

    if kind == "zoho.create_candidate_from_pdmail":
        try:
            fn = _lazy_import("api.integrations.zoho_recruit:create_candidate_from_pdmail")
            link = fn()
            return {"artifact_uri": link, "integration_status": "ok"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Zoho create candidate from PD mail failed: {e}")


    # Existing Microsoft bindings
    if kind not in bindings:
        raise HTTPException(status_code=400, detail=f"Unsupported integration kind: {kind}")

    fn = _lazy_import(bindings[kind])

    try:
        if kind == "ms.word_upsert":
            file_name = spec.file_name or "router-output.docx"
            folder = (spec.folder or "").strip() or None
            uri = fn(text=output_text, file_name=file_name, folder=folder)
            return {"artifact_uri": uri, "integration_status": "ok"}

        if kind == "ms.mail_draft_reply":
            link = fn(output_text)
            return {"artifact_uri": link, "integration_status": "ok"}

        if kind == "ms.calendar_create":
            subject = (spec.extra.get("subject") if spec.extra else None) or "Quick sync"
            body = (spec.extra.get("body") if spec.extra else None) or ""
            start = (spec.extra.get("start") if spec.extra else None)
            end = (spec.extra.get("end") if spec.extra else None)
            tz = (spec.extra.get("tz") if spec.extra else None) or "Europe/Warsaw"
            link = fn(subject=subject, body=body, start=start, end=end, tz=tz)
            return {"artifact_uri": link, "integration_status": "ok"}

    except HTTPException:
        raise
    except Exception as e:
        # Catch everything so a bad integration never kills the API
        raise HTTPException(status_code=500, detail=f"Integration failed: {e}")

    raise HTTPException(status_code=500, detail="Integration dispatch fell through unexpectedly")



@app.post("/route", response_model=RouteResponse)
def route(req: RouteRequest) -> RouteResponse:
    t0 = time.time()

    # Model policy (triage uses a slightly stronger but still cheap local model)
    kind = req.integration.kind if req.integration else None
    # Build the full prompt that will hit the model (same as now)
    # (We compute selection AFTER prefetch/rewriting below to get accurate token counts.)
    # We still compute selection early to record a plan (we’ll re-evaluate cost right before call).
    selected = {"provider": "ollama", "model": choose_model(req.quality_floor, kind)}

    # Optional prefetch: enrich prompt for certain kinds before LLM call
    prompt = req.prompt
    pre = None
    if req.integration is not None:
        pre = _prefetch_context(req.integration.kind, req.integration)  # ← MISSING CALL (restore)
        if pre and req.integration.kind == "ms.mail_triage":
            prompt = _build_triage_prompt(pre, req.prompt)
        elif pre and req.integration.kind == "pd.inbox_lead_actions":
            prompt = _build_pd_mailtriage_prompt(pre, req.prompt)
        elif pre and req.integration.kind == "pd.mail_lead":
            prompt = _build_pd_maillead_prompt(pre, req.prompt)
        elif pre and req.integration.kind == "g.gmail_summarize":   # ← add this block
            prompt = _build_gmail_summary_prompt(pre, req.prompt)   # ← use the builder
        elif pre:
            prompt = f"{pre}\n\n=== USER REQUEST ===\n{req.prompt}"

    # ---- Cost-aware model planning (from price_table.yaml) ----
    planned = _pick_model_by_policy(prompt, req.expected_output_tokens, req.quality_floor)
    provider = planned["provider"]
    model = planned["model"]

    # Safety: until hosted providers are wired, coerce to ollama so nothing breaks
    if provider != "ollama":
        provider = "ollama"
        model = choose_model(req.quality_floor, kind)

    # Call LLM with robust timeout/retry/fallback
    output = call_ollama(model=model, prompt=prompt, timeout=make_timeout())

    artifact_uri = None
    integration_status = None
    if req.integration is not None:
        result = dispatch_integration(req.integration.kind, output, req.integration)
        artifact_uri = result.get("artifact_uri")
        integration_status = result.get("integration_status", "ok")
        if result.get("output_override"):
            output = result["output_override"]

    latency_ms = int((time.time() - t0) * 1000)

    return RouteResponse(
        job_id=os.urandom(8).hex(),
        provider=provider,
        model=model,
        output_text=output,
        estimated_cost_usd=0.0,
        latency_ms=latency_ms,
        cached=False,
        artifact_uri=artifact_uri,
        integration_status=integration_status,
    )
