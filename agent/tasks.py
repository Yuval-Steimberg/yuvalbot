"""Follow-ups and reminders — the substrate for proactivity.

An agent that only answers when spoken to is a chatbot. The thing that makes
Instinct feel different is that it schedules its own return visits, so every
turn can leave a dated open loop behind.
"""

import os, sqlite3, logging
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


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_due(when: str) -> str:
    """Accept ISO timestamps or 'in 2h' / 'in 30m' / 'in 3d'."""
    w = (when or "").strip().lower()
    if w.startswith("in "):
        n, unit = w[3:].strip()[:-1], w.strip()[-1]
        mult = {"m": "minutes", "h": "hours", "d": "days"}.get(unit)
        if mult and n.isdigit():
            return (datetime.now(timezone.utc) + timedelta(**{mult: int(n)})) \
                .strftime("%Y-%m-%dT%H:%M:%SZ")
    return when


def add(due: str, what: str, channel: str = "whatsapp", repeat_hours: int = 0) -> dict:
    with _con() as con:
        cur = con.execute(
            "INSERT INTO tasks(due,what,channel,repeat_hours,created) VALUES(?,?,?,?,?)",
            (_parse_due(due), what, channel, repeat_hours, now()))
    return {"id": cur.lastrowid, "due": _parse_due(due), "what": what}


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


def recent_turns(limit: int = 20):
    with _con() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,))]
    return list(reversed(rows))


def turns_since(ts: str, limit: int = 400):
    with _con() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM turns WHERE ts>? ORDER BY id LIMIT ?", (ts, limit))]
