"""Gmail + Google Calendar over REST, authenticated with a stored refresh token.

Setup once (see README): create an OAuth client, grant gmail.modify and
calendar scopes, and put GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET /
GOOGLE_REFRESH_TOKEN in the environment. No google SDK dependency.
"""

import os, re, base64, time, logging, requests
from email.mime.text import MIMEText

log = logging.getLogger("yuvalbot.google")
_TOKEN = {"value": "", "expires": 0}


class GoogleError(RuntimeError):
    pass


def configured() -> bool:
    return all(os.environ.get(k) for k in
               ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"))


def _token() -> str:
    if not configured():
        raise GoogleError("Google not connected (GOOGLE_CLIENT_ID / "
                          "GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN)")
    if _TOKEN["value"] and _TOKEN["expires"] > time.time() + 60:
        return _TOKEN["value"]
    r = requests.post("https://oauth2.googleapis.com/token", timeout=25, data={
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise GoogleError(f"token refresh failed {r.status_code}: {r.text[:200]}")
    d = r.json()
    _TOKEN.update(value=d["access_token"], expires=time.time() + d.get("expires_in", 3600))
    return _TOKEN["value"]


def _api(method: str, url: str, **kw):
    r = requests.request(method, url, headers={"Authorization": f"Bearer {_token()}"},
                         timeout=30, **kw)
    if r.status_code >= 300:
        raise GoogleError(f"{method} {url.split('/')[-1]} → {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


# ─── Gmail ────────────────────────────────────────────────────────────────────

GM = "https://gmail.googleapis.com/gmail/v1/users/me"


def _header(payload, name):
    for h in payload.get("headers", []):
        if h["name"].lower() == name:
            return h["value"]
    return ""


def _body_text(payload) -> str:
    if payload.get("body", {}).get("data"):
        raw = base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode(
            "utf-8", "replace")
        if payload.get("mimeType") == "text/html":
            raw = re.sub(r"(?s)<[^>]+>", " ", raw)
        return raw
    for part in payload.get("parts", []):
        t = _body_text(part)
        if t.strip():
            return t
    return ""


def search(query: str, limit: int = 8) -> dict:
    ids = _api("GET", f"{GM}/messages",
               params={"q": query, "maxResults": min(limit, 20)}).get("messages", [])
    out = []
    for m in ids:
        d = _api("GET", f"{GM}/messages/{m['id']}", params={"format": "full"})
        p = d.get("payload", {})
        out.append({"id": m["id"], "thread_id": d.get("threadId"),
                    "from": _header(p, "from"), "to": _header(p, "to"),
                    "subject": _header(p, "subject"), "date": _header(p, "date"),
                    "snippet": d.get("snippet", ""),
                    "body": re.sub(r"\s{2,}", " ", _body_text(p))[:2500]})
    return {"query": query, "messages": out}


def send(to: str, subject: str, body: str, thread_id: str | None = None,
         in_reply_to: str | None = None) -> dict:
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"], msg["Subject"] = to, subject
    if in_reply_to:
        msg["In-Reply-To"] = msg["References"] = in_reply_to
    payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if thread_id:
        payload["threadId"] = thread_id
    d = _api("POST", f"{GM}/messages/send", json=payload)
    log.info(f"📧 gmail sent to {to}: {subject[:60]}")
    return {"sent": True, "id": d.get("id"), "to": to, "subject": subject}


def reply(message_id: str, body: str) -> dict:
    d = _api("GET", f"{GM}/messages/{message_id}", params={"format": "metadata"})
    p = d.get("payload", {})
    frm = _header(p, "reply-to") or _header(p, "from")
    subj = _header(p, "subject")
    return send(frm, subj if subj.lower().startswith("re:") else f"Re: {subj}",
                body, thread_id=d.get("threadId"), in_reply_to=_header(p, "message-id"))


def modify(message_id: str, add: list | None = None, remove: list | None = None) -> dict:
    _api("POST", f"{GM}/messages/{message_id}/modify",
         json={"addLabelIds": add or [], "removeLabelIds": remove or []})
    return {"id": message_id, "added": add or [], "removed": remove or []}


# ─── Calendar ─────────────────────────────────────────────────────────────────

CAL = "https://www.googleapis.com/calendar/v3/calendars/primary"


def events(time_min: str, time_max: str, limit: int = 20) -> dict:
    d = _api("GET", f"{CAL}/events",
             params={"timeMin": time_min, "timeMax": time_max, "singleEvents": "true",
                     "orderBy": "startTime", "maxResults": limit})
    return {"events": [{"id": e.get("id"), "summary": e.get("summary"),
                        "start": e.get("start", {}).get("dateTime") or
                                 e.get("start", {}).get("date"),
                        "end": e.get("end", {}).get("dateTime") or
                               e.get("end", {}).get("date"),
                        "location": e.get("location"),
                        "attendees": [a.get("email") for a in e.get("attendees", [])]}
                       for e in d.get("items", [])]}


def create_event(summary: str, start: str, end: str, description: str = "",
                 location: str = "", attendees: list | None = None,
                 timezone: str | None = None) -> dict:
    tz = timezone or os.environ.get("TIMEZONE", "Asia/Jerusalem")
    body = {"summary": summary, "description": description, "location": location,
            "start": {"dateTime": start, "timeZone": tz},
            "end": {"dateTime": end, "timeZone": tz}}
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    d = _api("POST", f"{CAL}/events", json=body,
             params={"sendUpdates": "all" if attendees else "none"})
    log.info(f"📅 event created: {summary}")
    return {"created": True, "id": d.get("id"), "link": d.get("htmlLink"),
            "summary": summary, "start": start}
