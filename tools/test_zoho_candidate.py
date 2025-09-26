#!/usr/bin/env python3
"""
tools/test_zoho_candidate.py
Sanity-check that we can create a Zoho Candidate via your existing helper and via a raw POST.
Run inside api container:
  docker compose exec api python tools/test_zoho_candidate.py --helper
  docker compose exec api python tools/test_zoho_candidate.py --raw
Env needed (one of):
  ZOHO_ACCESS_TOKEN
  or ZOHO_REFRESH_TOKEN + ZOHO_CLIENT_ID + ZOHO_CLIENT_SECRET
Optional:
  ZOHO_REGION (eu|com|in|au|jp|sa|ca), ZOHO_ORG_ID, ZOHO_RECRUIT_BASE_URL
"""
from __future__ import annotations
import os
import time
import json
from typing import Any, Dict, Optional
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import requests

def _base() -> str:
    override = os.getenv("ZOHO_RECRUIT_BASE_URL")
    if override:
        return override.rstrip("/")
    region = (os.getenv("ZOHO_REGION", "eu") or "eu").strip().lower()
    host = {
        "eu": "recruit.zoho.eu",
        "com": "recruit.zoho.com",
        "in": "recruit.zoho.in",
        "au": "recruit.zoho.com.au",
        "jp": "recruit.zoho.jp",
        "sa": "recruit.zoho.sa",
        "ca": "recruit.zoho.ca",
    }.get(region, "recruit.zoho.eu")
    return f"https://{host}/recruit/v2"

def _access_token() -> str:
    tok = os.getenv("ZOHO_ACCESS_TOKEN")
    if tok:
        return tok
    # try refresh flow
    rt = os.getenv("ZOHO_REFRESH_TOKEN")
    cid = os.getenv("ZOHO_CLIENT_ID")
    cs = os.getenv("ZOHO_CLIENT_SECRET")
    if not (rt and cid and cs):
        raise RuntimeError("Missing ZOHO_ACCESS_TOKEN or (ZOHO_REFRESH_TOKEN + ZOHO_CLIENT_ID + ZOHO_CLIENT_SECRET)")
    accounts_host = os.getenv("ZOHO_ACCOUNTS_HOST", {
        "eu": "accounts.zoho.eu",
        "com": "accounts.zoho.com",
        "in": "accounts.zoho.in",
        "au": "accounts.zoho.com.au",
        "jp": "accounts.zoho.jp",
        "sa": "accounts.zoho.sa",
        "ca": "accounts.zoho.ca",
    }.get(os.getenv("ZOHO_REGION","eu").lower(), "accounts.zoho.eu"))
    url = f"https://{accounts_host}/oauth/v2/token"
    r = requests.post(url, data={
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": cid,
        "client_secret": cs,
    }, timeout=30)
    r.raise_for_status()
    js = r.json()
    return js["access_token"]

def _headers_json() -> Dict[str,str]:
    h = {
        "Authorization": f"Zoho-oauthtoken {_access_token()}",
        "Content-Type": "application/json",
    }
    org = os.getenv("ZOHO_ORG_ID")
    if org:
        h["X-RECRUIT-ORG"] = org
    return h

def do_raw():
    base = _base()
    email = f"test+{int(time.time())}@example.com"
    payload = { "data":[{ "First_Name":"Test", "Last_Name":"Probe", "Email": email }] }
    print("[RAW] POST /Candidates with payload:", json.dumps(payload))

    def _headers():
        h = {
            "Authorization": f"Zoho-oauthtoken {_access_token()}",
            "Content-Type": "application/json",
        }
        org = os.getenv("ZOHO_ORG_ID")
        if org:
            h["X-RECRUIT-ORG"] = org
        return h

    # 1st attempt
    r = requests.post(f"{base}/Candidates", headers=_headers(), json=payload, timeout=30)
    print("[RAW] Status:", r.status_code)
    print("[RAW] Body:", r.text[:800])

    if r.status_code == 401 and os.getenv("ZOHO_REFRESH_TOKEN"):
        print("[RAW] 401 -> attempting refresh-token flow and retry...")
        # force refresh by clearing any ZOHO_ACCESS_TOKEN env
        os.environ.pop("ZOHO_ACCESS_TOKEN", None)
        r = requests.post(f"{base}/Candidates", headers=_headers(), json=payload, timeout=30)
        print("[RAW] Retry Status:", r.status_code)
        print("[RAW] Retry Body:", r.text[:800])

    r.raise_for_status()
    js = r.json()
    cid = js.get("data",[{}])[0].get("details",{}).get("id")
    print("[RAW] ✅ Candidate created id:", cid)



def do_helper():
    try:
        from api.integrations.zoho_recruit import create_candidate
    except Exception as e:
        print(f"[HELPER] import failed: {e}. Did you add project root to sys.path?")
        return
    email = f"test+{int(time.time())}@example.com"
    try:
        link = create_candidate(name="Helper Probe", email=email)
        print("[HELPER] ✅ Candidate link:", link)
    except Exception as e:
        print(f"[HELPER] create_candidate error: {e}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--helper", action="store_true")
    ap.add_argument("--raw", action="store_true")
    args = ap.parse_args()
    if not args.helper and not args.raw:
        print("Usage: python tools/test_zoho_candidate.py --helper | --raw")
        raise SystemExit(1)
    if args.helper:
        do_helper()
    if args.raw:
        do_raw()
