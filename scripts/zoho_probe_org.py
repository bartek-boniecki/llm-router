#!/usr/bin/env python3
"""
Probe Zoho Recruit org endpoint with current access token.

env required:
  ZOHO_RECRUIT_API_BASE  e.g. https://recruit.zoho.eu/recruit/v2
  ZOHO_RECRUIT_ACCESS_TOKEN  e.g. 1000.xxxxxx
"""
import os, urllib.request, urllib.error

base = os.getenv("ZOHO_RECRUIT_API_BASE", "").rstrip("/")
tok = os.getenv("ZOHO_RECRUIT_ACCESS_TOKEN", "")

print("BASE =", base or "(empty)")
print("TOKEN_PREFIX =", (tok[:20] + "…" if tok else "(empty)"))

if not base or not tok:
    raise SystemExit("❌ Missing base or token env var inside container.")

req = urllib.request.Request(f"{base}/org", headers={"Authorization": f"Zoho-oauthtoken {tok}"})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        print("HTTP", r.status)
        print((r.read()[:400]).decode("utf-8", "replace"))
except urllib.error.HTTPError as e:
    print("HTTP error:", e.code)
    print(e.read().decode("utf-8", "replace"))
except Exception as e:
    print("Error:", repr(e))
