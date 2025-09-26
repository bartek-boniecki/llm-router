#!/usr/bin/env python3
"""
tools/test_inboxes.py
Sanity-check that we can extract a sender email from:
  (a) Gmail (via your existing gmail_fetch_newest_thread wrapper)
  (b) Pipedrive mailbox (via REST)
Run inside the api container:
  docker compose exec api python tools/test_inboxes.py --gmail --pipedrive
Env needed:
  Gmail: whatever your google_ws module already uses (unchanged)
  Pipedrive: PD_API_TOKEN (or PIPEDRIVE_API_TOKEN), optional PD_API_BASE
"""
from __future__ import annotations
import os
import sys
import argparse
import json
from typing import Any, Optional, Tuple, List
import sys, pathlib
# Ensure project root (the directory that contains 'api/') is on sys.path
ROOT = pathlib.Path(__file__).resolve().parents[1]  # project root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import requests

# ---------- Helpers copied from zoho_recruit (no import conflicts) ----------
def _extract_from_header(headers: Any) -> Optional[str]:
    if not headers:
        return None
    if isinstance(headers, list):
        for h in headers:
            try:
                if str(h.get("name", "")).lower() == "from":
                    return h.get("value")
            except Exception:
                continue
        return None
    if isinstance(headers, dict):
        for k, v in headers.items():
            if str(k).lower() == "from":
                return v
    return None

def _norm_from_any(obj: Any) -> Tuple[Optional[str], Optional[str]]:
    if isinstance(obj, dict):
        nm = obj.get("name")
        em = obj.get("email")
        if em:
            return (nm or None, str(em).strip().lower())
        hdr_from = _extract_from_header(obj)
        if hdr_from:
            return _norm_from_any(hdr_from)
        if "from" in obj:
            return _norm_from_any(obj.get("from"))
        payload = obj.get("payload") or {}
        if isinstance(payload, dict):
            headers = payload.get("headers")
            hdr_val = _extract_from_header(headers)
            if hdr_val:
                return _norm_from_any(hdr_val)
        headers = obj.get("headers")
        if headers:
            hdr_val = _extract_from_header(headers)
            if hdr_val:
                return _norm_from_any(hdr_val)
    if isinstance(obj, str):
        s = obj.strip()
        if "<" in s and ">" in s:
            try:
                em = s.split("<", 1)[1].split(">", 1)[0].strip().lower()
                nm = s.split("<", 1)[0].strip().strip('"').strip()
                return (nm or None, em)
            except Exception:
                pass
        if "@" in s and " " not in s:
            return (None, s.lower())
    return (None, None)

def _print(title: str, obj: Any):
    print(f"\n=== {title} ===")
    print(json.dumps(obj, indent=2, ensure_ascii=False)[:4000])

# ---------- Gmail ----------
def test_gmail():
    try:
        from api.integrations.google_ws import gmail_fetch_newest_thread
    except Exception as e:
        print(f"[GMAIL] google_ws import failed: {e}")
        return

    resp = None
    # Try modern signature
    try:
        n_threads = int(os.getenv("GMAIL_THREADS_N", "8"))
        lookback_days = int(os.getenv("GMAIL_LOOKBACK_DAYS", "14"))
        resp = gmail_fetch_newest_thread(n_threads=n_threads, lookback_days=lookback_days)
    except TypeError:
        # Try common alternates
        tried = False
        for kwargs in (
            {"limit": int(os.getenv("GMAIL_THREADS_N", "8")), "days": int(os.getenv("GMAIL_LOOKBACK_DAYS", "14"))},
            {"limit": int(os.getenv("GMAIL_THREADS_N", "8"))},
            {},
        ):
            try:
                resp = gmail_fetch_newest_thread(**kwargs)
                tried = True
                break
            except TypeError:
                continue
        if not tried:
            print("[GMAIL] gmail_fetch_newest_thread signature not supported.")
            return
    except Exception as e:
        print(f"[GMAIL] gmail_fetch_newest_thread threw: {e}")
        return

    # Normalize threads list
    threads = []
    if isinstance(resp, dict):
        threads = resp.get("threads") or resp.get("items") or resp.get("data") or []
    elif isinstance(resp, list):
        threads = resp

    if not threads:
        print("[GMAIL] No threads returned.")
        _print("GMAIL raw", resp)
        return

    for idx, th in enumerate(threads[:5], 1):
        msgs = th.get("messages", []) or th.get("msgs", []) or []
        if not msgs:
            # some wrappers return 'payloads' or a single 'message'
            maybe = th.get("message") or th.get("payloads")
            if isinstance(maybe, list):
                msgs = maybe
            elif isinstance(maybe, dict):
                msgs = [maybe]
        for m in reversed(msgs):
            nm, em = _norm_from_any(m)
            if not em and isinstance(m, dict):
                nm, em = _norm_from_any(m.get("from"))
            if em:
                print(f"[GMAIL] ✅ Found sender: name='{nm}' email='{em}'")
                return
    print("[GMAIL] ❌ No sender email parsed from recent threads.")
    _print("GMAIL sample thread", threads[0])


