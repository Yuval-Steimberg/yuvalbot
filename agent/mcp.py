"""MCP client — one integration, then any app with an MCP server.

Servers are declared in mcp.json (or the MCP_SERVERS env var) and their tools
are discovered at runtime and handed to the model as mcp__<server>__<tool>.

Two things this module treats as hostile:
  • tool descriptions, which are third-party text the model reads — a server can
    try to talk the agent into something, so writes stay behind the approval gate
  • servers that claim to be read-only — 'trust' is set by you in mcp.json, never
    by the server itself
"""

import os, re, json, time, shutil, logging, subprocess, threading
from pathlib import Path

import requests

log = logging.getLogger("yuvalbot.mcp")

CONFIG_PATH = Path(os.environ.get("MCP_CONFIG", "mcp.json"))
PROTOCOL = "2024-11-05"

# Hosted providers name tools GMAIL_FETCH_EMAILS, SLACK_SEND_MESSAGE — the verb
# sits anywhere in the name, so every word is examined. A write verb anywhere
# means the gate; only a name that reads and never writes runs free; anything
# unrecognised is gated, because guessing wrong in that direction is cheap.
READ_WORDS = {"search", "list", "get", "read", "fetch", "query", "find", "describe",
              "check", "lookup", "browse", "view", "show", "info", "count",
              "download", "export", "retrieve", "status"}
WRITE_WORDS = {"send", "create", "add", "update", "delete", "remove", "trash",
               "archive", "move", "post", "reply", "forward", "modify", "edit",
               "write", "upload", "schedule", "book", "buy", "pay", "cancel",
               "invite", "share", "set", "patch", "put", "star", "label", "draft",
               "mark", "assign", "close", "merge", "run", "execute", "enable",
               "disable", "revoke", "approve"}

_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def _tool_words(name: str) -> set[str]:
    return {w.lower() for w in _WORDS.findall(name.replace("_", " "))}

_clients: dict[str, "Server"] = {}
_lock = threading.Lock()


def _config() -> dict:
    """Servers from MCP_SERVERS, from mcp.json, and from ones added in the UI."""
    servers = dict(_env_config())
    try:
        from . import store
        servers.update(store.get("mcp_servers", {}) or {})
    except Exception as e:
        log.error(f"stored MCP servers unreadable: {e}")
    return servers


def _env_config() -> dict:
    raw = os.environ.get("MCP_SERVERS", "")
    if raw:
        try:
            return json.loads(raw).get("mcpServers", json.loads(raw))
        except json.JSONDecodeError as e:
            log.error(f"MCP_SERVERS is not valid JSON: {e}")
            return {}
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text()).get("mcpServers", {})
        except json.JSONDecodeError as e:
            log.error(f"{CONFIG_PATH} is not valid JSON: {e}")
    return {}


