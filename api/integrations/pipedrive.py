# api/integrations/pipedrive.py
# Minimal, robust helpers for Pipedrive + 3 use-cases:
# 1) Stalled deals / missing next step report
# 2) Decide if newest Gmail email is a lead -> returns context string for LLM
# 3) Summarize newest Gmail thread, then (optionally) create a Pipedrive note

from __future__ import annotations

import datetime as dt
import os
from typing import Any, Dict, List, Optional

import requests
import time
import httpx

PD_BASE = (os.getenv("PIPEDRIVE_BASE_URL") or "").rstrip("/")
PD_TOKEN = os.getenv("PIPEDRIVE_API_TOKEN", "")

def _pd_debug(tag: str, data) -> None:
    if os.getenv("DEBUG_PD", "0") != "1":
        return
    try:
        import json
        def skim(x, depth=0):
            if depth > 2:
                return type(x).__name__
            if isinstance(x, dict):
                return {k: skim(v, depth+1) for k,v in list(x.items())[:10]}
            if isinstance(x, list):
                return [skim(v, depth+1) for v in x[:5]]
            return x if isinstance(x, (str,int,float,bool)) else type(x).__name__
        print(f"[PD-DEBUG] {tag}: {json.dumps(skim(data))[:1000]}")
    except Exception:
        pass

def _pd_client(timeout=30) -> httpx.Client:
    if not PD_BASE or not PD_TOKEN:
        raise RuntimeError("Pipedrive not configured: set PIPEDRIVE_BASE_URL and PIPEDRIVE_API_TOKEN in .env")
    # Pipedrive v1 API uses api token as query param
    return httpx.Client(
        base_url=f"{PD_BASE}/api/v1",
        timeout=timeout,
        params={"api_token": PD_TOKEN},
        headers={"Accept": "application/json"},
    )

def _pd_get(path: str, params=None, retries=2, backoff=1.5) -> dict:
    with _pd_client() as c:
        attempt = 0
        while True:
            try:
                r = c.get(path, params=params)
                if r.status_code == 429:
                    raise httpx.HTTPStatusError("rate limited", request=r.request, response=r)
                r.raise_for_status()
                js = r.json()
                _pd_debug(f"GET {path}", {"status": r.status_code, "data_keys": list(js.keys())})
                return js
            except httpx.HTTPStatusError as e:
                if e.response is not None and e.response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(backoff * (attempt + 1))
                    attempt += 1
                    continue
                txt = e.response.text[:400] if e.response is not None else str(e)
                raise RuntimeError(f"Pipedrive GET failed {path}: {txt}")
            except Exception as e:
                if attempt < retries:
                    time.sleep(backoff * (attempt + 1))
                    attempt += 1
                    continue
                raise


# Reuse your Gmail helpers (already working in this project)
try:
    from api.integrations.google_ws import gmail_fetch_newest_thread
except Exception:
    gmail_fetch_newest_thread = None  # allow unit tests without google configured

PD_BASE = os.getenv("PIPEDRIVE_BASE_URL", "https://api.pipedrive.com/v1")
PD_TOKEN = os.getenv("PIPEDRIVE_API_TOKEN", "")


class PipedriveError(RuntimeError):
    pass


