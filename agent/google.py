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


class QuotaError(GoogleError):
    """Rate limit or daily quota. The caller should stop and come back later."""

    def __init__(self, msg, retry_after: int = 0, daily: bool = False):
        super().__init__(msg)
        self.retry_after = retry_after
        self.daily = daily


def _api(method: str, url: str, retries: int = 4, **kw):
    """One API call, with backoff on transient limits and a clear signal when the
    daily quota is spent — bulk jobs need to distinguish 'wait 8 seconds' from
    'come back tomorrow'."""
    delay = 2
    for attempt in range(retries):
        r = requests.request(method, url, headers={"Authorization": f"Bearer {_token()}"},
                             timeout=60, **kw)
        if r.status_code < 300:
            return r.json() if r.content else {}
        body = r.text[:400]
        if r.status_code in (429, 403) and any(
                k in body for k in ("rateLimitExceeded", "userRateLimitExceeded",
                                    "quotaExceeded", "Quota exceeded")):
            daily = "Daily Limit" in body or "dailyLimitExceeded" in body
            if daily or attempt == retries - 1:
                raise QuotaError(f"Google quota: {body[:200]}",
                                 retry_after=int(r.headers.get("Retry-After", 0)),
                                 daily=daily)
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code >= 500 and attempt < retries - 1:
            time.sleep(delay)
            delay *= 2
            continue
        raise GoogleError(f"{method} {url.split('/')[-1]} → {r.status_code}: {body[:200]}")
    raise GoogleError("exhausted retries")


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


# ─── Drive ────────────────────────────────────────────────────────────────────

DRIVE = "https://www.googleapis.com/drive/v3"


def drive_search(query: str, limit: int = 10) -> dict:
    d = _api("GET", f"{DRIVE}/files", params={
        "q": f"fullText contains '{query.replace(chr(39), '')}' and trashed=false",
        "pageSize": min(limit, 25), "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"})
    return {"files": d.get("files", [])}


def drive_read(file_id: str, max_chars: int = 8000) -> dict:
    meta = _api("GET", f"{DRIVE}/files/{file_id}", params={"fields": "id,name,mimeType"})
    mime = meta.get("mimeType", "")
    token = _token()
    if mime.startswith("application/vnd.google-apps."):
        export = {"application/vnd.google-apps.document": "text/plain",
                  "application/vnd.google-apps.spreadsheet": "text/csv",
                  "application/vnd.google-apps.presentation": "text/plain"}.get(mime)
        if not export:
            return {"error": f"cannot export {mime}", "name": meta.get("name")}
        r = requests.get(f"{DRIVE}/files/{file_id}/export",
                         headers={"Authorization": f"Bearer {token}"},
                         params={"mimeType": export}, timeout=45)
    else:
        r = requests.get(f"{DRIVE}/files/{file_id}",
                         headers={"Authorization": f"Bearer {token}"},
                         params={"alt": "media"}, timeout=45)
    if r.status_code >= 300:
        return {"error": f"{r.status_code}: {r.text[:200]}"}
    text = r.content.decode("utf-8", "replace")
    return {"id": file_id, "name": meta.get("name"), "text": text[:max_chars],
            "truncated": len(text) > max_chars}


# ─── Contacts (People API) ────────────────────────────────────────────────────

def contacts_search(query: str, limit: int = 8) -> dict:
    d = _api("GET", "https://people.googleapis.com/v1/people:searchContacts",
             params={"query": query, "pageSize": min(limit, 20),
                     "readMask": "names,emailAddresses,phoneNumbers,organizations"})
    out = []
    for r in d.get("results", []):
        p = r.get("person", {})
        out.append({
            "name": (p.get("names") or [{}])[0].get("displayName", ""),
            "emails": [e.get("value") for e in p.get("emailAddresses", [])],
            "phones": [t.get("value") for t in p.get("phoneNumbers", [])],
            "org": (p.get("organizations") or [{}])[0].get("name", "")})
    return {"contacts": out}