class Server:
    """One MCP server: stdio subprocess or streamable HTTP endpoint."""

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg
        self.trust = cfg.get("trust", "gated")
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self.error = ""
        self.session = ""
        self._id = 0
        self._io = threading.Lock()

    # ── transport ────────────────────────────────────────────────────────────

    @property
    def is_http(self) -> bool:
        return bool(self.cfg.get("url"))

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _start_stdio(self):
        cmd = [self.cfg["command"], *self.cfg.get("args", [])]
        if not shutil.which(cmd[0]):
            raise RuntimeError(f"'{cmd[0]}' not installed in this image")
        env = {**os.environ, **{k: str(v) for k, v in self.cfg.get("env", {}).items()}}
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, env=env,
                                     bufsize=1)
        self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                 "clientInfo": {"name": "yuvalbot", "version": "1.0"}})
        self._notify("notifications/initialized")

    def _rpc(self, method: str, params: dict | None = None, timeout: int = 20) -> dict:
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": method,
               "params": params or {}}
        if self.is_http:
            headers = {"Content-Type": "application/json",
                       "Accept": "application/json, text/event-stream",
                       "MCP-Protocol-Version": PROTOCOL,
                       **self.cfg.get("headers", {})}
            if self.session:
                headers["Mcp-Session-Id"] = self.session
            r = requests.post(self.cfg["url"], json=msg, headers=headers,
                              timeout=(8, timeout))
            if r.status_code >= 300:
                raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
            got = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
            if got:
                self.session = got
            body = r.text.strip()
            if body.startswith("event:") or body.startswith("data:"):   # SSE framing
                for line in body.splitlines():
                    if line.startswith("data:"):
                        body = line[5:].strip()
            data = json.loads(body)
        else:
            with self._io:
                if not self.proc or self.proc.poll() is not None:
                    self._start_stdio()
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
                deadline = time.time() + timeout
                data = None
                while time.time() < deadline:
                    line = self.proc.stdout.readline()
                    if not line:
                        raise RuntimeError("server closed the pipe")
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("id") == msg["id"]:
                        data = d
                        break
                if data is None:
                    raise TimeoutError(f"no reply to {method}")
        if "error" in data:
            raise RuntimeError(str(data["error"])[:300])
        return data.get("result", {})

    def _notify(self, method: str, params: dict | None = None):
        if self.is_http:
            headers = {"Content-Type": "application/json",
                       "Accept": "application/json, text/event-stream",
                       "MCP-Protocol-Version": PROTOCOL,
                       **self.cfg.get("headers", {})}
            if self.session:
                headers["Mcp-Session-Id"] = self.session
            try:
                requests.post(self.cfg["url"], timeout=20, headers=headers,
                              json={"jsonrpc": "2.0", "method": method,
                                    "params": params or {}})
            except Exception as e:
                log.debug(f"notify {method} failed: {e}")
            return
        with self._io:
            self.proc.stdin.write(json.dumps(
                {"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n")
            self.proc.stdin.flush()

    # ── api ──────────────────────────────────────────────────────────────────

    def connect(self) -> list[dict]:
        try:
            if self.is_http:
                self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                         "clientInfo": {"name": "yuvalbot",
                                                        "version": "1.0"}})
                self._notify("notifications/initialized")
            elif not self.proc:
                self._start_stdio()
            self.tools = self._rpc("tools/list").get("tools", [])
            self.error = ""
            log.info(f"🔌 mcp '{self.name}': {len(self.tools)} tools "
                     f"({self.trust})")
        except Exception as e:
            self.error = str(e)
            self.tools = []
            log.error(f"mcp '{self.name}' unavailable: {e}")
        return self.tools

    def call(self, tool: str, args: dict) -> dict:
        try:
            res = self._rpc("tools/call", {"name": tool, "arguments": args}, timeout=180)
        except Exception as e:
            return {"error": f"mcp call failed: {e}"}
        text = "\n".join(c.get("text", "") for c in res.get("content", [])
                         if c.get("type") == "text")
        out = {"result": text[:8000] or res.get("structuredContent") or "(no content)"}
        if res.get("isError"):
            out = {"error": text[:2000] or "tool reported an error"}
        return out

    def close(self):
        if self.proc:
            self.proc.terminate()
            self.proc = None


# ─── registry ─────────────────────────────────────────────────────────────────

def servers() -> dict[str, Server]:
    with _lock:
        if not _clients:
            for name, cfg in _config().items():
                if cfg.get("disabled"):
                    continue
                s = Server(name, cfg)
                s.connect()
                _clients[name] = s
        return _clients


def reload() -> dict:
    with _lock:
        for s in _clients.values():
            s.close()
        _clients.clear()
    return status()


def status() -> dict:
    return {name: {"tools": len(s.tools), "trust": s.trust,
                   "transport": "http" if s.is_http else "stdio",
                   "error": s.error}
            for name, s in servers().items()}


def schemas() -> list[dict]:
    """MCP tools as Anthropic tool definitions."""
    out = []
    for name, s in servers().items():
        for t in s.tools:
            desc = (t.get("description") or "")[:900]
            out.append({
                "name": f"mcp__{name}__{t['name']}"[:64],
                "description": f"[{name}] {desc}",
                "input_schema": t.get("inputSchema") or
                                {"type": "object", "properties": {}}})
    return out


def is_mcp(tool_name: str) -> bool:
    return tool_name.startswith("mcp__")


def split(tool_name: str) -> tuple[str, str]:
    _, server, tool = tool_name.split("__", 2)
    return server, tool


# Composio's tool router exposes a handful of meta-tools rather than one tool per
# action: the real action arrives as an argument. Judging the wrapper name alone
# would gate every read behind an approval, or worse, wave a write through.
WRAPPERS = {"execute_tool", "composio_execute_tool", "execute", "call_tool",
            "run_tool", "composio_multi_execute_tool"}
