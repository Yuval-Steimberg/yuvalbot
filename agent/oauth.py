"""Browser-based Google connection: click a link, approve, done.

The alternative — clone the repo, pip install, run a script, copy a refresh
token into a deployment variable — is a wall for anyone not at a keyboard. Here
the app runs the OAuth dance itself and keeps the refresh token on its volume.

The one thing that cannot be automated away is creating a Google OAuth client:
Google will not hand a personal deployment credentials for someone else's app.
That is a one-time paste of a client id and secret, doable from a phone.
"""

import base64, hashlib, hmac, json, logging, os, time, urllib.parse

import requests

from . import config, store

log = logging.getLogger("yuvalbot.oauth")

SCOPES = ["https://www.googleapis.com/auth/gmail.modify",
          "https://www.googleapis.com/auth/gmail.compose",
          "https://www.googleapis.com/auth/calendar",
          "https://www.googleapis.com/auth/drive.readonly",
          "https://www.googleapis.com/auth/contacts.readonly"]


def redirect_uri() -> str:
    return f"{config.PUBLIC_URL}/oauth/google/callback"


def client() -> tuple[str, str]:
    return (store.setting("google_client_id", "GOOGLE_CLIENT_ID"),
            store.setting("google_client_secret", "GOOGLE_CLIENT_SECRET"))


# ─── signed links, so a link sent over Telegram just works ────────────────────

def _secret() -> bytes:
    return (os.environ.get("SECRET_KEY") or os.environ.get("VAULT_KEY")
            or "insecure-dev-key").encode()


def sign(purpose: str, ttl_seconds: int = 1800) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"p": purpose, "exp": int(time.time()) + ttl_seconds}).encode()
    ).decode().rstrip("=")
    mac = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{mac}"


def verify(token: str, purpose: str) -> bool:
    try:
        payload, mac = (token or "").split(".", 1)
    except ValueError:
        return False
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac, expected):
        return False
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=="))
    except Exception:
        return False
    return data.get("p") == purpose and data.get("exp", 0) > time.time()


def connect_link() -> str:
    """A link the owner can tap from anywhere, valid for 30 minutes."""
    return f"{config.PUBLIC_URL}/connect?t={sign('connect')}"


# ─── the flow ─────────────────────────────────────────────────────────────────

def auth_url() -> str:
    cid, _ = client()
    if not cid:
        raise RuntimeError("no Google client id yet")
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": sign("google-oauth", 900)})


def exchange(code: str) -> dict:
    cid, secret = client()
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "code": code, "client_id": cid, "client_secret": secret,
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code"})
    if r.status_code != 200:
        return {"ok": False, "error": f"{r.status_code}: {r.text[:300]}"}
    token = r.json().get("refresh_token")
    if not token:
        return {"ok": False,
                "error": "Google returned no refresh token — this account already "
                         "granted access. Revoke it at "
                         "myaccount.google.com/permissions and connect again."}
    store.put("google_refresh_token", token)
    log.info("🔗 Google connected through the browser")
    return {"ok": True}


def connected() -> bool:
    return bool(store.setting("google_refresh_token", "GOOGLE_REFRESH_TOKEN"))
