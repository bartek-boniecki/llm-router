# scripts/ms_auth.py
"""
Device-code login for PERSONAL Microsoft accounts (MS_TENANT_ID=consumers).
Writes token cache to /app/secrets/ms_token_cache.json so the API integration can use it.

Run:
  docker compose exec api python scripts/ms_auth.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import List

import msal
import httpx


def fail(msg: str) -> None:
    print(f"❌ {msg}")
    sys.exit(1)


def main() -> None:
    client_id = os.getenv("MS_CLIENT_ID")
    tenant_id = os.getenv("MS_TENANT_ID", "consumers")
    # IMPORTANT: Only Graph *resource* scopes here. No 'openid', 'profile', or 'offline_access'.
    # We ask consent for Files (OneDrive), Mail+Calendars (optional for later), User profile.
    scopes_env = os.getenv("MS_SCOPES", "User.Read Files.ReadWrite")
    scopes: List[str] = [s for s in scopes_env.split() if s.strip()]

    cache_path = pathlib.Path("/app/secrets/ms_token_cache.json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    print("=== ms_auth.py: environment ===")
    print("MS_CLIENT_ID :", client_id)
    print("MS_TENANT_ID :", tenant_id)
    print("MS_SCOPES    :", scopes)
    print("===============================")

    if not client_id:
        fail("MS_CLIENT_ID is missing in environment (.env)")

    # Create MSAL app with a serializable cache
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        try:
            cache.deserialize(cache_path.read_text())
        except Exception:
            # If corrupt, start clean
            pass

    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )

    # Begin device flow
    flow = app.initiate_device_flow(scopes=[f"https://graph.microsoft.com/{s}" for s in scopes])
    if "user_code" not in flow:
        fail(f"Failed to create device flow: {json.dumps(flow, indent=2)}")

    print("\n=== Microsoft Device Login ===")
    print("To sign in, use a web browser to open the page "
          f"{flow['verification_uri']} and enter the code {flow['user_code']} to authenticate.")
    print("=============================\n")

    # Poll for token
    result = app.acquire_token_by_device_flow(flow)

    # Handle common errors from AAD
    if not result or "access_token" not in result:
        print("❌ Login failed:")
        print(json.dumps(result, indent=2))
        sys.exit(1)

    # Save updated cache
    cache_path.write_text(cache.serialize())
    print(f"✅ Login complete. Token cache saved at {cache_path}")

    # Quick sanity: call Graph /me
    try:
        r = httpx.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {result['access_token']}"},
            timeout=20,
        )
        print("Graph /me status:", r.status_code)
        if r.status_code != 200:
            print("Response:", r.text[:500])
    except Exception as e:
        print("Warning: Graph /me sanity check failed:", repr(e))


if __name__ == "__main__":
    main()
