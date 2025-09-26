# scripts/g_auth.py
# Final, container-safe Google OAuth helper for Gmail (Readonly + Modify).
# - Uses the official InstalledAppFlow.run_local_server()
# - NEVER tries to open a browser in the container (open_browser=False)
# - Prints the consent URL so you open it in your Windows browser
# - Binds to host='localhost' and port from env (default 8765)
# - Validates that your JSON is a Desktop (Installed) client

import os
import sys
import json
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

def validate_installed_client(client_secret_file: Path) -> None:
    try:
        data = json.loads(client_secret_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Could not read {client_secret_file}: {e}")
        sys.exit(1)
    if "installed" not in data:
        kind = "web" if "web" in data else "unknown"
        print(f"❌ Wrong client type: {kind}. Need Desktop (Installed) client with top-level key 'installed'.")
        print("   Fix: Google Cloud Console → Credentials → Create OAuth client ID → Desktop app → Download JSON")
        sys.exit(1)
    required = ("client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris")
    missing = [k for k in required if not data["installed"].get(k)]
    if missing:
        print("❌ Missing keys in 'installed':", ", ".join(missing))
        sys.exit(1)

def main():
    secrets_dir = Path(os.getenv("GOOGLE_SECRETS_DIR", "/app/secrets"))
    secrets_dir.mkdir(parents=True, exist_ok=True)

    client_secret_file = secrets_dir / "google_client_secret.json"
    token_path = secrets_dir / "google_token.json"
    port = int(os.getenv("GOOGLE_OAUTH_PORT", "8765"))

    print("=== Google Login (official run_local_server; no in-container browser) ===")
    print("Client secrets:", client_secret_file)
    print("Token path    :", token_path)
    print("Callback port :", port)
    print("============================================================")

    if not client_secret_file.exists():
        print("❌ google_client_secret.json missing at:", client_secret_file)
        sys.exit(1)

    validate_installed_client(client_secret_file)

    # Reuse/refresh if token already exists
    creds: Optional[Credentials] = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            creds = None

    if creds and creds.valid:
        print("✅ Existing token is already valid.")
        return

    if creds and creds.expired and creds.refresh_token:
        print("Refreshing existing token...")
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        print("✅ Token refreshed and saved.")
        return

    # Create the flow from Desktop client JSON
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), SCOPES)

    print("\n👉 ACTION REQUIRED")
    print(f"1) Ensure docker-compose maps port {port} (host → container) for the 'api' service.")
    print("2) A URL will be printed below. COPY that URL into your Windows browser and complete consent.")
    print("3) After you see 'The authentication flow has completed' in the browser,")
    print("   return here; the token will be saved.\n")

    # DO NOT try to open a browser in the container; just print the URL.
    # run_local_server(open_browser=False) will print the URL in the terminal.
    creds = flow.run_local_server(
        host="localhost",         # must be localhost to satisfy Google's loopback rule
        port=port,
        authorization_prompt_message="Please visit this URL to authorize this application: {url}",
        success_message="The authentication flow has completed. You may close this window.",
        open_browser=False,       # critical: avoids 'could not locate runnable browser'
        prompt="consent",
    )

    token_path.write_text(creds.to_json(), encoding="utf-8")
    print("✅ Google login successful. Token saved at:", token_path)

if __name__ == "__main__":
    main()
