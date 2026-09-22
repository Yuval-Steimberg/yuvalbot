"""End-to-end checks with every external service stubbed.

Run: python tests/test_all.py
There are no live credentials in development, so anything that leaves the box is
faked — but every path through this codebase is exercised for real.
"""

import base64, json, os, sys, tempfile, threading, time, http.server

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = tempfile.mkdtemp()
os.environ.update(DATA_DIR=TMP, AGENT_DB=f"{TMP}/agent.db", MEMORY_DIR=f"{TMP}/memory",
                  SECRET_KEY="test-secret", OWNER_NAME="Yuval",
                  RAILWAY_PUBLIC_DOMAIN="example.up.railway.app")

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


import agent                                                            # noqa: E402
from agent import (memory, tasks, brain, tools, approvals, jobs, workers,  # noqa: E402
                   google, channels, llm, mcp, store, oauth, telegram, config, docs)
agent.boot()

sent = []
channels.send = lambda ch, body, subject="": sent.append((ch, body)) or {"ok": True}

print("\nmemory")
memory.write("preferences", "Coffee", "Black, no sugar.", aliases=["coffee", "קפה"])
check("write and find", memory.search("coffee")[0]["id"] == "preferences/coffee")
check("alias in another language", bool(memory.search("קפה")))
memory.write("preferences", "Coffee", "2026-09-22: oat milk now.", mode="append")
rec = memory.read("preferences/coffee")
check("correction appends, keeps the old line",
      "Black" in rec["body"] and "oat milk" in rec["body"])
check("history is in git", "memory: " in memory.history("preferences/coffee"))
memory.archive("preferences/coffee", "test")
check("archived record is demoted", memory.read("preferences/coffee")["meta"]["status"]
      == "archived")

print("\nfollow-ups")
for phrase in ("in 3 minutes", "3 min", "in 2 hours", "2026-09-22T20:14:00+03:00"):
    try:
        tasks._parse_due(phrase)
        ok = True
    except tasks.BadDueDate:
        ok = False
    check(f"understands {phrase!r}", ok)
try:
    tasks._parse_due("whenever")
    check("refuses nonsense rather than never firing", False)
except tasks.BadDueDate:
    check("refuses nonsense rather than never firing", True)

sent.clear()
tasks.add("in 0 minutes", "tell a joke", "telegram")
llm.call = lambda *a, **k: {"content": [{"type": "text", "text": "A joke."}]}
brain.tick()
check("a reminder the model only wrote is still delivered",
      sent and sent[-1][1] == "A joke.")

print("\napprovals")
gated = tools.dispatch("gmail_send", {"to": "a@b.com", "subject": "s", "body": "b"})
check("sending waits for a yes", gated.get("status") == "awaiting_approval")
google.send = lambda *a, **k: {"sent": True}
done = approvals.decide(gated["approval_id"], True)
check("approving executes the stored call", done.get("result", {}).get("sent") is True)
check("reading is not gated", tools._needs_approval("gmail_search", {}) is False)

gated_twice_a = tools.dispatch("gmail_send", {"to": "x@y.com", "subject": "s",
                                              "body": "b"})
gated_twice_b = tools.dispatch("gmail_send", {"to": "x@y.com", "subject": "s",
                                              "body": "b"})
check("asking for the same approval twice reuses the first",
      gated_twice_a["approval_id"] == gated_twice_b["approval_id"])
approvals.decide(gated_twice_a["approval_id"], False)

print("\njobs")
state = {"promotions": 2500, "tripped": False}
def list_ids(q, page="", size=500):
    if "promotions" in q and "in:inbox" in q:
        if not state["tripped"] and state["promotions"] < 2500:
            state["tripped"] = True
            raise google.QuotaError("Daily Limit Exceeded", daily=True)
        n = min(size, state["promotions"]); state["promotions"] -= n
        return {"ids": [f"p{i}" for i in range(n)], "next_page_token": "", "estimate": 0}
    return {"ids": [], "next_page_token": "", "estimate": 0}
