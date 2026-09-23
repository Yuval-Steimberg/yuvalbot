"""Human-in-the-loop gate for irreversible actions."""

import json, sqlite3, logging
from datetime import datetime, timezone
from . import config

log = logging.getLogger("yuvalbot.approvals")

# Tools that reach outside the box or spend money.
GATED = {"send_email", "gmail_send", "gmail_reply", "calendar_create_event",
         "browser_act", "shell", "gmail_bulk_trash", "gmail_send_draft", "gmail_create_filter", "drive_move"}


def _con():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with _con() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS approvals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created TEXT, tool TEXT, args TEXT, summary TEXT,
            status TEXT DEFAULT 'pending', decided TEXT, result TEXT)""")
        cols = {r[1] for r in con.execute("PRAGMA table_info(approvals)")}
        if "run_at" not in cols:        # older deployments predate scheduled sends
            con.execute("ALTER TABLE approvals ADD COLUMN run_at TEXT")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def request(tool: str, args: dict, summary: str, run_at: str | None = None) -> dict:
    with _con() as con:
        cur = con.execute(
            "INSERT INTO approvals(created,tool,args,summary,run_at) VALUES(?,?,?,?,?)",
            (now(), tool, json.dumps(args)[:8000], summary[:500], run_at))
    log.info(f"🖐 approval #{cur.lastrowid} needed: {summary[:80]}")
    out = {"status": "awaiting_approval", "approval_id": cur.lastrowid,
           "summary": summary,
           "note": "Tell the user what you want to do and that you need a yes. "
                   "Nothing has happened yet."}
    if run_at:
        out["run_at"] = run_at
        out["note"] = ("Show them the text and say when it goes out. One yes covers "
                       "it — you will not ask again at send time.")
    return out


def pending(limit: int = 20):
    with _con() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM approvals WHERE status='pending' ORDER BY id LIMIT ?", (limit,))]


def decide(approval_id: int, approved: bool) -> dict:
    """Approve → execute the stored call now. Deny → close it out."""
    with _con() as con:
        row = con.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            return {"error": f"no approval #{approval_id}"}
        if row["status"] != "pending":
            return {"error": f"approval #{approval_id} already {row['status']}"}
    if not approved:
        with _con() as con:
            con.execute("UPDATE approvals SET status='denied',decided=? WHERE id=?",
                        (now(), approval_id))
        return {"id": approval_id, "status": "denied"}

    if row["run_at"] and row["run_at"] > now():
        # Approved ahead of time: the yes is the only one we will ask for.
        with _con() as con:
            con.execute("UPDATE approvals SET status='scheduled',decided=? WHERE id=?",
                        (now(), approval_id))
        log.info(f"⏳ approval #{approval_id} scheduled for {row['run_at']}")
        return {"id": approval_id, "status": "scheduled", "run_at": row["run_at"]}

    from . import tools
    result = tools.execute(row["tool"], json.loads(row["args"]))
    with _con() as con:
        con.execute("UPDATE approvals SET status='approved',decided=?,result=? WHERE id=?",
                    (now(), str(result)[:2000], approval_id))
    log.info(f"✅ approval #{approval_id} executed: {row['tool']}")
    return {"id": approval_id, "status": "approved", "result": result}


def scheduled(limit: int = 20):
    """Approved actions still waiting for their clock time."""
    with _con() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM approvals WHERE status='scheduled' ORDER BY run_at LIMIT ?",
            (limit,))]


def run_due(limit: int = 10) -> list:
    """Execute approvals whose send time has arrived. Called by the scheduler."""
    from . import tools
    done = []
    with _con() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM approvals WHERE status='scheduled' AND run_at<=? "
            "ORDER BY run_at LIMIT ?", (now(), limit))]
    for row in rows:
        try:
            result = tools.execute(row["tool"], json.loads(row["args"]))
        except Exception as e:                      # never let one send stall the loop
            result = {"error": f"{type(e).__name__}: {e}"}
        with _con() as con:
            con.execute("UPDATE approvals SET status='approved',decided=?,result=? "
                        "WHERE id=?", (now(), str(result)[:2000], row["id"]))
        log.info(f"📤 scheduled approval #{row['id']} ran: {row['tool']}")
        done.append({"id": row["id"], "tool": row["tool"], "summary": row["summary"],
                     "result": result})
    return done
