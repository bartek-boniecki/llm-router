#!/usr/bin/env python3
"""
Probe Zoho Recruit with current access token using endpoints that match common scopes.

It first tries /Candidates (READ). If that passes, your token works for candidate use cases.
If it fails with OAUTH_SCOPE_MISMATCH or 401, it prints the server's response verbatim.

env required:
  ZOHO_RECRUIT_API_BASE       e.g. https://recruit.zoho.eu/recruit/v2
  ZOHO_RECRUIT_ACCESS_TOKEN   e.g. 1000.xxxxxx
"""
import os, json, urllib.request, urllib.error

BASE = os.getenv("ZOHO_RECRUIT_API_BASE", "").rstrip("/")
TOK  = os.getenv("ZOHO_RECRUIT_ACCESS_TOKEN", "")

print("BASE =", BASE or "(empty)")
print("TOKEN_PREFIX =", (TOK[:20] + "…" if TOK else "(empty)"))
if not BASE or not TOK:
    raise SystemExit("❌ Missing base or token env var inside container.")

def get(url: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Zoho-oauthtoken {TOK}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode("utf-8", "replace")
        print("HTTP", r.status, url)
        print(body[:600])

def try_get(url: str):
    try:
        get(url)
        return True
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        print("HTTP error:", e.code, url)
        print(text)
        return False
    except Exception as e:
        print("Error:", repr(e))
        return False

# 1) Try a lightweight candidates read (should be allowed by ZohoRecruit.modules.ALL)
if try_get(f"{BASE}/Candidates?per_page=1"):
    pass
else:
    print("\nℹ️ If the error says OAUTH_SCOPE_MISMATCH, you need ZohoRecruit.candidates.READ in your token.\n")

# 2) Optional: if you want org info too, uncomment this line and ensure your token has ZohoRecruit.org.READ
# try_get(f"{BASE}/org")
