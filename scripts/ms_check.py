# scripts/ms_check.py
"""
Checks that your Microsoft Graph token cache works and lists 1 OneDrive file.

IMPORTANT:
- During interactive login (device-code), it is OK to include OIDC scopes like
  'openid profile offline_access'.
- But when calling acquire_token_silent(), DO NOT include those reserved scopes.
  Ask only for resource scopes, e.g. 'https://graph.microsoft.com/Files.ReadWrite'.

Run inside the API container:
    docker compose exec api python scripts/ms_check.py
"""

import os
import sys
import pathlib
import httpx
import msal

CACHE_PATH = pathlib.Path("/app/secrets/ms_token_cache.json")
CLIENT_ID = os.getenv("MS_CLIENT_ID")
TENANT_ID = os.getenv("MS_TENANT_ID", "organizations")

# For silent acquisition, request ONLY the Graph resource scope(s)
RESOURCE_SCOPES = ["https://graph.microsoft.com/Files.ReadWrite"]


def fail(msg: str):
    print(f"❌ {msg}")
    sys.exit(1)


def main():
    if not CLIENT_ID:
        fail("MS_CLIENT_ID is missing in environment (.env).")

    if not CACHE_PATH.exists():
        fail("Token cache not found. Run: docker compose exec api python scripts/ms_auth.py")

    # Load token cache
    cache = msal.SerializableTokenCache()
    try:
        cache.deserialize(CACHE_PATH.read_text())
    except Exception as e:
        fail(f"Could not read token cache: {e}")

    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=cache,
    )

    accounts = app.get_accounts()
    if not accounts:
        fail("No account in cache. Re-run: docker compose exec api python scripts/ms_auth.py")

    # Acquire a token silently for the resource scope ONLY (no openid/profile/offline_access here)
    result = app.acquire_token_silent(RESOURCE_SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        fail(
            "Could not acquire token silently. "
            "Re-run the device-code helper so the cached account has Graph consent: "
            "docker compose exec api python scripts/ms_auth.py"
        )

    access_token = result["access_token"]

    # Call Graph: list first file in OneDrive root
    url = "https://graph.microsoft.com/v1.0/me/drive/root/children?$top=1"
    r = httpx.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    print("HTTP status:", r.status_code)
    if r.status_code == 200:
        data = r.json()
        name = (data.get("value") or [{}])[0].get("name", "(no file found)")
        print("Sample file name:", name)
        print("✅ OneDrive access OK.")
    else:
        print("Response text:", r.text)
        fail("Graph call failed. Check Files.ReadWrite permission and redo ms_auth.py.")

if __name__ == "__main__":
    main()
