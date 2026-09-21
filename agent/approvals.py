"""Human-in-the-loop gate for irreversible actions."""

import json, sqlite3, logging
from datetime import datetime, timezone
from . import config

log = logging.getLogger("yuvalbot.approvals")

# Tools that reach outside the box or spend money.
GATED = {"send_email", "gmail_send", "gmail_reply", "calendar_create_event",
         "browser_act", "shell"}


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


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def request(tool: str, args: dict, summary: str) -> dict:
    with _con() as con:
        cur = con.execute(
            "INSERT INTO approvals(created,tool,args,summary) VALUES(?,?,?,?)",
            (now(), tool, json.dumps(args)[:8000], summary[:500]))
    log.info(f"🖐 approval #{cur.lastrowid} needed: {summary[:80]}")
    return {"status": "awaiting_approval", "approval_id": cur.lastrowid,
            "summary": summary,
            "note": "Tell the user what you want to do and that you need a yes. "
                    "Nothing has happened yet."}


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

    from . import tools
    result = tools.execute(row["tool"], json.loads(row["args"]))
    with _con() as con:
        con.execute("UPDATE approvals SET status='approved',decided=?,result=? WHERE id=?",
                    (now(), str(result)[:2000], approval_id))
    log.info(f"✅ approval #{approval_id} executed: {row['tool']}")
    return {"id": approval_id, "status": "approved", "result": result}
