# scripts/g_auth_host.py
# Run this on your Windows host (outside Docker).
# It uses Google's official quickstart approach:
# - Starts a local web server on your Windows localhost
# - Opens your default browser
# - After consent, saves the token into ./secrets/google_token.json
#
# The Docker API container already mounts ./secrets → /app/secrets,
# so the token will be immediately available to the container.

import os
import sys
import json
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes we actually need for the current use cases
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]

def validate_installed_client(client_secret_path: Path) -> None:
    try:
        data = json.loads(client_secret_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Cannot read {client_secret_path}: {e}")
        sys.exit(1)
    if "installed" not in data:
        kind = "web" if "web" in data else "unknown"
        print(f"❌ Wrong client type: {kind}. You must create a Desktop (Installed) OAuth client and download that JSON.")
        print("   Google Cloud Console → APIs & Services → Credentials → Create OAuth Client ID → Desktop app")
        sys.exit(1)
    required = ("client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris")
    missing = [k for k in required if not data["installed"].get(k)]
    if missing:
        print("❌ Missing keys in 'installed':", ", ".join(missing))
        sys.exit(1)

def main():
    # Run on host; secrets live under the project root
    secrets_dir = Path("secrets")
    secrets_dir.mkdir(parents=True, exist_ok=True)

    client_secret_file = secrets_dir / "google_client_secret.json"
    token_path = secrets_dir / "google_token.json"

    print("=== Google Login (HOST) ===")
    print("Client secrets:", client_secret_file.resolve())
    print("Token path    :", token_path.resolve())
    print("===========================")

    if not client_secret_file.exists():
        print("❌ Missing:", client_secret_file)
        print("   Put your downloaded Desktop client JSON there and run again.")
        sys.exit(1)

    validate_installed_client(client_secret_file)

    # Reuse/refresh existing token if present
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

    # Official happy path: run a local server on HOST (your Windows machine)
    # Use port=0 so Windows picks a free port automatically (avoids 'in use' errors).
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), SCOPES)
    creds = flow.run_local_server(
        host="localhost",
        port=0,  # auto-pick a free port
        authorization_prompt_message=None,
        success_message="✅ Google auth complete. You may close this tab.",
        open_browser=True,
        prompt="consent",
    )

    token_path.write_text(creds.to_json(), encoding="utf-8")
    print("✅ Google login successful. Token saved at:", token_path.resolve())

if __name__ == "__main__":
    main()
