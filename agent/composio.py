"""Composio: turn an API key into a usable MCP endpoint.

Composio has moved its MCP surface more than once (per-app servers, custom
servers, now sessions), so nothing here assumes one shape. Every known endpoint
is tried, the first that answers wins, and if none do the raw replies are shown
rather than a shrug — the user can then paste a URL by hand.
"""

import logging
import requests

from . import config

log = logging.getLogger("yuvalbot.composio")

BASES = ["https://backend.composio.dev/api/v3", "https://backend.composio.dev/v3"]
LIST_PATHS = ["/mcp/servers", "/mcp", "/mcp/custom"]
TIMEOUT = 25


def api_key() -> str:
    return config.setting("COMPOSIO_API_KEY")


def _headers() -> dict:
    return {"x-api-key": api_key(), "Content-Type": "application/json"}


def _rows(payload) -> list:
    """Their list responses have been items / data / results / a bare array."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("items", "data", "results", "servers", "mcp_servers"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
    return []


def _server(row: dict) -> dict:
    sid = row.get("id") or row.get("uuid") or row.get("server_id") or ""
    url = (row.get("mcp_url") or row.get("url") or row.get("server_url")
           or row.get("endpoint") or "")
    return {"id": sid, "name": row.get("name") or row.get("slug") or sid,
            "toolkits": row.get("toolkits") or row.get("apps") or [],
            "url": url or (f"https://backend.composio.dev/v3/mcp/{sid}" if sid else "")}


def discover(user_id: str = "default") -> dict:
    """Find MCP servers this key can see. Returns what was tried on failure."""
    if not api_key():
        return {"ok": False, "error": "no Composio API key saved yet"}
    tried = []
    for base in BASES:
        for path in LIST_PATHS:
            url = base + path
            try:
                r = requests.get(url, headers=_headers(), timeout=TIMEOUT)
            except Exception as e:
                tried.append({"url": url, "error": str(e)[:120]})
                continue
            if r.status_code >= 300:
                tried.append({"url": url, "status": r.status_code,
                              "body": r.text[:160]})
                continue
            try:
                rows = _rows(r.json())
            except ValueError:
                tried.append({"url": url, "error": "not JSON"})
                continue
            servers = [_server(x) for x in rows if isinstance(x, dict)]
            servers = [s for s in servers if s["url"]]
            for s in servers:
                if "user_id=" not in s["url"]:
                    joiner = "&" if "?" in s["url"] else "?"
                    s["url"] = f"{s['url']}{joiner}user_id={user_id}"
            return {"ok": True, "source": url, "servers": servers}
    return {"ok": False, "error": "no Composio endpoint answered with a server list",
            "tried": tried}


def create(name: str, toolkits: list[str], user_id: str = "default") -> dict:
    """Ask Composio to make a server for these toolkits, if the key allows it."""
    if not api_key():
        return {"ok": False, "error": "no Composio API key saved yet"}
    body = {"name": name, "toolkits": [t.lower() for t in toolkits],
            "user_id": user_id}
    tried = []
    for base in BASES:
        for path in ("/mcp/servers", "/mcp/custom", "/mcp"):
            url = base + path
            try:
                r = requests.post(url, headers=_headers(), json=body, timeout=TIMEOUT)
            except Exception as e:
                tried.append({"url": url, "error": str(e)[:120]})
                continue
            if r.status_code < 300:
                try:
                    return {"ok": True, "server": _server(r.json()), "source": url}
                except ValueError:
                    tried.append({"url": url, "error": "not JSON"})
                    continue
            tried.append({"url": url, "status": r.status_code, "body": r.text[:160]})
    return {"ok": False, "error": "Composio would not create a server with this key",
            "tried": tried}
