"""Composio: connect apps with clicks, not a terminal.

The flow this enables, entirely in a browser:
  1. paste a Composio API key once (it is the user's own account)
  2. press Connect next to Gmail -> Google's own account chooser -> back here
  3. press Enable tools -> a tool-router session with an MCP endpoint, which the
     agent's existing MCP client picks up

Composio has renamed this surface more than once (per-app MCP servers, custom
servers, now tool-router sessions), so every call tries the shapes their docs
have used and reports exactly what failed rather than shrugging.
"""

import logging
import requests

from . import config, store

log = logging.getLogger("yuvalbot.composio")

BASES = ["https://backend.composio.dev/api/v3",
         "https://backend.composio.dev/api/v3.1",
         "https://backend.composio.dev/v3"]
TIMEOUT = 30
USER_ID = "owner"          # single-user deployment: one identity is enough

# What a person actually wants connected, in the order they want it.
APPS = [
    ("gmail", "Gmail"),
    ("googlecalendar", "Google Calendar"),
    ("googledrive", "Google Drive"),
    ("notion", "Notion"),
    ("slack", "Slack"),
    ("linear", "Linear"),
    ("todoist", "Todoist"),
    ("github", "GitHub"),
]


class ComposioError(RuntimeError):
    pass


def api_key() -> str:
    return config.setting("COMPOSIO_API_KEY")


def configured() -> bool:
    return bool(api_key())


def _req(method: str, path: str, **kw) -> dict:
    """Call Composio, trying each base until one answers sensibly."""
    if not api_key():
        raise ComposioError("no Composio API key saved")
    headers = {"x-api-key": api_key(), "Content-Type": "application/json"}
    problems = []
    for base in BASES:
        url = base + path
        try:
            r = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kw)
        except Exception as e:
            problems.append(f"{url}: {e}")
            continue
        if r.status_code == 404:
            problems.append(f"{url}: 404")
            continue
        if r.status_code >= 300:
            raise ComposioError(f"{r.status_code} from {url}: {r.text[:300]}")
        try:
            return r.json() if r.content else {}
        except ValueError:
            problems.append(f"{url}: not JSON")
    raise ComposioError("no Composio endpoint answered — tried " + "; ".join(problems))


