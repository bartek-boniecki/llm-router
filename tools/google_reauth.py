import argparse, json, os, sys
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--client", required=True, help="Path to client_secret.json")
    p.add_argument("--token", required=True, help="Path where token will be saved (google_token.json)")
    p.add_argument("--scopes", required=True, help="Space-separated scopes")
    args = p.parse_args()

    scopes = args.scopes.split()

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
    except Exception as e:
        print("Missing google-auth libs. Install first:\n  pip install --upgrade google-auth-oauthlib google-auth google-api-python-client")
        sys.exit(2)

    client_path = Path(args.client).resolve()
    token_path = Path(args.token).resolve()
    token_path.parent.mkdir(parents=True, exist_ok=True)

    if not client_path.exists():
        print(f"Client file not found: {client_path}")
        sys.exit(3)

    # Use local server flow (opens your browser) – best for desktop auth
    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), scopes=scopes)
    creds = flow.run_local_server(port=8765, prompt="consent")  # if port busy, change it

    # Write token JSON
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"Wrote token to {token_path}")

if __name__ == "__main__":
    main()
