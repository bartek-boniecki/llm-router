# scripts/ms_upload_test.py
"""
Uploads a small text file to your OneDrive (PERSONAL account flow).
Uses the MSAL token cache created by scripts/ms_auth.py.

Run:
  docker compose exec api python scripts/ms_upload_test.py
"""

import os
import sys
import pathlib
import httpx
import msal
from datetime import datetime

CACHE_PATH = pathlib.Path("/app/secrets/ms_token_cache.json")
CLIENT_ID = os.getenv("MS_CLIENT_ID")
TENANT_ID = os.getenv("MS_TENANT_ID", "consumers")  # for personal account flow
RESOURCE_SCOPES = ["https://graph.microsoft.com/Files.ReadWrite"]

def fail(msg: str):
    print(f"❌ {msg}")
    sys.exit(1)

def main():
    if not CLIENT_ID:
        fail("MS_CLIENT_ID missing in .env")
    if not CACHE_PATH.exists():
        fail("Token cache not found. Run: docker compose exec api python scripts/ms_auth.py")

    cache = msal.SerializableTokenCache()
    cache.deserialize(CACHE_PATH.read_text())
    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=cache,
    )
    accounts = app.get_accounts()
    if not accounts:
        fail("No account in cache. Re-run ms_auth.py")

    result = app.acquire_token_silent(RESOURCE_SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        fail("Could not acquire token silently. Re-run ms_auth.py with Files.ReadWrite scope consented.")

    token = result["access_token"]

    # Build a simple file and upload to OneDrive root using Graph:
    # PUT /me/drive/root:/<name>:/content
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    name = f"router-upload-check-{ts}.txt"
    content = f"Hello from ms_upload_test.py at {ts}\n"

    url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{name}:/content"
    r = httpx.put(url, content=content.encode("utf-8"),
                  headers={"Authorization": f"Bearer {token}"},
                  timeout=60)

    print("HTTP status:", r.status_code)
    print("Response:", r.text[:300])
    if r.status_code in (200, 201):
        print(f"✅ Upload OK. Check OneDrive root for {name}")
    else:
        fail("Upload failed. See status/response above.")

if __name__ == "__main__":
    main()
