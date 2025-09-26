"""
Google OAuth Device Code helper for Gmail read-only (local demo).
Requires a Google Cloud project + OAuth consent screen and OAuth Client (Desktop).
"""

import requests
import os
import time

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
SCOPES = os.getenv("GOOGLE_SCOPES", "https://www.googleapis.com/auth/gmail.readonly")

DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"

r = requests.post(DEVICE_URL, data={"client_id": CLIENT_ID, "scope": SCOPES})
r.raise_for_status()
j = r.json()
print("Visit:", j["verification_url"])
print("Enter code:", j["user_code"])

data = {
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "device_code": j["device_code"],
    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
}

while True:
    time.sleep(j["interval"])
    tr = requests.post(TOKEN_URL, data=data)
    if tr.status_code == 200:
        tj = tr.json()
        print("ACCESS_TOKEN:", tj["access_token"])
        print("REFRESH_TOKEN:", tj.get("refresh_token", ""))
        break
    elif tr.status_code == 400 and tr.json().get("error") in ("authorization_pending", "slow_down"):
        continue
    else:
        tr.raise_for_status()