def _pd_request(
    path: str,
    method: str = "GET",
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    if not PD_TOKEN:
        raise PipedriveError("Missing PIPEDRIVE_API_TOKEN in environment")
    params = dict(params or {})
    params["api_token"] = PD_TOKEN
    url = f"{PD_BASE}{path}"
    r = requests.request(method, url, params=params, json=json, timeout=timeout)
    if r.status_code >= 400:
        raise PipedriveError(
            f"Pipedrive {method} {path} failed: {r.status_code} {r.text[:300]}"
        )
    data = r.json() if r.text else {}
    return data.get("data") if isinstance(data, dict) and "data" in data else data


def _pd_list(
    path: str, params: Optional[Dict[str, Any]] = None, limit_pages: int = 5
) -> List[Dict[str, Any]]:
    """Page through list endpoints (classic start/limit pagination)."""
    out: List[Dict[str, Any]] = []
    start = 0
    for _ in range(limit_pages):
        page = _pd_request(path, params={**(params or {}), "start": start, "limit": 100})
        if not page:
            break
        items = (
            page
            if isinstance(page, list)
            else page.get("items")
            or page.get("deals")
            or page.get("data")
            or []
        )
        if isinstance(items, dict):
            # Some endpoints return {"items": [{"item": {...}}]}
            items = items.get("items", [])
        if not items:
            break
        for it in items:
            out.append(it["item"] if isinstance(it, dict) and "item" in it else it)
        more = (
            page.get("additional_data", {})
            .get("pagination", {})
            .get("more_items_in_collection", False)
            if isinstance(page, dict)
            else False
        )
        if not more:
            break
        start = (
            page.get("additional_data", {})
            .get("pagination", {})
            .get("next_start", start + len(items))
        )
    return out


# ---------- Use case (i): stalled deals / missing next step ----------


def stalled_deals_report(
    *, days_stalled: int = 10, only_missing_next_step: bool = True, **_
) -> str:
    """
    Consider a deal stalled if:
    - status == 'open'
    - no next_activity_date OR next_activity_date is in the past
    - update_time older than `days_stalled` days (UTC)
    """
    today = dt.date.today()
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=days_stalled)

    deals = _pd_list("/deals", params={"status": "open", "sort": "update_time DESC"})
    lines: List[str] = []
    for d in deals:
        next_act = d.get("next_activity_date")
        upd_str = d.get("update_time") or d.get("update_time_utc") or ""
        try:
            last_upd = (
                dt.datetime.fromisoformat(upd_str.replace("Z", "+00:00")).replace(tzinfo=None)
                if upd_str
                else None
            )
        except Exception:
            last_upd = None

        missing_next = not next_act
        overdue_next = False
        if next_act:
            try:
                overdue_next = dt.datetime.strptime(next_act, "%Y-%m-%d").date() < today
            except Exception:
                overdue_next = False

        if only_missing_next_step and not missing_next:
            continue
        if last_upd and last_upd > cutoff:
            continue

        title = d.get("title") or "(untitled)"
        person = d.get("person_name") or ""
        org = d.get("org_name") or ""
        value = d.get("value") or 0
        currency = d.get("currency") or ""
        deal_id = d.get("id")

        why = "missing next step" if missing_next else ("next step overdue" if overdue_next else "stalled")
        lines.append(
            f"- Deal #{deal_id}: {title} — {person} / {org} — {value}{currency} — {why}"
        )

    if not lines:
        return "No stalled deals found with the given criteria."

    return "\n".join(
        [
            f"Stalled deals (>={days_stalled} days, only_missing_next_step={only_missing_next_step}):",
            *lines,
        ]
    )


# ---------- Use case (ii): decide if a new email is a lead ----------


