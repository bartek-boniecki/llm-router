#!/usr/bin/env python3
"""
Zoho OAuth helper (no external deps).

Usage A: refresh access token
  env: ZOHO_ACCOUNTS_HOST=accounts.zoho.eu
       ZOHO_CLIENT_ID=1000.xxxxxx
       ZOHO_CLIENT_SECRET=xxxxxxxx
       ZOHO_REFRESH_TOKEN=1000.xxxxxx
  cmd: python scripts/zoho_oauth_refresh.py refresh

Usage B: exchange a one-time grant code
  env: ZOHO_ACCOUNTS_HOST=accounts.zoho.eu
       ZOHO_CLIENT_ID=1000.xxxxxx
       ZOHO_CLIENT_SECRET=xxxxxxxx
       ZOHO_GRANT_CODE=1000.xxxxxx
       ZOHO_REDIRECT_URI=https://www.zoho.com
  cmd: python scripts/zoho_oauth_refresh.py grant
"""
import json, os, sys, urllib.parse, urllib.request, urllib.error

def post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded"
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code}: {text}")
    except Exception as e:
        raise SystemExit(f"ERROR: {e!r}")

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("refresh", "grant"):
        print(__doc__)
        sys.exit(1)

    accounts = os.getenv("ZOHO_ACCOUNTS_HOST", "accounts.zoho.eu").strip()
    if not accounts:
        raise SystemExit("Missing ZOHO_ACCOUNTS_HOST (e.g., accounts.zoho.eu)")
    token_url = f"https://{accounts}/oauth/v2/token"

    client_id = os.getenv("ZOHO_CLIENT_ID", "").strip()
    client_secret = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
    if not client_id.startswith("1000."):
        raise SystemExit("ZOHO_CLIENT_ID must start with '1000.' (copy it exactly from Zoho).")
    if not client_secret:
        raise SystemExit("Missing ZOHO_CLIENT_SECRET")

    mode = sys.argv[1]
    if mode == "refresh":
        refresh_token = os.getenv("ZOHO_REFRESH_TOKEN", "").strip()
        if not refresh_token.startswith("1000."):
            raise SystemExit("ZOHO_REFRESH_TOKEN missing or invalid (must start with '1000.')")
        res = post_form(token_url, {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        })
    else:  # grant
        grant_code = os.getenv("ZOHO_GRANT_CODE", "").strip()
        redirect_uri = os.getenv("ZOHO_REDIRECT_URI", "https://www.zoho.com").strip()
        if not grant_code.startswith("1000."):
            raise SystemExit("ZOHO_GRANT_CODE missing or invalid (must start with '1000.')")
        res = post_form(token_url, {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": grant_code,
        })

    # Print clean JSON only
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