def _rows(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("items", "data", "results", "auth_configs", "connected_accounts"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


# ─── auth configs: the per-app credential Composio manages for you ────────────

def auth_config_id(toolkit: str) -> str:
    """Find Composio's auth config for an app, creating it if there is none."""
    cached = (store.get("composio_auth_configs", {}) or {}).get(toolkit)
    if cached:
        return cached

    found = ""
    try:
        rows = _rows(_req("GET", "/auth_configs", params={"toolkit_slug": toolkit}))
        for row in rows:
            slug = str(row.get("toolkit", {}).get("slug")
                       if isinstance(row.get("toolkit"), dict)
                       else row.get("toolkit") or row.get("toolkit_slug") or "").lower()
            if slug == toolkit and (row.get("id") or row.get("uuid")):
                found = row.get("id") or row.get("uuid")
                break
    except ComposioError as e:
        log.info(f"listing auth configs for {toolkit} failed: {e}")

    if not found:
        body = {"toolkit": {"slug": toolkit},
                "auth_config": {"type": "use_composio_managed_auth"}}
        created = _req("POST", "/auth_configs", json=body)
        inner = created.get("auth_config") or created
        found = inner.get("id") or inner.get("uuid") or ""
        if not found:
            raise ComposioError(f"Composio created no auth config for {toolkit}: "
                                f"{str(created)[:200]}")

    cache = store.get("composio_auth_configs", {}) or {}
    cache[toolkit] = found
    store.put("composio_auth_configs", cache)
    return found


# ─── connecting an account ────────────────────────────────────────────────────

def connect_url(toolkit: str, callback: str = "") -> str:
    """The URL that shows the app's own consent screen. This is the whole point:
    the user presses a button, picks their Google account, and comes back."""
    body = {"auth_config_id": auth_config_id(toolkit), "user_id": USER_ID}
    if callback:
        body["callback_url"] = callback
    try:
        d = _req("POST", "/connected_accounts/link", json=body)
    except ComposioError as e:
        if "404" not in str(e):
            raise
        d = _req("POST", "/connected_accounts", json=body)   # pre-2026 shape
    url = (d.get("redirect_url") or d.get("redirectUrl")
           or (d.get("connectionData") or {}).get("redirect_url")
           or (d.get("connected_account") or {}).get("redirect_url") or "")
    if not url:
        raise ComposioError(f"no redirect URL came back: {str(d)[:200]}")
    return url


def accounts() -> list[dict]:
    """Which apps are connected, as Composio sees it."""
    try:
        rows = _rows(_req("GET", "/connected_accounts", params={"user_ids": USER_ID}))
    except ComposioError as e:
        log.info(f"listing connected accounts failed: {e}")
        return []
    out = []
    for row in rows:
        tk = row.get("toolkit")
        slug = (tk.get("slug") if isinstance(tk, dict) else tk) or row.get("appName", "")
        out.append({"toolkit": str(slug).lower(),
                    "status": str(row.get("status", "")).upper(),
                    "id": row.get("id") or row.get("uuid", "")})
    # Cached so the agent knows what is connected without a network call on every
    # turn — and so it never tells its owner to connect something twice.
    store.put("composio_connected",
              sorted({a["toolkit"] for a in out
                      if a["status"].startswith("ACTIVE") or a["status"] == "INITIATED"}))
    return out


def connected_apps() -> list[str]:
    """Apps connected through Composio, from cache. A connection is permanent;
    only the session in front of it is short-lived, and that is remade for you."""
    return store.get("composio_connected", []) or []


def live() -> bool:
    """Is there a working Composio session right now?"""
    from . import mcp
    state = mcp.status().get("composio", {})
    return bool(state) and not state.get("error")


# ─── the MCP endpoint the agent actually uses ─────────────────────────────────

def session() -> dict:
    """Create a tool-router session and return its MCP URL."""
    body = {"user_id": USER_ID, "mcp": True}
    last = None
    for path in ("/tool_router/session", "/tool_router/sessions", "/sessions"):
        try:
            d = _req("POST", path, json=body)
        except ComposioError as e:
            last = e
            if "404" in str(e):
                continue
            raise
        mcp_url = ((d.get("mcp") or {}).get("url") if isinstance(d.get("mcp"), dict)
                   else d.get("mcp_url") or d.get("url"))
        sid = d.get("session_id") or d.get("id") or ""
        if not mcp_url and sid:
            mcp_url = f"https://app.composio.dev/tool_router/v3/{sid}/mcp"
        if mcp_url:
            return {"session_id": sid, "url": mcp_url}
    raise ComposioError(f"could not create a tool-router session: {last}")


def wire(name: str = "composio") -> dict:
    """Create the session and hand the MCP endpoint to the agent's MCP client."""
    from . import mcp
    s = session()
    servers = store.get("mcp_servers", {}) or {}
    servers[name] = {"url": s["url"], "headers": {"x-api-key": api_key()},
                     "trust": "gated", "hosted": True, "composio": True}
    store.put("mcp_servers", servers)
    store.put("composio_session", s)
    mcp.reload()
    state = mcp.status().get(name, {})
    if state.get("error"):
        return {"ok": False, "error": state["error"], "url": s["url"]}
    return {"ok": True, "tools": state.get("tools", 0), "session_id": s["session_id"]}


def refresh_if_stale() -> dict:
    """Sessions expire. If the agent's Composio tools stop answering, remake it."""
    from . import mcp
    state = mcp.status().get("composio", {})
    if state and state.get("error"):
        log.info("composio session looks stale, recreating")
        return wire()
    return {"ok": True, "tools": state.get("tools", 0)}