NESTED_KEYS = ("arguments", "input", "params", "parameters", "body", "payload",
               "tool_input")
# A multi-execute call carries a list of actions under a plural key; every one of
# them has to be classified, and one write in the batch gates the whole call.
BATCH_KEYS = ("tools", "actions", "calls", "items", "requests", "operations")
INNER_KEYS = ("tool_slug", "tool_name", "toolSlug", "toolName", "slug", "action",
              "tool", "name")


WRAPPER_WORDS = ("execute", "run", "invoke", "call", "workbench", "code",
                 "bash", "shell", "script", "proxy", "perform")


def _is_wrapper(tool: str) -> bool:
    """A router tool that carries the real action inside its arguments.

    Composio has shipped several of these — execute_tool, multi_execute, a code
    workbench — so recognise the family rather than a list of names.
    """
    low = tool.lower()
    return low in WRAPPERS or any(w in low for w in WRAPPER_WORDS)


def _inner_tool(args: dict, depth: int = 0) -> str:
    """Dig the real action out of a wrapper's arguments, whatever shape it takes."""
    args = args or {}
    for k in INNER_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, list) and v:
            if isinstance(v[0], str):
                return " ".join(x for x in v if isinstance(x, str))
            if isinstance(v[0], dict) and depth < 3:
                names = [_inner_tool(x, depth + 1) for x in v if isinstance(x, dict)]
                if any(names):
                    return " ".join(n for n in names if n)
        if isinstance(v, dict) and depth < 3:
            found = _inner_tool(v, depth + 1)
            if found:
                return found
    for k in BATCH_KEYS:                           # {"tools": [{"tool_slug": ...}]}
        v = args.get(k)
        if isinstance(v, list) and v and depth < 3:
            names = [_inner_tool(x, depth + 1) for x in v if isinstance(x, dict)]
            names += [x for x in v if isinstance(x, str)]
            if any(names):
                return " ".join(n for n in names if n)
    for k in NESTED_KEYS:                          # {"arguments": {"tool_slug": ...}}
        v = args.get(k)
        if isinstance(v, dict) and depth < 3:
            found = _inner_tool(v, depth + 1)
            if found:
                return found
    return ""


# Composio's router often runs a snippet of code rather than a named action, so
# the action hides inside a string: composio.tools.execute("GMAIL_FETCH_EMAILS").
# Pull every toolkit slug out of whatever the arguments contain.
_SLUG = re.compile(r"\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+\b")


def _strings(value, depth: int = 0) -> list[str]:
    if depth > 4:
        return []
    if isinstance(value, str):
        return [value[:4000]]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v, depth + 1)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v, depth + 1)]
    return []


def _slugs_in(args: dict) -> list[str]:
    found = []
    for text in _strings(args):
        found += _SLUG.findall(text)
    return found


def _classify(name: str) -> bool:
    """True when this name should wait for a human."""
    words = _tool_words(name)
    if words & WRITE_WORDS:
        return True
    return not (words & READ_WORDS)


def needs_approval(tool_name: str, args: dict | None = None) -> bool:
    """Reads run free; anything else waits for a human. The server does not get
    a vote — 'trust' comes from your config, never from the server."""
    server, tool = split(tool_name)
    s = servers().get(server)
    if not s:
        return True
    if s.trust == "read_only":
        return False
    # An action named anywhere in the arguments outranks the tool's own name: a
    # wrapper is only ever as dangerous as what it carries.
    inner = _inner_tool(args or {})
    if inner:
        return _classify(inner)
    slugs = _slugs_in(args or {})
    if slugs:
        return any(_classify(s) for s in slugs)   # one write gates the call

    if _is_wrapper(tool):

        # No action name anywhere — judge the words of whatever was passed. A
        # snippet that only reads should not cost an approval every time; a
        # snippet mentioning send or delete still does.
        blob = " ".join(_strings(args or {}))[:4000]
        if blob:
            words = _tool_words(blob)
            if words & WRITE_WORDS:
                return True
            if words & READ_WORDS:
                return False
        return True
    return _classify(tool)


def call(tool_name: str, args: dict) -> dict:
    server, tool = split(tool_name)
    s = servers().get(server)
    if not s:
        return {"error": f"no MCP server '{server}'"}
    if s.error:
        return {"error": f"server '{server}' unavailable: {s.error}"}
    return s.call(tool, args)