def email_lead_from_gmail(**_) -> str:
    """
    Prefetches the newest Gmail thread and returns a short context for the LLM to decide lead/non-lead.
    Robust to gmail_fetch_newest_thread() returning either a string or a dict in different shapes.
    """
    if gmail_fetch_newest_thread is None:
        return "Gmail not configured."
    data = gmail_fetch_newest_thread()
    if not data:
        return "No Gmail threads available."

    # If helper already produced a string context, pass it through (cap length).
    if isinstance(data, str):
        return (data if len(data) <= 2000 else (data[:2000] + "\n[... trimmed ...]"))

    # Otherwise, we expect a dict in one of a few shapes.
    def _hdrs(msg: Dict[str, Any]) -> Dict[str, str]:
        # Works both for Google API payload headers and flat dicts
        headers_list = (msg.get("payload", {}) or {}).get("headers", [])
        if isinstance(headers_list, list) and headers_list:
            return {h.get("name", "").lower(): h.get("value", "") for h in headers_list if isinstance(h, dict)}
        return {k.lower(): v for k, v in msg.items() if isinstance(k, str)}

    # Pick the newest message we can find
    msg = None
    if isinstance(data, dict):
        if "threads" in data:
            th = (data.get("threads") or [{}])[0] or {}
            msgs = th.get("messages") or []
            msg = msgs[-1] if msgs else th  # fall back to thread-level dict
        elif "messages" in data:
            msgs = data.get("messages") or []
            msg = msgs[-1] if msgs else data
        else:
            msg = data
    else:
        # Unexpected type (not str, not dict) — return safe string
        return f"{str(data)[:2000]}\n[... trimmed or unknown shape ...]"

    headers = _hdrs(msg or {})
    subject = headers.get("subject") or msg.get("subject") or "(no subject)"
    from_h = headers.get("from") or msg.get("from") or "(unknown)"
    to_h = headers.get("to") or msg.get("to") or ""
    date_h = headers.get("date") or msg.get("date") or ""
    snippet = msg.get("snippet") or msg.get("text") or ""

    lines = [
        "Newest email:",
        f"From: {from_h}",
        f"To: {to_h}" if to_h else "To: (unknown)",
        f"Subject: {subject}",
        (f"Date: {date_h}" if date_h else "Date: (unknown)"),
        f"Snippet: {snippet}",
    ]
    out = "\n".join(lines)
    return out if len(out) <= 2000 else (out[:2000] + "\n[... trimmed ...]")


# ---------- Use case (iii): summarize full long email thread & write PD note ----------


def summarize_gmail_thread_and_note(
    *, llm_summary: Optional[str] = None, **_
) -> Optional[str]:
    """
    If llm_summary is None: return the full thread text (robust to string/dict Gmail helper outputs).
    If provided: create a Pipedrive note (best-effort association to person).
    """
    if gmail_fetch_newest_thread is None:
        return "Gmail not configured."

    def _headers_from_msg(m: Dict[str, Any]) -> Dict[str, str]:
        headers_list = (m.get("payload", {}) or {}).get("headers", [])
        if isinstance(headers_list, list) and headers_list:
            return {h.get("name", "").lower(): h.get("value", "") for h in headers_list if isinstance(h, dict)}
        return {k.lower(): v for k, v in m.items() if isinstance(k, str)}

    data = gmail_fetch_newest_thread()
    if not data:
        return "No Gmail threads available."

    # Prefetch phase: return text to summarize
    if llm_summary is None:
        if isinstance(data, str):
            text = data
        elif isinstance(data, dict):
            # Build a compact, ordered text from messages we can find
            msgs = []
            if "threads" in data:
                th = (data.get("threads") or [{}])[0] or {}
                msgs = th.get("messages") or []
            elif "messages" in data:
                msgs = data.get("messages") or []

            lines: List[str] = []
            if msgs:
                for m in msgs:
                    hdr = _headers_from_msg(m or {})
                    who = hdr.get("from") or m.get("from") or "(unknown)"
                    when = hdr.get("date") or m.get("date") or ""
                    snippet = m.get("snippet") or m.get("text") or ""
                    lines.append(f"{when} - {who}: {snippet}")
                text = "\n".join(lines) if lines else str(data)
            else:
                # Flat dict or unexpected shape
                who = (data.get("from") if isinstance(data, dict) else "") or "(unknown)"
                when = (data.get("date") if isinstance(data, dict) else "") or ""
                subj = (data.get("subject") if isinstance(data, dict) else "") or "(no subject)"
                snip = (data.get("snippet") if isinstance(data, dict) else "") or ""
                text = f"{when} - {who}: {subj}\n{snip}"
        else:
            text = str(data)

        return text[:8000] if len(text) > 8000 else text

    # Post phase: create PD note and try to link to a person by email (sender of last message)
    # We re-fetch so we can read the last sender robustly; if this fails we still create a note without person_id.
    data2 = data if isinstance(data, dict) else (gmail_fetch_newest_thread() or {})
    last_sender_email = None
    if isinstance(data2, dict):
        msg = None
        if "threads" in data2:
            th = (data2.get("threads") or [{}])[0] or {}
            msgs = th.get("messages") or []
            msg = msgs[-1] if msgs else th
        elif "messages" in data2:
            msgs = data2.get("messages") or []
            msg = msgs[-1] if msgs else data2
        else:
            msg = data2
        hdr = _headers_from_msg(msg or {})
        from_h = hdr.get("from") or msg.get("from") or ""
        # crude parse "Name <email@x>"
        if "<" in from_h and ">" in from_h:
            try:
                last_sender_email = from_h.split("<", 1)[1].split(">", 1)[0].strip()
            except Exception:
                last_sender_email = None
        if not last_sender_email and "@" in from_h:
            last_sender_email = from_h.strip()

    note_body = f"Gmail thread summary (auto):\n\n{llm_summary}"
    note_payload: Dict[str, Any] = {"content": note_body}

    # Try to attach to an existing person by email
    if last_sender_email:
        try:
            res = _pd_request("/persons/search", params={"term": last_sender_email})
            items = res.get("items", []) if isinstance(res, dict) else []
            if items:
                pid = items[0].get("item", {}).get("id")
                if pid:
                    note_payload["person_id"] = pid
        except Exception:
            pass  # non-fatal

    created = _pd_request("/notes", method="POST", json=note_payload)
    nid = created.get("id") if isinstance(created, dict) else None
    return f"pipedrive://notes/{nid}" if nid else "pipedrive://notes"



