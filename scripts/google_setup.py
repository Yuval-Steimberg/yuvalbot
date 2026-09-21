"""One-time: turn a Google OAuth client into a refresh token.

Run this on your own laptop (it opens a browser and listens on localhost),
not on the server.

1. console.cloud.google.com → create a project
2. APIs & Services → Library → enable: Gmail API, Google Calendar API,
   Google Drive API, People API
3. OAuth consent screen → External → add your own Gmail as a test user
4. Credentials → Create credentials → OAuth client ID → Desktop app
   → copy the client id and secret
5. GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... python scripts/google_setup.py
6. Put the printed GOOGLE_REFRESH_TOKEN into the deployment's variables.
"""

import os, sys, threading, urllib.parse, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

SCOPES = ["https://www.googleapis.com/auth/gmail.modify",
          "https://www.googleapis.com/auth/calendar",
          "https://www.googleapis.com/auth/drive.readonly",
          "https://www.googleapis.com/auth/contacts.readonly"]
PORT = int(os.environ.get("OAUTH_PORT", "8765"))
REDIRECT = f"http://localhost:{PORT}"   # Google killed the out-of-band flow;
                                        # a loopback redirect is what works now.
_code: dict[str, str] = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _code.update({k: v[0] for k, v in q.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        ok = "code" in _code
        self.wfile.write(
            (f"<h2>{'Done — close this tab and go back to the terminal.' if ok else 'No code received: ' + _code.get('error', 'unknown error')}</h2>")
            .encode())

    def log_message(self, *a):
        pass


def main():
    cid, secret = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first")

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent"})

    server = HTTPServer(("localhost", PORT), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print(f"\nOpening your browser. If it does not open, paste this:\n\n{url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print(f"Waiting for the redirect on {REDIRECT} …")
    while "code" not in _code and "error" not in _code:
        pass
    if "error" in _code:
        sys.exit(f"authorization failed: {_code['error']}")

    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "code": _code["code"], "client_id": cid, "client_secret": secret,
        "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
    if r.status_code != 200:
        sys.exit(f"token exchange failed {r.status_code}: {r.text}")
    token = r.json().get("refresh_token")
    if not token:
        sys.exit("no refresh_token returned — revoke the app at "
                 "myaccount.google.com/permissions and run this again")
    print(f"\n✅ GOOGLE_REFRESH_TOKEN={token}\n")


if __name__ == "__main__":
    main()
