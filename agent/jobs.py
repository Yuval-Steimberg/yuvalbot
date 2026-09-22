"""Long-running jobs: work that outlives a single message.

A chat turn is bounded — a dozen tool calls and it is over. Sorting fifteen
thousand emails is not that shape: it takes hours, it runs into Gmail's daily
quota, and the person wants to hear "85% done, finishing tomorrow" rather than
nothing. So a job is a row in sqlite with its own state, advanced one slice at a
time by the scheduler, able to stop for a day and pick up exactly where it was.
"""

import json, sqlite3, logging, threading
from datetime import datetime, timedelta, timezone

from . import config

log = logging.getLogger("yuvalbot.jobs")

SLICE_SECONDS = int(__import__("os").environ.get("JOB_SLICE_SECONDS", "45"))
_running = threading.Lock()


def _con():
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with _con() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS jobs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL, goal TEXT, params TEXT, state TEXT,
            status TEXT DEFAULT 'queued',     -- queued|running|waiting|done|failed|cancelled
            progress TEXT, result TEXT, error TEXT,
            channel TEXT DEFAULT 'auto',
            created TEXT, updated TEXT, next_run_at TEXT, last_report TEXT)""")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _in(**kw) -> str:
    return (datetime.now(timezone.utc) + timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


def start(kind: str, goal: str = "", params: dict | None = None,
          channel: str = "auto") -> dict:
    from . import workers
    if kind not in workers.HANDLERS:
        return {"error": f"unknown job kind '{kind}'. Known: "
                         f"{', '.join(workers.HANDLERS)}"}
    with _con() as con:
        cur = con.execute(
            "INSERT INTO jobs(kind,goal,params,state,created,updated,next_run_at,"
            "progress,channel) VALUES(?,?,?,?,?,?,?,?,?)",
            (kind, goal, json.dumps(params or {}), json.dumps({}), now(), now(),
             now(), "queued", channel))
    log.info(f"🧵 job #{cur.lastrowid} started: {kind} — {goal[:60]}")
    return {"job_id": cur.lastrowid, "kind": kind, "status": "queued",
            "note": "Running in the background. Tell the user it is underway and "
                    "that you will report progress — do not wait for it here."}


def get(job_id: int) -> dict | None:
    with _con() as con:
        r = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(r) if r else None


def listing(limit: int = 20, active_only: bool = False) -> list[dict]:
    q = "SELECT id,kind,goal,status,progress,next_run_at,updated,error FROM jobs"
    if active_only:
        q += " WHERE status IN ('queued','running','waiting')"
    q += " ORDER BY id DESC LIMIT ?"
    with _con() as con:
        return [dict(r) for r in con.execute(q, (limit,))]


def cancel(job_id: int) -> dict:
    with _con() as con:
        con.execute("UPDATE jobs SET status='cancelled',updated=? WHERE id=? "
                    "AND status IN ('queued','running','waiting')", (now(), job_id))
    return {"job_id": job_id, "status": "cancelled"}


def _save(job_id: int, **fields):
    fields["updated"] = now()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _con() as con:
        con.execute(f"UPDATE jobs SET {sets} WHERE id=?",
                    (*fields.values(), job_id))


def _emit(job: dict, text: str):
    """Send the owner a progress line on whichever channel they use."""
    from . import channels
    body = f"[{job['kind']} #{job['id']}] {text}"
    log.info(f"📣 {body[:140]}")
    try:
        channels.send(job.get("channel") or "auto", body)
    except Exception as e:
        log.error(f"job report failed: {e}")


def tick() -> list[dict]:
    """Advance one due job by one slice. Called by the scheduler every minute."""
    from . import workers
    from .google import QuotaError

    if not _running.acquire(blocking=False):
        return []                      # a slice is already in flight
    try:
        with _con() as con:
            row = con.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','waiting') "
                "AND next_run_at<=? ORDER BY next_run_at LIMIT 1", (now(),)).fetchone()
        if not row:
            return []
        job = dict(row)
        state = json.loads(job.get("state") or "{}")
        params = json.loads(job.get("params") or "{}")
        _save(job["id"], status="running")

        try:
            out = workers.HANDLERS[job["kind"]](job, params, state)
        except QuotaError as e:
            # The distinction that matters: a burst limit is minutes, the daily
            # quota is tomorrow. Either way the job keeps its place.
            when = _in(hours=10) if e.daily else _in(minutes=max(e.retry_after // 60, 15))
            _save(job["id"], status="waiting", next_run_at=when,
                  state=json.dumps(state))
            sofar = (job.get("progress") or "").strip()
            _emit(job, f"Google cut me off for now "
                       f"({'daily limit' if e.daily else 'rate limit'}). Progress is "
                       f"saved — resuming {when[:16]} UTC."
                       + (f" So far: {sofar}." if sofar else ""))
            return [{"id": job["id"], "status": "waiting"}]
        except Exception as e:
            log.error(f"job #{job['id']} failed: {e}")
            _save(job["id"], status="failed", error=str(e)[:1000],
                  state=json.dumps(state))
            _emit(job, f"Stopped: {e}")
            return [{"id": job["id"], "status": "failed"}]

        state = out.get("state", state)
        progress = out.get("progress", job.get("progress") or "")
        if out.get("done"):
            _save(job["id"], status="done", state=json.dumps(state),
                  progress=progress, result=json.dumps(out.get("result", {}))[:20000])
            _emit(job, out.get("summary") or f"Finished. {progress}")
            return [{"id": job["id"], "status": "done"}]

        _save(job["id"], status="queued", state=json.dumps(state),
              progress=progress, next_run_at=out.get("next_run_at", now()))
        if out.get("report"):
            _save(job["id"], last_report=now())
            _emit(job, out["report"])
        return [{"id": job["id"], "status": "queued", "progress": progress}]
    finally:
        _running.release()
