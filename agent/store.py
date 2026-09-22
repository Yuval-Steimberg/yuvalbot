"""Encrypted settings that live on the volume, not in environment variables.

Credentials obtained through the browser (a Google refresh token, an OAuth
client) have to be stored by the running app, and a person on their phone cannot
edit a Railway variable. They go here: sqlite on the mounted volume, encrypted
with VAULT_KEY, or SECRET_KEY when no vault key is set.
"""

import base64, hashlib, json, logging, os, sqlite3

from . import config

log = logging.getLogger("yuvalbot.store")


def _fernet():
    from cryptography.fernet import Fernet
    raw = os.environ.get("VAULT_KEY") or os.environ.get("SECRET_KEY") or ""
    if not raw:
        raise RuntimeError("set VAULT_KEY or SECRET_KEY before storing credentials")
    key = raw if len(raw) == 44 else base64.urlsafe_b64encode(
        hashlib.sha256(raw.encode()).digest()).decode()
    return Fernet(key.encode())


def _con():
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with _con() as con:
        con.execute("CREATE TABLE IF NOT EXISTS settings("
                    "key TEXT PRIMARY KEY, value BLOB, updated TEXT)")


def put(key: str, value) -> dict:
    blob = _fernet().encrypt(json.dumps(value).encode())
    with _con() as con:
        con.execute("INSERT INTO settings(key,value,updated) VALUES(?,?,datetime('now')) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "updated=excluded.updated", (key, blob))
    log.info(f"🔐 stored '{key}'")
    return {"stored": key}


def get(key: str, default=None):
    try:
        with _con() as con:
            row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        return json.loads(_fernet().decrypt(row["value"]).decode())
    except Exception as e:
        log.error(f"could not read '{key}': {e}")
        return default


def delete(key: str) -> dict:
    with _con() as con:
        con.execute("DELETE FROM settings WHERE key=?", (key,))
    return {"deleted": key}


def keys() -> list[str]:
    try:
        with _con() as con:
            return [r["key"] for r in con.execute("SELECT key FROM settings ORDER BY key")]
    except Exception:
        return []


def setting(name: str, env: str, default: str = "") -> str:
    """Environment first (explicit operator intent), then what the browser saved."""
    return os.environ.get(env) or store_str(name) or default


def store_str(name: str) -> str:
    v = get(name)
    return v if isinstance(v, str) else ""