# ---------- Pipedrive ----------
def _pd_request(path: str, params=None, method="GET"):
    base = os.getenv("PD_API_BASE", os.getenv("PIPEDRIVE_BASE_URL", "https://api.pipedrive.com/v1"))
    token = os.getenv("PD_API_TOKEN", os.getenv("PIPEDRIVE_API_TOKEN", ""))
    if not token:
        raise RuntimeError("Missing PD_API_TOKEN or PIPEDRIVE_API_TOKEN")
    params = dict(params or {})
    params["api_token"] = token
    url = f"{base}{path}"
    r = requests.request(method, url, params=params, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Pipedrive {method} {path} failed: {r.status_code} {r.text[:500]}")
    try:
        data = r.json()
    except Exception:
        return {}
    return data.get("data") if isinstance(data, dict) and "data" in data else data

def _pd_list(path: str, params=None) -> List[dict]:
    out: List[dict] = []
    start = 0
    while True:
        page = _pd_request(path, params={**(params or {}), "start": start, "limit": 50})
        if not page:
            break
        items = page if isinstance(page, list) else page.get("items") or page.get("data") or []
        if isinstance(items, dict):
            items = items.get("items", [])
        if not items:
            break
        for it in items:
            out.append(it["item"] if isinstance(it, dict) and "item" in it else it)
        more = page.get("additional_data", {}).get("pagination", {}).get("more_items_in_collection", False) if isinstance(page, dict) else False
        if not more:
            break
        start = page.get("additional_data", {}).get("pagination", {}).get("next_start", start + len(items))
    return out

def test_pipedrive():
    try:
        threads = _pd_list("/mailbox/mailThreads", params={"folder": os.getenv("PD_MAILBOX_FOLDER", "inbox")})
    except Exception as e:
        print(f"[PD] threads fetch failed: {e}")
        return

    if not threads:
        print("[PD] No mailbox threads found.")
        return

    # Try thread.parties.from (present in your tenant)
    for th in threads[:10]:
        parties = th.get("parties") or {}
        frm = parties.get("from") or []
        # prefer the first non-empty from party
        for party in frm:
            em = (party.get("email_address") or "").strip().lower()
            nm = (party.get("name") or "").strip() or None
            if em:
                print(f"[PD] ✅ Found sender from thread.parties.from: name='{nm}' email='{em}' (thread id={th.get('id')})")
                return

    # Fallback: try last_message (if present)
    for th in threads[:10]:
        last = th.get("last_message") or {}
        who = last.get("from")
        nm, em = _norm_from_any(who)
        if em:
            print(f"[PD] ✅ Found sender from thread.last_message: name='{nm}' email='{em}' (thread id={th.get('id')})")
            return

    # Query messages for each thread (widely available)
    for th in threads[:5]:
        try:
            msgs = _pd_list("/mailbox/mailMessages", params={"thread_id": th.get("id")})
        except Exception as e:
            print(f"[PD] messages fetch failed for thread {th.get('id')}: {e}")
            continue
        for m in reversed(msgs or []):
            who = m.get("from")
            nm, em = _norm_from_any(who)
            if em:
                print(f"[PD] ✅ Found sender from messages: name='{nm}' email='{em}' (thread id={th.get('id')})")
                return
    print("[PD] ❌ No sender email parsed from Pipedrive.")
    print("[PD] Sample thread:")
    _print("PD thread", threads[0])

# ---------- main ----------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gmail", action="store_true", help="Test Gmail sender extraction")
    ap.add_argument("--pipedrive", action="store_true", help="Test Pipedrive sender extraction")
    args = ap.parse_args()

    if not args.gmail and not args.pipedrive:
        print("Usage: python tools/test_inboxes.py --gmail --pipedrive")
        sys.exit(1)

    if args.gmail:
        test_gmail()
    if args.pipedrive:
        test_pipedrive()
