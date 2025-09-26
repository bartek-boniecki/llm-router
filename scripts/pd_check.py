# scripts/pd_check.py
# Sanity check your Pipedrive token and base URL
import os, sys
import requests

API_BASE = os.getenv("PIPEDRIVE_API_BASE", "https://api.pipedrive.com/v1")
API_TOKEN = os.getenv("PIPEDRIVE_API_TOKEN")

def main():
    if not API_TOKEN:
        print("❌ PIPEDRIVE_API_TOKEN is missing (check your .env).")
        sys.exit(1)

    try:
        r = requests.get(f"{API_BASE}/users/me", params={"api_token": API_TOKEN}, timeout=20)
        print("HTTP:", r.status_code)
        if r.ok:
            me = r.json().get("data", {})
            print("✅ Token OK for:", me.get("name"), "| email:", me.get("email"))
            print("Company domain:", me.get("company_domain"))
        else:
            print("Body:", r.text)
            print("❌ Token check failed.")
            sys.exit(2)
    except Exception as e:
        print("❌ Request error:", repr(e))
        sys.exit(3)

if __name__ == "__main__":
    main()