google.list_ids = list_ids
google.label_id = lambda name, create=True: "L1"
google.batch_modify = lambda ids, add=None, remove=None: {"modified": len(ids)}
job = jobs.start("gmail_triage", params={"categories": ["promotions"]})
jobs.tick()
check("a daily quota pauses instead of failing", jobs.get(job["job_id"])["status"] == "waiting")
jobs._save(job["job_id"], next_run_at=jobs.now())
jobs.tick()
j = jobs.get(job["job_id"])
check("and it resumes and finishes", j["status"] == "done", j["progress"])

google.drive_page = lambda tok="", size=200, query="": {"files": [
    {"id": "a", "name": "f.pdf", "size": "10", "md5Checksum": "h", "modifiedTime": "2026-01-01T00:00:00Z"},
    {"id": "b", "name": "f.pdf", "size": "10", "md5Checksum": "h", "modifiedTime": "2026-05-01T00:00:00Z"}],
    "next_page_token": ""}
trashed = []
google.drive_trash = lambda fid: trashed.append(fid) or {}
scan = jobs.start("drive_dedupe"); jobs.tick()
purge = jobs.start("drive_purge_dupes", params={"from_job": scan["job_id"]}); jobs.tick()
check("duplicate purge keeps the newest copy", trashed == ["a"], f"trashed {trashed}")

print("\nmail errands")
drafts = {}
google.create_draft = lambda to, subject, body, thread_id=None, in_reply_to=None: \
    drafts.update(to=to, body=body) or {"draft_id": "d1", "link": "https://mail/d1"}
google.reply_draft = lambda mid, body: google.create_draft("them@x.com", "Re:", body,
                                                           thread_id="t1")
d = tools.dispatch("gmail_draft_reply", {"message_id": "m1", "body": "שלום"})
check("a reply is drafted, not sent", d.get("draft_id") == "d1" and "to" in drafts)
check("sending that draft needs approval",
      tools._needs_approval("gmail_send_draft", {"draft_id": "d1"}))

msgs = [{"id": "m1", "from": "me@me.com", "subject": "s", "date": "d", "body": "sent"}]
google.thread = lambda tid, max_chars=1500: {"thread_id": tid, "count": len(msgs),
                                             "messages": msgs}
config.OWNER_EMAIL = "me@me.com"
w = tools.dispatch("watch_thread", {"thread_id": "t1", "title": "policy"})
jobs.tick()
check("watching ignores my own messages",
      jobs.get(w["job_id"])["status"] in ("queued", "waiting"))
msgs.append({"id": "m2", "from": "them@x.com", "subject": "Re: s", "date": "d2",
             "body": "cancelled 1.4.2025"})
jobs._save(w["job_id"], next_run_at=jobs.now()); sent.clear(); jobs.tick()
check("and speaks up when they reply",
      jobs.get(w["job_id"])["status"] == "done" and any("them@x.com" in b for _, b in sent))

print("\nsorting that lasts, and tidying Drive")
made = {}
google.label_id = lambda name, create=True: f"L_{name}"
google._api = lambda method, url, **kw: (
    made.update(method=method, url=url, body=kw.get("json"), params=kw.get("params"))
    or ({"id": "flt_1"} if "filters" in url else
        {"files": [{"id": "f1", "name": "CV_2026.pdf"}]} if kw.get("params", {}).get("q")
        else {"id": "fold_1", "name": "Resumes", "parents": ["root"]}))
out = tools.dispatch("gmail_create_filter", {"query": "category:promotions",
                                             "label": "Sorted/Promotions",
                                             "skip_inbox": True})
check("a filter waits for a yes", out.get("status") == "awaiting_approval")
check("and the approval says what it will catch",
      "from now on" in out.get("summary", "").lower(), out.get("summary", ""))
approvals.decide(out["approval_id"], True)
check("approving writes a real Gmail filter",
      made.get("method") == "POST" and "settings/filters" in made.get("url", ""),
      str(made)[:120])
