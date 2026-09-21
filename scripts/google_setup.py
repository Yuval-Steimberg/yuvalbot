"""One-time: turn a Google OAuth client into a refresh token.

1. console.cloud.google.com → create project → enable Gmail API + Calendar API
2. Enable Drive API + People API too, then OAuth consent screen
   (External, add yourself as a test user)
3. Credentials → OAuth client ID → Desktop app → copy id + secret
4. GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... python scripts/google_setup.py
5. Put the printed GOOGLE_REFRESH_TOKEN in the agent's environment.
"""

import os, sys, urllib.parse, requests

SCOPES = ["https://www.googleapis.com/auth/gmail.modify",
          "https://www.googleapis.com/auth/calendar",
          "https://www.googleapis.com/auth/drive.readonly",
          "https://www.googleapis.com/auth/contacts.readonly"]
REDIRECT = "urn:ietf:wg:oauth:2.0:oob"

cid, secret = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
if not cid or not secret:
    sys.exit("set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first")

url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
    "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent"})
print(f"\n1. Open:\n{url}\n")
code = input("2. Paste the code here: ").strip()

r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
    "code": code, "client_id": cid, "client_secret": secret,
    "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
if r.status_code != 200:
    sys.exit(f"exchange failed {r.status_code}: {r.text}")
print(f"\nGOOGLE_REFRESH_TOKEN={r.json()['refresh_token']}\n")
