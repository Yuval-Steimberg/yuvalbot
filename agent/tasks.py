"""Follow-ups and reminders — the substrate for proactivity.

An agent that only answers when spoken to is a chatbot. The thing that makes
Instinct feel different is that it schedules its own return visits, so every
turn can leave a dated open loop behind.
"""

import os, re, sqlite3, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("yuvalbot.tasks")
DB = Path(os.environ.get("AGENT_DB", "agent.db"))


def _con():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with _con() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            due TEXT NOT NULL,
            what TEXT NOT NULL,
            channel TEXT DEFAULT 'whatsapp',
            repeat_hours INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created TEXT, last_run TEXT, result TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS turns(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, role TEXT, content TEXT, channel TEXT)""")
    _repair_due_dates()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repair_due_dates():
    """Rescue follow-ups stored before due dates were validated.

    Rows holding something like 'in 3 minutes' can never come due, so they sit
    there forever. Make them due now: a reminder arriving late beats one that
    never arrives.
    """
    with _con() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id,due FROM tasks WHERE status='pending'")]
        for r in rows:
            try:
                datetime.fromisoformat((r["due"] or "").replace("Z", "+00:00"))
            except ValueError:
                con.execute("UPDATE tasks SET due=? WHERE id=?", (now(), r["id"]))
                log.warning(f"repaired unfireable follow-up #{r['id']} "
                            f"(was {r['due']!r}) — it will run on the next tick")


UNITS = {"s": "seconds", "sec": "seconds", "secs": "seconds", "second": "seconds",
         "seconds": "seconds",
         "m": "minutes", "min": "minutes", "mins": "minutes", "minute": "minutes",
         "minutes": "minutes",
         "h": "hours", "hr": "hours", "hrs": "hours", "hour": "hours",
         "hours": "hours",
         "d": "days", "day": "days", "days": "days",
         "w": "weeks", "week": "weeks", "weeks": "weeks"}

_REL = re.compile(r"^(?:in\s+)?(\d+)\s*([a-z]+)$")


class BadDueDate(ValueError):
    """Raised rather than storing a due date that can never come due.

    Due dates are compared as strings, so an unparsed 'in 3 minutes' sorts after
    every timestamp and the follow-up simply never fires. Silence is the worst
    possible failure for a reminder, so refuse it at the door.
    """


def _parse_due(when: str) -> str:
    """'in 3 minutes' | '3m' | 'in 2 hours' | an ISO timestamp -> ISO UTC."""
    w = (when or "").strip().lower()
    if not w:
        raise BadDueDate("no time given")

    m = _REL.match(w)
    if m and m.group(2) in UNITS:
        delta = timedelta(**{UNITS[m.group(2)]: int(m.group(1))})
        return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")

    iso = when.strip().replace("z", "Z")
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        raise BadDueDate(
            f"could not read '{when}' as a time. Use 'in 20 minutes', 'in 3 hours', "
            f"'in 2 days', or an ISO8601 UTC timestamp like "
            f"{(datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if dt.tzinfo is None:                  # naive means UTC here, not local
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add(due: str, what: str, channel: str = "auto", repeat_hours: int = 0) -> dict:
    when = _parse_due(due)               # raises BadDueDate rather than never firing
    with _con() as con:
        cur = con.execute(
            "INSERT INTO tasks(due,what,channel,repeat_hours,created) VALUES(?,?,?,?,?)",
            (when, what, channel, repeat_hours, now()))
    log.info(f"⏰ follow-up #{cur.lastrowid} due {when}: {what[:60]}")
    return {"id": cur.lastrowid, "due_utc": when, "what": what,
            "fires_in_seconds": max(0, int(
                (datetime.fromisoformat(when.replace("Z", "+00:00"))
                 - datetime.now(timezone.utc)).total_seconds()))}


def due_now(limit: int = 5):
    with _con() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM tasks WHERE status='pending' AND due<=? ORDER BY due LIMIT ?",
            (now(), limit))]


def pending(limit: int = 50):
    with _con() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM tasks WHERE status='pending' ORDER BY due LIMIT ?", (limit,))]


def finish(task_id: int, result: str):
    with _con() as con:
        row = con.execute("SELECT repeat_hours FROM tasks WHERE id=?", (task_id,)).fetchone()
        rep = row["repeat_hours"] if row else 0
        if rep:
            nxt = (datetime.now(timezone.utc) + timedelta(hours=rep)) \
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            con.execute("UPDATE tasks SET due=?,last_run=?,result=? WHERE id=?",
                        (nxt, now(), result[:2000], task_id))
        else:
            con.execute("UPDATE tasks SET status='done',last_run=?,result=? WHERE id=?",
                        (now(), result[:2000], task_id))


def cancel(task_id: int) -> dict:
    with _con() as con:
        con.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))
    return {"id": task_id, "status": "cancelled"}


def log_turn(role: str, content: str, channel: str = "web"):
    with _con() as con:
        con.execute("INSERT INTO turns(ts,role,content,channel) VALUES(?,?,?,?)",
                    (now(), role, content[:20000], channel))


def beat(name: str):
    """Record that a scheduled loop ran. Without this, a dead scheduler looks
    exactly like an idle one — which is how a reminder goes missing."""
    from . import store
    beats = store.get("heartbeats", {}) or {}
    beats[name] = now()
    store.put("heartbeats", beats)


def beats() -> dict:
    from . import store
    return store.get("heartbeats", {}) or {}


def recent_turns(limit: int = 20):
    with _con() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,))]
    return list(reversed(rows))


def turns_since(ts: str, limit: int = 400):
    with _con() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM turns WHERE ts>? ORDER BY id LIMIT ?", (ts, limit))]
