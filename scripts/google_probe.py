# scripts/google_probe.py
# Purpose: deterministically test your Google OAuth client *without* a browser.
# If the client_id/client_secret pair is valid, Google's token endpoint will return
# {"error":"invalid_grant"} because we're using a FAKE code (that's GOOD for this probe).
# If your client is wrong/invalid/rotated, you'll get {"error":"invalid_client"} (that's BAD).

import json, sys, os
import requests

SECRETS_PATH = os.path.join("secrets", "google_client_secret.json")
TOKEN_URI_DEFAULT = "https://oauth2.googleapis.com/token"

def main():
    if not os.path.isfile(SECRETS_PATH):
        print(f"❌ Missing secrets file: {SECRETS_PATH}")
        sys.exit(1)

    try:
        data = json.load(open(SECRETS_PATH, "r", encoding="utf-8"))
    except Exception as e:
        print(f"❌ Could not read JSON from {SECRETS_PATH}: {e}")
        sys.exit(1)

    top = "installed" if "installed" in data else "web" if "web" in data else None
    if top is None:
        print(f"❌ Unexpected JSON format. Top-level keys: {list(data.keys())}")
        print("   Expecting the official 'Desktop app' JSON with top-level key 'installed'.")
        sys.exit(1)

    cfg = data[top]
    client_id = cfg.get("client_id")
    client_secret = cfg.get("client_secret")
    token_uri = cfg.get("token_uri", TOKEN_URI_DEFAULT)
    redirect_uris = cfg.get("redirect_uris", [])

    print("=== Google OAuth probe ===")
    print("Top-level key  :", top)
    print("client_id      :", client_id)
    print("token_uri      :", token_uri)
    print("redirect_uris  :", redirect_uris[:3], ("... + more" if len(redirect_uris) > 3 else ""))

    if not client_id or not client_secret:
        print("❌ Missing client_id or client_secret in your secrets JSON.")
        sys.exit(1)

    # Desktop apps typically use loopback redirect URIs. We don't need a real redirect here.
    redirect_uri = "http://localhost"

    payload = {
        "code": "FAKE_CODE_123",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        r = requests.post(token_uri, data=payload, timeout=15)
    except Exception as e:
        print(f"❌ Network error calling token endpoint: {e}")
        sys.exit(1)

    print("HTTP status    :", r.status_code)
    print("Body           :", r.text.strip())

    if r.status_code == 400 and '"invalid_grant"' in r.text:
        print("✅ Probe PASS: Your client is valid. (invalid_grant is EXPECTED with fake code)")
        sys.exit(0)
    elif '"invalid_client"' in r.text or r.status_code in (401, 403):
        print("❌ Probe FAIL: invalid_client/unauthorized. Your client_id/secret is not accepted by Google.")
        print("   Fix by recreating a *Desktop app* OAuth client in the SAME project as your consent screen,")
        print("   re-download the JSON, and overwrite secrets/google_client_secret.json.")
        sys.exit(2)
    else:
        print("ℹ️ Probe inconclusive. But if not invalid_client, your client is probably fine.")
        sys.exit(0)

if __name__ == "__main__":
    main()
