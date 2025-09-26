# scripts/ms_sanity.py
# One-time Microsoft Graph token sanity check used inside the API container.
# This version FIXES the scope issue by using ONLY resource scopes in acquire_token_silent.

import os
import sys
import httpx
import msal

def fail(msg: str) -> None:
    print(f"❌ {msg}")
    sys.exit(1)

def main() -> None:
    cache_path = os.getenv("MS_TOKEN_CACHE_PATH", "/app/secrets/ms_token_cache.json")
    client_id = os.getenv("MS_CLIENT_ID", "")
    tenant = os.getenv("MS_TENANT_ID", "consumers")

    print("=== Microsoft token sanity ===")
    print("Using cache path :", cache_path)
    print("MS_CLIENT_ID     :", client_id or "(missing)")
    print("MS_TENANT_ID     :", tenant)
    print("------------------------------")

    if not os.path.exists(cache_path):
        fail("Token cache not found. Run: docker compose exec api python scripts/ms_auth.py")

    cache = msal.SerializableTokenCache()
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache.deserialize(f.read())
    except Exception as e:
        fail(f"Cannot read token cache: {e}")

    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache,
    )

    accounts = app.get_accounts()
    if not accounts:
        fail("No account in cache. Re-run ms_auth.py")

    # IMPORTANT: For silent calls DO NOT include reserved scopes
    # (openid, offline_access, profile). Use ONLY resource scopes.
    RESOURCE_SCOPES = [
        "User.Read",
        "Mail.ReadWrite",
        "Mail.Send",
        "Calendars.ReadWrite",
        "Files.ReadWrite",
    ]

    result = app.acquire_token_silent(RESOURCE_SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        fail("Silent token refresh failed. Re-run ms_auth.py")

    token = result["access_token"]
    try:
        r = httpx.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        print("HTTP status:", r.status_code)
        if r.status_code == 200:
            print("✅ Graph reachable. /me OK")
        else:
            print("Response:", r.text[:300])
            fail("Graph returned a non-200 status.")
    except Exception as e:
        fail(f"HTTP error calling Graph: {e}")

if __name__ == "__main__":
    main()