check("it labels and takes mail out of the inbox",
      made["body"]["action"]["addLabelIds"] == ["L_Sorted/Promotions"]
      and "INBOX" in made["body"]["action"]["removeLabelIds"])

moves = []
google._api = lambda method, url, **kw: (
    moves.append((method, url, kw.get("params"))) or
    ({"parents": ["old"], "name": "CV.pdf"} if method == "GET" else {"id": "x"}))
mv = tools.dispatch("drive_move", {"file_ids": ["a", "b"], "folder_id": "fold_1"})
check("moving files waits for a yes", mv.get("status") == "awaiting_approval")
res = approvals.decide(mv["approval_id"], True)["result"]
check("and then moves them without deleting anything",
      res["moved"] == 2 and all(m[0] in ("GET", "PATCH") for m in moves),
      str(res)[:80])
check("nothing was renamed",
      not any("name" in (m[2] or {}) for m in moves if m[0] == "PATCH"))

print("\napps over MCP")
class Strict(http.server.BaseHTTPRequestHandler):
    SESSION = "s1"
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if b.get("method") != "initialize" and \
                self.headers.get("Mcp-Session-Id") != self.SESSION:
            self.send_response(400); self.end_headers()
            self.wfile.write(b'{"error":"no session"}'); return
        if "id" not in b:
            self.send_response(202); self.end_headers(); return
        res = ({"protocolVersion": "2024-11-05", "capabilities": {}}
               if b["method"] == "initialize" else
               {"tools": [{"name": "GMAIL_FETCH_EMAILS", "description": "read",
                           "inputSchema": {"type": "object", "properties": {}}},
                          {"name": "GMAIL_SEND_EMAIL", "description": "send",
                           "inputSchema": {"type": "object", "properties": {}}}]}
               if b["method"] == "tools/list" else
               {"content": [{"type": "text", "text": "3 unread"}]})
        p = json.dumps({"jsonrpc": "2.0", "id": b["id"], "result": res}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        if b["method"] == "initialize":
            self.send_header("Mcp-Session-Id", self.SESSION)
        self.send_header("Content-Length", str(len(p))); self.end_headers()
        self.wfile.write(p)
    def log_message(self, *a): pass

srv = http.server.HTTPServer(("127.0.0.1", 8131), Strict)
threading.Thread(target=srv.serve_forever, daemon=True).start()
store.put("mcp_servers", {"hosted": {"url": "http://127.0.0.1:8131/mcp",
                                     "headers": {"x-api-key": "k"}, "trust": "gated"}})
mcp.reload()
check("a hosted server that demands a session works",
      mcp.status().get("hosted", {}).get("tools") == 2, str(mcp.status()))
check("its reads run free", not mcp.needs_approval("mcp__hosted__GMAIL_FETCH_EMAILS"))
check("its writes are gated", mcp.needs_approval("mcp__hosted__GMAIL_SEND_EMAIL"))
for inner, expect, label in (
        ("GMAIL_FETCH_EMAILS", False, "a read routed through a wrapper runs free"),
        ("GMAIL_SEND_EMAIL", True, "a write routed through a wrapper is gated"),
        ("", True, "a wrapper naming no action is gated")):
    args = {"tool_slug": inner} if inner else {}
    check(label, mcp.needs_approval("mcp__hosted__COMPOSIO_EXECUTE_TOOL", args)
          is expect)
for name, args, expect, label in (
        ("COMPOSIO_REMOTE_EXECUTE_TOOL", {"arguments": {"tool_slug": "GMAIL_FETCH_EMAILS"}},
         False, "a differently named wrapper is still recognised"),
        ("COMPOSIO_MULTI_EXECUTE_TOOL", {"tools": [{"tool_slug": "GMAIL_FETCH_EMAILS"}]},
         False, "a batch of reads runs free"),
        ("COMPOSIO_MULTI_EXECUTE_TOOL",
         {"tools": [{"tool_slug": "GMAIL_FETCH_EMAILS"}, {"tool_slug": "GMAIL_SEND_EMAIL"}]},
         True, "one write in a batch gates the whole batch"),
        ("COMPOSIO_EXECUTE_TOOL", {"params": {"tool": {"slug": "GMAIL_SEND_EMAIL"}}},
         True, "a deeply nested action is found")):
    check(label, mcp.needs_approval(f"mcp__hosted__{name}", args) is expect)
for code, expect, label in (
        ('composio.tools.execute("GMAIL_FETCH_EMAILS")', False,
         "code that reads mail runs free"),
        ('composio.tools.execute("GMAIL_SEND_EMAIL")', True,
         "code that sends mail is gated"),
        ('msgs = gmail.list_messages(query="in:inbox")', False,
         "read code without an action name still runs free"),
        ('gmail.delete_message(id)', True, "delete code is gated"),
        ('x = 1', True, "code nobody can classify stays gated")):
    check(label, mcp.needs_approval("mcp__hosted__COMPOSIO_EXECUTE_CODE",
                                    {"code": code}) is expect)
check("a nested action name is found too",
      mcp.needs_approval("mcp__hosted__COMPOSIO_EXECUTE_TOOL",
                         {"arguments": {"tool_name": "GMAIL_DELETE_MESSAGE"}}) is True)
check("a tool call returns content",
      mcp.call("mcp__hosted__GMAIL_FETCH_EMAILS", {}).get("result") == "3 unread")
srv.shutdown()

print("\ncomposio, clicks only")
import http.server as _h                                               # noqa: E402
from agent import composio                                             # noqa: E402
_STATE = {"auth": {}, "accounts": [], "n": 0}


class FakeComposio(_h.BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        p = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(p))); self.end_headers()
        self.wfile.write(p)

    def do_GET(self):
        if self.headers.get("x-api-key") != "ak_test":
            return self._send({"error": "bad key"}, 401)
        if self.path.startswith("/api/v3/auth_configs"):
            slug = self.path.split("toolkit_slug=")[-1]
            got = _STATE["auth"].get(slug)
            return self._send({"items": [{"id": got, "toolkit": {"slug": slug}}]
                               if got else []})
        if self.path.startswith("/api/v3/connected_accounts"):
            return self._send({"items": _STATE["accounts"]})
        return self._send({}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
        if self.path == "/api/v3/auth_configs":
            slug = body["toolkit"]["slug"]
            _STATE["auth"][slug] = f"ac_{slug}"
            return self._send({"auth_config": {"id": f"ac_{slug}"}})
        if self.path == "/api/v3/connected_accounts/link":
            slug = body["auth_config_id"].replace("ac_", "")
            _STATE["accounts"].append({"id": f"ca_{slug}",
                                       "toolkit": {"slug": slug}, "status": "ACTIVE"})
            return self._send({"redirect_url": f"https://consent/{slug}"})
        if self.path == "/api/v3/tool_router/session":
            _STATE["n"] += 1
            sid = f"trs_{_STATE['n']}"
            return self._send({"session_id": sid, "mcp": {
                "type": "http",
                "url": f"https://app.composio.dev/tool_router/v3/{sid}/mcp"}})
        return self._send({}, 404)

    def log_message(self, *a):
        pass


fake = _h.HTTPServer(("127.0.0.1", 8142), FakeComposio)
threading.Thread(target=fake.serve_forever, daemon=True).start()
composio.BASES = ["http://127.0.0.1:8142/api/v3"]
store.put("composio_api_key", "ak_test")
check("an app with no auth config gets one made",
      composio.auth_config_id("gmail") == "ac_gmail")
url = composio.connect_url("gmail", callback="https://me/connect")
check("connecting returns the app's own consent screen", url == "https://consent/gmail")
check("and the account then reads as connected",
      any(a["toolkit"] == "gmail" and a["status"] == "ACTIVE"
          for a in composio.accounts()))
s_ = composio.session()
check("a tool-router session yields an MCP url",
      s_["url"].endswith("/mcp") and s_["session_id"].startswith("trs_"))
_real_reload, _real_status = mcp.reload, mcp.status
mcp.reload = lambda: {}                       # no live network from the test suite
mcp.status = lambda: {"composio": {"error": "unreachable from tests", "tools": 0}}
wired = composio.wire()
mcp.reload, mcp.status = _real_reload, _real_status
srv_cfg = (store.get("mcp_servers") or {}).get("composio", {})
check("the MCP endpoint is stored with the right auth header",
      srv_cfg.get("headers") == {"x-api-key": "ak_test"}, str(srv_cfg))
check("an unreachable session is reported, not swallowed", wired.get("ok") is False)
composio.accounts()
check("connected apps are remembered without a network call",
      "gmail" in composio.connected_apps())
_status = mcp.status
mcp.status = lambda: {"composio": {"tools": 6, "error": ""}}
check("a Composio-connected app counts as available",
      config.capabilities()["gmail"] is True)
check("and the agent is told never to ask for it again",
      "never ask" in brain._context())
mcp.status = lambda: {"composio": {"tools": 0, "error": "session expired"}}
check("an expired session does not fake availability",
      config.capabilities()["gmail"] is False)
mcp.status = _status
fake.shutdown()
store.put("mcp_servers", {})
mcp.reload()

print("\nsaved settings survive a key change")
import importlib                                                       # noqa: E402
store.put("composio_api_key", "ak_live")
os.environ["VAULT_KEY"] = "a-vault-key-added-later"
importlib.reload(store)
check("a key added later does not orphan what was saved",
      store.get("composio_api_key") == "ak_live")
check("the store reports itself readable", store.health()["ok"])
store.put("after", "x")
check("new writes use the new key and old ones still read",
      store.get("after") == "x" and store.get("composio_api_key") == "ak_live")
del os.environ["VAULT_KEY"]
importlib.reload(store)

print("\nconnecting things")
import app as webapp                                                    # noqa: E402
c = webapp.app.test_client()
with c.session_transaction() as s:
    s["ok"] = True
check("connect page renders every section",
      all(k in c.get("/connect").data.decode()
          for k in ("Composio", "Google", "Telegram", "Apps (MCP)")))
check("only whitelisted keys are writable",
      c.post("/connect/key", data={"key": "SECRET_KEY", "value": "x"}).status_code == 400)
code = oauth.new_nonce("telegram-link")
store.delete("telegram_chat_id"); store.put("telegram_bot_token", "1:A")
telegram.send = lambda t, chat=None: {"ok": True}
c.post("/webhook/telegram", json={"update_id": 99, "message": {
    "chat": {"id": "555"}, "text": f"/start {code}"}})
check("a deep-link code links the chat", telegram.chat_id() == "555")
check("and the code cannot be reused", not oauth.check_nonce("telegram-link", code))
check("a forged OAuth state is refused",
      c.get("/oauth/google/callback?code=x&state=bad").status_code == 400)

print("\na browser you sign into yourself")
from agent import livebrowser                                          # noqa: E402
_avail = livebrowser.available
livebrowser.available = lambda: True
link = tools.dispatch("browser_login_link", {"site": "airbnb.com"})
check("a site with no API gets a sign-in link, not a password request",
      link.get("url", "").startswith("http") and link.get("open_first") == "airbnb.com")
from urllib.parse import urlparse, parse_qs                          # noqa: E402
token = parse_qs(urlparse(link["url"]).query)["t"][0]
check("that link is signed and purpose-scoped",
      oauth.verify(token, "browser") and not oauth.verify(token, "connect"))
check("the browser page refuses an anonymous visitor",
      webapp.app.test_client().get("/browser").status_code == 302)
check("and opens for the signed link",
      webapp.app.test_client().get(f"/browser?t={token}").status_code == 200)
check("the link opens on the site the agent named", "&u=airbnb.com" in link["url"])
page = webapp.app.test_client().get(
    f"/browser?t={token}&u=airbnb.com").data.decode()
check("and the page starts there rather than blank",
      'value="airbnb.com"' in page and "__START__" not in page)
check("the agent can see which sites are already signed in",
      "signed_in_to" in tools.dispatch("browser_sessions", {}))
livebrowser.available = _avail

print("\nattachments")
open(f"{TMP}/note.txt", "w").write("policy 13224640 cancelled")
check("a saved file is readable", "13224640" in docs.read(f"{TMP}/note.txt")["text"])
captured = {}
brain.run = lambda text, channel="web", images=None, **k: \
    captured.update(images=bool(images), text=text) or "seen"
webapp.brain.run = brain.run
telegram.download = lambda fid: (b"\xff\xd8jpeg", "p.jpg")
store.put("telegram_chat_id", "555")
c.post("/webhook/telegram", json={"update_id": 100, "message": {
    "chat": {"id": "555"}, "photo": [{"file_id": "f", "file_size": 9}],
    "caption": "what is this"}})
time.sleep(0.5)
check("a photo reaches the model as an image", captured.get("images") is True)

print("\nchat formatting")
from agent.telegram import to_html, strip_markdown
messy = "**כותרת** with <b>raw</b> & *emphasis*\n- item\n# Heading\n`code`"
html = to_html(messy)
check("no raw asterisks reach the chat", "*" not in html, html)
check("user angle brackets are escaped", "&lt;b&gt;raw&lt;/b&gt;" in html)
check("bold and bullets convert", "<b>כותרת</b>" in html and "• item" in html)
check("plain fallback strips everything", "*" not in strip_markdown(messy)
      and "#" not in strip_markdown(messy))

print("\nsecrets never go through chat")
os.environ["VAULT_KEY"] = "test-vault-key"
os.environ["VAULT_PATH"] = f"{TMP}/v.enc"
import importlib                                                       # noqa: E402
from agent import vault                                                # noqa: E402
importlib.reload(vault)
vlink = tools.dispatch("vault_link", {"for_what": "airbnb_password"})
vtoken = parse_qs(urlparse(vlink["url"]).query)["t"][0]
check("a credential request becomes a link, not a question",
      oauth.verify(vtoken, "vault"))
check("that link cannot be reused for the browser",
      not oauth.verify(vtoken, "browser"))
vc = webapp.app.test_client()
check("the vault page refuses an anonymous visitor", vc.get("/vault").status_code == 302)
page = vc.get(f"/vault?t={vtoken}&for=airbnb_password").data.decode()
check("and prefills the service the agent asked about",
      'value="airbnb"' in page and "Save login" in page)
vc.post("/vault/add", data={"name": "airbnb password", "value": "hunter2"})
check("the secret is stored under a tidy name", "AIRBNB_PASSWORD" in vault.names())
check("the page never shows the value back", "hunter2" not in vc.get("/vault").data.decode())
check("the agent can use it without seeing it",
      vault.fill("pw={{secret:AIRBNB_PASSWORD}}") == "pw=hunter2")
from agent import signin as _signin                                    # noqa: E402
vc.post("/vault/login", data={"service": "airbnb.com", "username": "me@x.com",
                              "password": "pw123"})
check("a login is stored under the names the sign-in looks for",
      _signin.credentials("airbnb.com")[:2] == ("me@x.com", "pw123"))
check("saving a login books an immediate retry",
      any("was just saved in the vault" in t["what"] for t in tasks.pending()))
check("the vault page groups logins and hides values",
      "AIRBNB" in vc.get("/vault").data.decode()
      and "pw123" not in vc.get("/vault").data.decode())
check("settings shows what it knows", b"records" in vc.get("/settings").data)
check("forgetting everything needs the word",
      vc.post("/settings/forget", data={"confirm": "no"}).status_code == 302)
check("the prompt forbids asking for secrets in chat",
      "Never ask for a password" in brain.SYSTEM)
check("and requires a follow-up on anything blocked",
      "Every blocked request gets one" in brain.SYSTEM)

print("\nsigning in on the owner's behalf")
from agent import signin                                               # noqa: E402
check("the vault name matches what a person would pick",
      signin.credentials("https://www.airbnb.com/login")[2]
      == ["AIRBNB_EMAIL", "AIRBNB_PASSWORD"])
for _n in ("AIRBNB_EMAIL", "AIRBNB_PASSWORD"):   # the vault section stored these
    vault.delete(_n)
without = signin.sign_in("airbnb.com")
check("with nothing stored it names the two secrets to ask for",
      without["need"] == ["AIRBNB_EMAIL", "AIRBNB_PASSWORD"])
check("and says to do it itself afterwards, not hand over a browser",
      "do not ask them to drive" in without["next"])
vault.put("AIRBNB_EMAIL", "me@example.com")
vault.put("AIRBNB_PASSWORD", "hunter2")

calls = {}


class FakePage:
    def __init__(self, stage):
        self.stage, self.filled = stage, {}
        self.url = "https://www.airbnb.com/login"

    def goto(self, url, **k):
        calls["goto"] = url

    def wait_for_timeout(self, ms):
        pass

    def query_selector(self, sel):
        if "password" in sel and self.stage == "no-password":
            return None
        if "password" in sel and self.stage == "done" and calls.get("submitted"):
            return None
        if "code" in sel or "one-time" in sel:
            return None if self.stage != "code" else _El(self, "code")
        if "email" in sel or "user" in sel or "tel" in sel:
            return _El(self, "user")
        if "password" in sel:
            return _El(self, "pass")
        if "button" in sel or "submit" in sel:
            return _El(self, "submit")
        return None

    def inner_text(self, sel):
        return ("Enter the verification code we sent" if self.stage == "code"
                else "Welcome back, Yuval")


class _El:
    def __init__(self, page, kind):
        self.page, self.kind = page, kind

    def is_visible(self):
        return True

    def fill(self, v):
        self.page.filled[self.kind] = v

    def click(self):
        calls["submitted"] = True


class FakeWorker:
    def __init__(self, stage):
        self._page = FakePage(stage)

    def save_state(self):
        calls["saved"] = True
        return {"saved": True}


w = FakeWorker("done")
res = signin._sign_in(w, "airbnb.com", "me@example.com", "hunter2")
check("it fills the stored credentials itself",
      w._page.filled.get("user") == "me@example.com"
      and w._page.filled.get("pass") == "hunter2")
check("it goes to the site's real login page",
      calls["goto"] == "https://www.airbnb.com/login")
check("a successful sign-in is saved for next time",
      res["ok"] and calls.get("saved"))
w2 = FakeWorker("code")
res2 = signin._sign_in(w2, "airbnb.com", "me@example.com", "hunter2")
check("a code prompt asks for the code, not the password",
      res2["stage"] == "needs a verification code"
      and "browser_enter_code" in res2["next"])
check("the prompt tries signing in before handing over a browser",
      "site_sign_in first" in brain.SYSTEM
      and "fallback, not the opener" in brain.SYSTEM)

print("\nself check")
from agent import diagnose                                             # noqa: E402
report = diagnose.run()
check("every capability is exercised",
      {"model", "memory", "read mail", "browser", "scheduler"}
      <= {c["check"] for c in report["checks"]})
check("a missing capability says why and what to do",
      all(c["fix"] for c in report["checks"] if not c["ok"]))
check("memory works even with nothing connected",
      any(c["check"] == "memory" and c["ok"] for c in report["checks"]))
check("the summary counts what works", "working" in report["summary"])
check("the agent can run it itself",
      "checks" in tools.dispatch("self_check", {}))

print("\nshape of the toolset")
bad = [t["name"] for t in tools.SCHEMAS
       if not t.get("description") or t["input_schema"].get("type") != "object"]
check("every tool is well formed", not bad, str(bad))
check("the prompt renders", len(brain.SYSTEM.format(owner="Yuval")) > 3000)
check("readiness reports the scheduler",
      "scheduler" in c.get("/api/ready").get_json()["checks"])

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("failed: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
