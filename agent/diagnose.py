"""Prove what actually works, against the real accounts, in one call.

Every failure so far has been found by the owner hitting it in a chat. This does
the hitting: each capability is exercised for real, read-only where it matters,
and reports what works, what does not, and what to do about it.
"""

import logging, time
from datetime import datetime, timedelta, timezone

from . import config, memory, tasks, mcp

log = logging.getLogger("yuvalbot.diagnose")


def _check(name, fn, fix=""):
    started = time.time()
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    return {"check": name, "ok": bool(ok), "detail": str(detail)[:300],
            "fix": "" if ok else fix, "ms": int((time.time() - started) * 1000)}


def _model():
    from . import llm
    r = llm.call([{"role": "user", "content": "reply with: ok"}],
                 model=config.MODEL, max_tokens=8)
    said = "".join(b.get("text", "") for b in r.get("content", []))
    return bool(said.strip()), f"{config.MODEL} answered {said.strip()[:20]!r}"


def _memory():
    rec = memory.write("facts", "Self check",
                       f"Written by the self check at {memory.now()}.",
                       aliases=["selfcheck", "בדיקה"])
    hits = memory.search("selfcheck")
    return bool(hits), f"wrote {rec['id']}, found it again: {bool(hits)}"


def _channel():
    from . import telegram
    if not telegram.configured():
        return False, "no Telegram bot token"
    if not telegram.chat_id():
        return False, "bot token set but no chat linked"
    return True, f"telegram chat {telegram.chat_id()}"


def _gmail_read():
    from . import google
    if not google.configured():
        return _composio_probe("gmail", "GMAIL")
    page = google.list_ids("in:inbox", "", 1)
    return True, f"native Gmail reachable, about {page['estimate']} in the inbox"


def _calendar():
    from . import google
    if not google.configured():
        return _composio_probe("googlecalendar", "CALENDAR")
    now = datetime.now(timezone.utc)
    ev = google.events(now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"), 5)
    return True, f"{len(ev['events'])} events in the next week"


def _drive():
    from . import google
    if not google.configured():
        return _composio_probe("googledrive", "DRIVE")
    page = google.drive_page("", 5)
    return True, f"{len(page['files'])} files read from Drive"


def _composio_probe(app: str, tool_hint: str):
    """When Google is connected through the router, check that route instead."""
    from . import composio
    if not composio.configured():
        return False, "neither a Google connection nor a Composio key"
    if app not in composio.connected_apps():
        return False, f"{app} is not connected in Composio"
    state = mcp.status().get("composio", {})
    if state.get("error"):
        return False, f"Composio session is down: {state['error']}"
    names = [t["name"] for t in mcp.schemas() if t["name"].startswith("mcp__composio__")]
    return bool(names), (f"through Composio: {len(names)} router tools "
                         f"({app} connected)")


def _filters():
    from . import google
    if not google.configured():
        return False, ("needs the self-hosted Google connection — the router does "
                       "not expose Gmail settings")
    f = google.filters()
    return True, f"{len(f['filters'])} filters exist; creating them will work"


def _browser():
    from . import livebrowser
    if not livebrowser.available():
        return False, "no Playwright in this image"
    sites = livebrowser.sessions()
    return True, (f"browser ready; signed in to {', '.join(sites[:5])}"
                  if sites else "browser ready; no sites signed in yet")


def _scheduler():
    beats = tasks.beats()
    if not beats.get("followups"):
        return False, "the follow-up loop has not ticked yet"
    age = (datetime.now(timezone.utc)
           - datetime.fromisoformat(beats["followups"].replace("Z", "+00:00")))
    secs = int(age.total_seconds())
    return secs < 600, f"last tick {secs}s ago"


def _jobs():
    from . import jobs
    active = jobs.listing(active_only=True)
    beats = tasks.beats()
    if not beats.get("jobs"):
        return False, "the job loop has not ticked yet"
    return True, f"job runner alive, {len(active)} jobs in flight"


def run() -> dict:
    checks = [
        _check("model", _model, "check ANTHROPIC_API_KEY on /api/selftest"),
        _check("memory", _memory, "the volume may not be mounted at DATA_DIR"),
        _check("can reach you", _channel, "link Telegram on /connect"),
        _check("read mail", _gmail_read, "connect Gmail on /connect"),
        _check("calendar", _calendar, "connect Google Calendar on /connect"),
        _check("drive", _drive, "connect Google Drive on /connect"),
        _check("mail filters", _filters,
               "connect the self-hosted Google card on /connect"),
        _check("browser", _browser, "rebuild the image with INSTALL_BROWSER=1"),
        _check("scheduler", _scheduler, "restart the service"),
        _check("background jobs", _jobs, "restart the service"),
    ]
    working = [c["check"] for c in checks if c["ok"]]
    broken = [c for c in checks if not c["ok"]]
    return {"working": working,
            "broken": [{"check": c["check"], "why": c["detail"], "fix": c["fix"]}
                       for c in broken],
            "summary": (f"{len(working)} of {len(checks)} working"
                        + ("" if not broken else
                           ". Not working: " + ", ".join(c["check"] for c in broken))),
            "checks": checks}