# ─── Gmail in bulk ────────────────────────────────────────────────────────────
# Reading 15,000 messages one at a time is a day of API calls. These page over
# ids and mutate up to 1,000 at a time, which is how a mailbox clean-up finishes.

def list_ids(query: str, page_token: str = "", page_size: int = 500) -> dict:
    """Message ids matching a query, one page at a time. Cheap: no bodies."""
    params = {"q": query, "maxResults": min(page_size, 500)}
    if page_token:
        params["pageToken"] = page_token
    d = _api("GET", f"{GM}/messages", params=params)
    return {"ids": [m["id"] for m in d.get("messages", [])],
            "next_page_token": d.get("nextPageToken", ""),
            "estimate": d.get("resultSizeEstimate", 0)}


def headers_of(message_ids: list[str]) -> list[dict]:
    """From/subject/date for a batch of ids, without pulling bodies."""
    out = []
    for mid in message_ids:
        try:
            d = _api("GET", f"{GM}/messages/{mid}", params={
                "format": "metadata",
                "metadataHeaders": ["From", "Subject", "Date", "List-Unsubscribe"]})
        except QuotaError:
            raise
        except GoogleError:
            continue
        p = d.get("payload", {})
        out.append({"id": mid, "thread_id": d.get("threadId"),
                    "from": _header(p, "from"), "subject": _header(p, "subject"),
                    "date": _header(p, "date"), "snippet": d.get("snippet", "")[:300],
                    "unsubscribe": bool(_header(p, "list-unsubscribe")),
                    "labels": d.get("labelIds", [])})
    return out


def batch_modify(message_ids: list[str], add: list | None = None,
                 remove: list | None = None) -> dict:
    """Label / unlabel up to 1,000 messages in one call."""
    done = 0
    for i in range(0, len(message_ids), 1000):
        chunk = message_ids[i:i + 1000]
        _api("POST", f"{GM}/messages/batchModify",
             json={"ids": chunk, "addLabelIds": add or [],
                   "removeLabelIds": remove or []})
        done += len(chunk)
    return {"modified": done, "added": add or [], "removed": remove or []}


def batch_trash(message_ids: list[str]) -> dict:
    """Move messages to Trash (recoverable for 30 days). Never a hard delete —
    a bulk delete that cannot be undone is not something to hand an agent."""
    return batch_modify(message_ids, add=["TRASH"], remove=["INBOX"])


def labels() -> dict:
    d = _api("GET", f"{GM}/labels")
    return {"labels": [{"id": l["id"], "name": l["name"], "type": l.get("type")}
                       for l in d.get("labels", [])]}


def label_id(name: str, create: bool = True) -> str:
    """Resolve a label name to an id, creating it if needed."""
    for l in labels()["labels"]:
        if l["name"].lower() == name.lower():
            return l["id"]
    if not create:
        return ""
    d = _api("POST", f"{GM}/labels", json={
        "name": name, "labelListVisibility": "labelShow",
        "messageListVisibility": "show"})
    log.info(f"🏷  created label {name}")
    return d["id"]


# ─── Drive in bulk ────────────────────────────────────────────────────────────

def drive_page(page_token: str = "", page_size: int = 200,
               query: str = "trashed=false") -> dict:
    """Page over every file with the fields duplicate detection needs."""
    params = {"q": query, "pageSize": min(page_size, 1000),
              "fields": "nextPageToken,files(id,name,size,md5Checksum,mimeType,"
                        "modifiedTime,parents,webViewLink)",
              "orderBy": "folder,name"}
    if page_token:
        params["pageToken"] = page_token
    d = _api("GET", f"{DRIVE}/files", params=params)
    return {"files": d.get("files", []), "next_page_token": d.get("nextPageToken", "")}


def drive_trash(file_id: str) -> dict:
    _api("PATCH", f"{DRIVE}/files/{file_id}", json={"trashed": True})
    return {"trashed": file_id}
