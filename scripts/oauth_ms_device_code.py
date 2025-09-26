"""
Microsoft Graph Device Code helper (console)
This helps you get an ACCESS TOKEN for simple local tests.
A real deployment should use a proper OAuth redirect flow.
"""

import requests
import time
import os

TENANT = os.getenv("MS_TENANT_ID", "common")
CLIENT_ID = os.getenv("MS_CLIENT_ID")  # app registration id
SCOPES = os.getenv("MS_SCOPES", "Mail.Read offline_access")  # space-separated

device_url = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/devicecode"
token_url = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"

# 1) Start device flow
r = requests.post(device_url, data={"client_id": CLIENT_ID, "scope": SCOPES})
r.raise_for_status()
j = r.json()
print("Go to:", j["verification_uri"])
print("Enter code:", j["user_code"])

# 2) Poll for token
data = {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": CLIENT_ID, "device_code": j["device_code"]}
while True:
    time.sleep(j["interval"])
    tr = requests.post(token_url, data=data)
    if tr.status_code == 200:
        tj = tr.json()
        print("ACCESS_TOKEN:", tj["access_token"])
        print("REFRESH_TOKEN:", tj.get("refresh_token", ""))
        break
    elif tr.status_code == 400 and tr.json().get("error") in ("authorization_pending", "slow_down"):
        continue
    else:
        tr.raise_for_status()