def inbox_lead_actions_prefetch(
    *,
    lookback_days: int = 14,
    max_threads: int = 25,
    consider_if_no_reply_hours: int = 36,
    **_,
) -> str:
    """
    Try Mailbox threads first (requires Email Sync). If unavailable or empty, fall back to
    a Persons-based heuristic using last_incoming_mail_time / last_outgoing_mail_time / next_activity_date.
    The output is ASCII-only and numbered so the LLM can reference items reliably.
    """
    max_chars = int(os.getenv("PD_CONTEXT_MAX_CHARS", "2500"))
    # ---------- Attempt 1: Mailbox threads ----------
    since = (dt.datetime.utcnow() - dt.timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    folder = os.getenv("PD_MAILBOX_FOLDER", "inbox")
    try:
        threads = _pd_list(
            "/mailbox/mailThreads",
            params={"folder": folder, "since_date": since, "sort": "modified_time DESC"},
        )
    except Exception:
        threads = []

    lines: List[str] = []
    idx = 1
    for t in threads or []:
        if len(lines) >= max_threads:
            break
        # Robust subject/from extraction
        subj = t.get("subject") or ""
        if not subj and isinstance(t.get("last_message"), dict):
            subj = t["last_message"].get("subject") or ""
        if not subj:
            subj = "(no subject)"
        last_time = t.get("modified_time") or t.get("update_time") or ""
        from_part = ""
        # participants array
        for p in (t.get("participants") or []):
            if p.get("role") == "from":
                nm = p.get("name") or ""
                em = p.get("email") or ""
                from_part = f"{nm} <{em}>" if em else nm
                break
        # last_message.from as fallback
        if not from_part and isinstance(t.get("last_message"), dict):
            lf = (t["last_message"].get("from") or {}) if isinstance(t["last_message"], dict) else {}
            if isinstance(lf, dict):
                nm = lf.get("name") or ""
                em = lf.get("email") or ""
                from_part = f"{nm} <{em}>" if em else (nm or em)

        last_dir = t.get("last_message_direction")  # incoming or outgoing
        status = "unknown"
        if last_dir == "incoming":
            status = "unanswered_incoming"
        elif last_dir == "outgoing":
            status = "awaiting_their_reply"

        overdue_hint = ""
        if last_time:
            try:
                last_dt = dt.datetime.fromisoformat(last_time.replace("Z", "+00:00")).replace(tzinfo=None)
                hours_since = (dt.datetime.utcnow() - last_dt).total_seconds() / 3600.0
                if hours_since >= consider_if_no_reply_hours:
                    overdue_hint = f" | overdue_possible (>{consider_if_no_reply_hours}h since last touch)"
            except Exception:
                pass

        item = (
            f"[EMAIL {idx}]\n"
            f"From: {from_part or '(unknown)'}\n"
            f"Subject: {subj}\n"
            f"Last: {last_time}\n"
            f"Status: {status}{overdue_hint}"
        )
        lines.append(item)
        idx += 1

        # Early cap to keep context small
        if sum(len(x) for x in lines) >= max_chars:
            break

    if lines:
        header = "Pipedrive mailbox threads:\n"
        text = header + "\n".join(lines)
        return text[:max_chars]

    # ---------- Attempt 2: Persons-based heuristic ----------
    persons: List[Dict[str, Any]] = []
    try:
        persons = _pd_list("/persons", params={"sort": "update_time DESC"})
    except Exception:
        persons = []

    if not persons:
        msg = (
            "No mailbox threads and no persons found. "
            "If you expect mailbox, ensure Email Sync is enabled for the API user."
        )
        return msg[:max_chars]

    def _parse_dt(s: Optional[str]) -> Optional[dt.datetime]:
        if not s:
            return None
        s = s.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(s[:19], fmt) if fmt != "%Y-%m-%d" else dt.datetime.strptime(s[:10], fmt)
            except Exception:
                continue
        try:
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None

    now = dt.datetime.utcnow()
    cutoff = now - dt.timedelta(days=lookback_days)

    out: List[str] = []
    idx = 1
    for person in persons:
        if len(out) >= max_threads:
            break

        name = person.get("name") or "(unnamed)"
        emails = person.get("email") or []
        primary_email = ""
        if isinstance(emails, list) and emails:
            for e in emails:
                if isinstance(e, dict) and e.get("value"):
                    primary_email = e["value"]
                    if e.get("primary"):
                        break

        last_in = _parse_dt(person.get("last_incoming_mail_time"))
        last_out = _parse_dt(person.get("last_outgoing_mail_time"))
        next_act_date_s = person.get("next_activity_date")
        next_act_date = None
        if next_act_date_s:
            try:
                next_act_date = dt.datetime.strptime(next_act_date_s, "%Y-%m-%d")
            except Exception:
                next_act_date = None

        most_recent = max([t for t in (last_in, last_out) if t is not None], default=None)
        if not most_recent or most_recent < cutoff:
            continue

        status = "unknown"
        overdue_hint = ""
        if last_in and (not last_out or last_in > last_out):
            status = "unanswered_incoming"
            hours_since = (now - last_in).total_seconds() / 3600.0
            if hours_since >= consider_if_no_reply_hours:
                overdue_hint = f" | overdue_possible (>{consider_if_no_reply_hours}h since inbound)"
        elif last_out:
            status = "awaiting_their_reply"
            if next_act_date is None or next_act_date.date() < dt.date.today():
                overdue_hint = " | follow_up_missing_or_overdue"

        item = (
            f"[EMAIL {idx}]\n"
            f"From: {name} <{primary_email}>\n"
            f"Subject: (no subject)\n"
            f"LastIncoming: {last_in or 'None'} | LastOutgoing: {last_out or 'None'} | NextActivityDate: {next_act_date_s or 'None'}\n"
            f"Status: {status}{overdue_hint}"
        )
        out.append(item)
        idx += 1

        if sum(len(x) for x in out) >= max_chars:
            break

    if not out:
        msg = (
            "Mailbox unavailable and no recent persons suggest action within lookback window. "
            "Consider increasing lookback_days or verifying Email Sync."
        )
        return msg[:max_chars]

    header = "Pipedrive persons (heuristic):\n"
    text = header + "\n".join(out)
    return text[:max_chars]


def _pd_thread_messages(thread_id: Any, limit_pages: int = 2) -> List[Dict[str, Any]]:
    """
    Best-effort fetch of messages in a mailbox thread. Some tenants gate this endpoint.
    Returns a list of messages (possibly empty) without raising.
    """
    try:
        msgs = _pd_list("/mailbox/mailMessages", params={"thread_id": thread_id, "sort": "timestamp ASC"}, limit_pages=limit_pages)
        if isinstance(msgs, list):
            return msgs
        if isinstance(msgs, dict):
            items = msgs.get("items") or msgs.get("data") or []
            if isinstance(items, list):
                return items
        return []
    except Exception:
        return []


def pd_mail_lead_prefetch(
    *,
    lookback_days: int = 7,
    max_preview_chars: int = 600,
    **_,
) -> str:
    """
    Use Pipedrive Mailbox to pull the newest inbox thread and return a compact lead-decision context.
    No Gmail usage. ASCII-only, small, safe for tiny local models.
    """
    folder = os.getenv("PD_MAILBOX_FOLDER", "inbox")
    since = (dt.datetime.utcnow() - dt.timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    try:
        threads = _pd_list(
            "/mailbox/mailThreads",
            params={"folder": folder, "since_date": since, "sort": "modified_time DESC"},
            limit_pages=1,
        )
    except Exception:
        threads = []

    if not threads:
        return "No PD mailbox threads found."

    th = threads[0]
    subj = th.get("subject") or "(no subject)"
    last = th.get("modified_time") or th.get("update_time") or ""
    from_part = ""
    for p in (th.get("participants") or []):
        if p.get("role") == "from":
            nm = p.get("name") or ""
            em = p.get("email") or ""
            from_part = f"{nm} <{em}>" if em else (nm or em)
            break
    if not from_part and isinstance(th.get("last_message"), dict):
        lf = th["last_message"].get("from") or {}
        if isinstance(lf, dict):
            nm = lf.get("name") or ""
            em = lf.get("email") or ""
            from_part = f"{nm} <{em}>" if em else (nm or em)

    # Try to get the last message preview
    preview = ""
    msgs = _pd_thread_messages(th.get("id"))
    if msgs:
        last_msg = msgs[-1]
        # Common fields: subject/body/plaintext or snippet; we keep it tiny
        preview = (
            last_msg.get("snippet")
            or last_msg.get("plaintext")
            or (last_msg.get("body") or "").replace("\r", "").replace("\n", " ")
        )
        preview = (preview or "")[:max_preview_chars]

    out = [
        "Newest PD mailbox thread:",
        f"From: {from_part or '(unknown)'}",
        f"Subject: {subj}",
        f"Last: {last}",
    ]
    if preview:
        out.append(f"Preview: {preview}")
    return "\n".join(out)


def pd_thread_context_prefetch(
    *,
    lookback_days: int = 14,
    max_messages: int = 15,
    max_chars: int = 3500,
    **_,
) -> str:
    """
    Build a compact plain-text transcript from the newest PD mailbox thread.
    Returns a newline-joined text safe to feed to an LLM for summarization.
    """
    folder = os.getenv("PD_MAILBOX_FOLDER", "inbox")
    since = (dt.datetime.utcnow() - dt.timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    try:
        threads = _pd_list(
            "/mailbox/mailThreads",
            params={"folder": folder, "since_date": since, "sort": "modified_time DESC"},
            limit_pages=1,
        )
    except Exception:
        threads = []

    if not threads:
        return "No PD mailbox threads found."

    th = threads[0]
    msgs = _pd_thread_messages(th.get("id"), limit_pages=2)
    if not msgs:
        # Fall back to header-only context
        subj = th.get("subject") or "(no subject)"
        last = th.get("modified_time") or th.get("update_time") or ""
        from_part = ""
        for p in (th.get("participants") or []):
            if p.get("role") == "from":
                nm = p.get("name") or ""
                em = p.get("email") or ""
                from_part = f"{nm} <{em}>" if em else nm
                break
        header = f"Thread: {subj} | From: {from_part or '(unknown)'} | Last: {last}"
        return header[:max_chars]

    lines: List[str] = []
    count = 0
    for m in msgs:
        if count >= max_messages:
            break
        frm = ""
        who = m.get("from")
        if isinstance(who, dict):
            nm = who.get("name") or ""
            em = who.get("email") or ""
            frm = f"{nm} <{em}>" if em else (nm or em)
        when = m.get("timestamp") or m.get("date") or ""
        snippet = (
            m.get("plaintext")
            or m.get("snippet")
            or (m.get("body") or "").replace("\r", " ").replace("\n", " ")
        )
        snippet = (snippet or "")[:500]
        lines.append(f"{when} - {frm}: {snippet}")
        count += 1
        if sum(len(x) for x in lines) >= max_chars:
            break

    text = "\n".join(lines)
    return text[:max_chars]


def summarize_pdmail_thread_and_note(
    *, llm_summary: Optional[str] = None, **_
) -> Optional[str]:
    """
    If llm_summary is None: return newest PD mailbox thread text (prefetch).
    If provided: create a PD Note and link to a best-guess Person by sender email.
    """
    if llm_summary is None:
        return pd_thread_context_prefetch()

    # Resolve participants and try to find a Person; if none, create one from last sender
    folder = os.getenv("PD_MAILBOX_FOLDER", "inbox")
    try:
        threads = _pd_list(
            "/mailbox/mailThreads",
            params={"folder": folder, "sort": "modified_time DESC"},
            limit_pages=1,
        )
    except Exception:
        threads = []

    person_id = None
    last_sender_name = None
    last_sender_email = None
    emails_to_try: List[str] = []

    if threads:
        th = threads[0]
        # Collect emails from participants
        for p in (th.get("participants") or []):
            em = p.get("email")
            if em:
                emails_to_try.append(em)
        # Also check last message sender
        msgs = _pd_thread_messages(th.get("id"), limit_pages=1)
        if msgs:
            who = msgs[-1].get("from")
            if isinstance(who, dict):
                last_sender_name = who.get("name") or None
                last_sender_email = who.get("email") or None
                if last_sender_email:
                    emails_to_try.append(last_sender_email)

    # Deduplicate
    emails_to_try = list({e for e in emails_to_try if e})

    # Try to find an existing person
    for em in emails_to_try:
        try:
            res = _pd_request("/persons/search", params={"term": em})
            items = res.get("items", []) if isinstance(res, dict) else []
            if items:
                person_id = items[0].get("item", {}).get("id")
                break
        except Exception:
            continue

    # If still none, create a minimal person using last sender
    if person_id is None and last_sender_email:
        try:
            payload_person = {"name": last_sender_name or last_sender_email, "email": last_sender_email}
            created_p = _pd_request("/persons", method="POST", json=payload_person)
            person_id = created_p.get("id") if isinstance(created_p, dict) else None
        except Exception:
            person_id = None

    # Create the note (must attach to something)
    note_body = f"PD mailbox thread summary (auto):\n\n{llm_summary}"
    payload_note: Dict[str, Any] = {"content": note_body}
    if person_id:
        payload_note["person_id"] = person_id

    # If we still have nothing to attach, fail gracefully with message
    if not person_id:
        # Return the summary as a string; skip note creation to avoid 400
        return "No related Person found/created; summary not saved:\n\n" + llm_summary

    created = _pd_request("/notes", method="POST", json=payload_note)
    nid = created.get("id") if isinstance(created, dict) else None
    return f"pipedrive://notes/{nid}" if nid else "pipedrive://notes"

