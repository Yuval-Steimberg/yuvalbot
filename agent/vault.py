"""Encrypted credential store.

Instinct keeps cached credentials so it can act between your messages. Storing
third-party passwords is the single most dangerous thing this system does, so:
secrets are Fernet-encrypted at rest with a key that lives only in the
environment, values are never returned to the model — tools reference a secret
by name and the value is substituted at call time.
"""

import os, json, base64, hashlib, logging
from pathlib import Path

log = logging.getLogger("yuvalbot.vault")
from . import config
STORE = config.VAULT_PATH


def _fernet():
    from cryptography.fernet import Fernet
    key = os.environ.get("VAULT_KEY", "")
    if not key:
        raise RuntimeError("VAULT_KEY not set — refusing to store secrets in plaintext")
    if len(key) != 44:  # not already a Fernet key → derive one
        key = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()).decode()
    return Fernet(key.encode())


def _load() -> dict:
    if not STORE.exists():
        return {}
    try:
        return json.loads(_fernet().decrypt(STORE.read_bytes()).decode())
    except Exception as e:
        log.error(f"vault unreadable: {e}")
        return {}


def _save(data: dict):
    STORE.write_bytes(_fernet().encrypt(json.dumps(data).encode()))
    os.chmod(STORE, 0o600)


def put(name: str, value: str) -> dict:
    d = _load(); d[name] = value; _save(d)
    log.info(f"🔐 stored secret '{name}'")
    return {"stored": name}


def get(name: str) -> str | None:
    return _load().get(name)


def names() -> list[str]:
    return sorted(_load().keys())


def delete(name: str) -> dict:
    d = _load(); d.pop(name, None); _save(d)
    return {"deleted": name}


def fill(text: str) -> str:
    """Replace {{secret:NAME}} placeholders — the only path a secret takes out."""
    import re
    return re.sub(r"\{\{secret:([A-Za-z0-9_.-]+)\}\}",
                  lambda m: get(m.group(1)) or "", text or "")
