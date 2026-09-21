"""MCP client — one integration, then any app with an MCP server.

Servers are declared in mcp.json (or the MCP_SERVERS env var) and their tools
are discovered at runtime and handed to the model as mcp__<server>__<tool>.

Two things this module treats as hostile:
  • tool descriptions, which are third-party text the model reads — a server can
    try to talk the agent into something, so writes stay behind the approval gate
  • servers that claim to be read-only — 'trust' is set by you in mcp.json, never
    by the server itself
"""

import os, json, time, shutil, logging, subprocess, threading
from pathlib import Path

import requests

log = logging.getLogger("yuvalbot.mcp")

CONFIG_PATH = Path(os.environ.get("MCP_CONFIG", "mcp.json"))
PROTOCOL = "2024-11-05"

# Tools whose names look like reads run without asking; everything else needs a
# yes. A server marked "trust": "read_only" skips the gate entirely.
READ_PREFIXES = ("search", "list", "get", "read", "fetch", "query", "find",
                 "describe", "check", "lookup", "browse", "view")

_clients: dict[str, "Server"] = {}
_lock = threading.Lock()


def _config() -> dict:
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

    def _rpc(self, method: str, params: dict | None = None, timeout: int = 60) -> dict:
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": method,
               "params": params or {}}
        if self.is_http:
            headers = {"Content-Type": "application/json",
                       "Accept": "application/json, text/event-stream",
                       **self.cfg.get("headers", {})}
            r = requests.post(self.cfg["url"], json=msg, headers=headers, timeout=timeout)
            if r.status_code >= 300:
                raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
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


def needs_approval(tool_name: str) -> bool:
    """Reads run free; anything else waits for a human. The server does not get
    a vote — 'trust' comes from your mcp.json."""
    server, tool = split(tool_name)
    s = servers().get(server)
    if not s:
        return True
    if s.trust == "read_only":
        return False
    return not tool.lower().lstrip("_").startswith(READ_PREFIXES)


def call(tool_name: str, args: dict) -> dict:
    server, tool = split(tool_name)
    s = servers().get(server)
    if not s:
        return {"error": f"no MCP server '{server}'"}
    if s.error:
        return {"error": f"server '{server}' unavailable: {s.error}"}
    return s.call(tool, args)
