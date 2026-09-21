"""The agent's tool surface: memory, follow-ups, messaging, Google, web, browser.

dispatch() is what the loop calls — it routes gated tools through approvals.
execute() is the raw call, used by dispatch and by an approval being granted.
"""

import os, json, logging, subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import memory, tasks, channels, llm, web, browser, google, vault, approvals, config

log = logging.getLogger("yuvalbot.tools")

SCHEMAS = [
    # ─── memory ──────────────────────────────────────────────────────────────
    {"name": "memory_search",
     "description": "Keyword search over long-term memory. Call this BEFORE answering "
                    "anything about the user, their people, plans, preferences or past "
                    "decisions. Never answer from assumption.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"},
         "kind": {"type": "string", "enum": memory.KINDS},
         "limit": {"type": "integer", "default": 8}}, "required": ["query"]}},
    {"name": "memory_read",
     "description": "Read one memory record in full, with its git history.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string"}},
                      "required": ["id"]}},
    {"name": "memory_write",
     "description": "Save something durable. Narrowest true kind; one subject per "
                    "record; aliases are every word the user might later search with.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": memory.KINDS},
         "title": {"type": "string"}, "body": {"type": "string"},
         "aliases": {"type": "array", "items": {"type": "string"}},
         "links": {"type": "array", "items": {"type": "string"}},
         "mode": {"type": "string", "enum": ["upsert", "append"], "default": "append"}},
         "required": ["kind", "title", "body"]}},
    {"name": "memory_archive",
     "description": "Mark a record obsolete (kept in git history).",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["id"]}},

    # ─── time and follow-ups ─────────────────────────────────────────────────
    {"name": "current_time",
     "description": "Current UTC and local time. Call before any date reasoning.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "schedule_followup",
     "description": "Book your own future turn. 'what' is the instruction you will "
                    "receive then — write it so it stands alone. Every open loop gets one.",
     "input_schema": {"type": "object", "properties": {
         "when": {"type": "string", "description": "ISO8601 UTC or 'in 2h' / 'in 3d'"},
         "what": {"type": "string"},
         "channel": {"type": "string", "enum": ["whatsapp", "email", "silent"],
                     "default": "whatsapp"},
         "repeat_hours": {"type": "integer", "default": 0}}, "required": ["when", "what"]}},
    {"name": "list_followups", "description": "List scheduled follow-ups.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "cancel_followup", "description": "Cancel a follow-up by id.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}},
                      "required": ["id"]}},

    # ─── talking to the owner ────────────────────────────────────────────────
    {"name": "send_message",
     "description": "Message the OWNER out of band (their own WhatsApp or email). Use "
                    "when acting on a scheduled follow-up; in a live chat just reply.",
     "input_schema": {"type": "object", "properties": {
         "channel": {"type": "string", "enum": ["whatsapp", "email"]},
         "subject": {"type": "string"}, "body": {"type": "string"}},
         "required": ["channel", "body"]}},

    # ─── gmail / calendar ────────────────────────────────────────────────────
    {"name": "gmail_search",
     "description": "Search the owner's Gmail (Gmail query syntax, e.g. "
                    "'from:airline newer_than:7d'). Returns bodies.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "limit": {"type": "integer", "default": 8}},
         "required": ["query"]}},
    {"name": "gmail_send",
     "description": "Send email AS the owner to a third party. Needs approval.",
     "input_schema": {"type": "object", "properties": {
         "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
         "required": ["to", "subject", "body"]}},
    {"name": "gmail_reply",
     "description": "Reply in thread to a Gmail message id. Needs approval.",
     "input_schema": {"type": "object", "properties": {
         "message_id": {"type": "string"}, "body": {"type": "string"}},
         "required": ["message_id", "body"]}},
    {"name": "gmail_archive",
     "description": "Archive a message (remove INBOX label).",
     "input_schema": {"type": "object", "properties": {"message_id": {"type": "string"}},
                      "required": ["message_id"]}},
    {"name": "calendar_events",
     "description": "List calendar events in an ISO8601 window.",
     "input_schema": {"type": "object", "properties": {
         "time_min": {"type": "string"}, "time_max": {"type": "string"},
         "limit": {"type": "integer", "default": 20}},
         "required": ["time_min", "time_max"]}},
    {"name": "calendar_create_event",
     "description": "Create a calendar event. Needs approval if it invites anyone.",
     "input_schema": {"type": "object", "properties": {
         "summary": {"type": "string"}, "start": {"type": "string"},
         "end": {"type": "string"}, "description": {"type": "string"},
         "location": {"type": "string"},
         "attendees": {"type": "array", "items": {"type": "string"}}},
         "required": ["summary", "start", "end"]}},

    # ─── the open web ────────────────────────────────────────────────────────
    {"name": "web_search", "description": "Search the web.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "limit": {"type": "integer", "default": 6}},
         "required": ["query"]}},
    {"name": "web_fetch", "description": "Read a URL as text (no JavaScript).",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}},
                      "required": ["url"]}},
    {"name": "browser_read",
     "description": "Render a page in a real browser and read it — use when web_fetch "
                    "returns nothing useful or the page needs a login you have saved.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}},
                      "required": ["url"]}},
    {"name": "browser_act",
     "description": "Drive a real browser: click, fill, press. Use {{secret:NAME}} in a "
                    "fill value to inject a stored credential — you never see it. "
                    "Needs approval.",
     "input_schema": {"type": "object", "properties": {
         "url": {"type": "string"},
         "steps": {"type": "array", "items": {"type": "object"},
                   "description": '[{"fill":"#user","value":"{{secret:AIRLINE_USER}}"},'
                                  '{"click":"button[type=submit]"},{"wait":2000}]'},
         "intent": {"type": "string", "description": "one line: what this accomplishes"}},
         "required": ["url", "steps"]}},
    {"name": "vault_list",
     "description": "Names of stored credentials you may reference as {{secret:NAME}}.",
     "input_schema": {"type": "object", "properties": {}}},

    # ─── approvals ───────────────────────────────────────────────────────────
    {"name": "list_approvals", "description": "Actions waiting on the owner's yes.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "decide_approval",
     "description": "Call this when the owner approves or rejects a pending action "
                    "(e.g. they reply 'yes, send it'). Approving executes it now.",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "integer"}, "approved": {"type": "boolean"}},
         "required": ["id", "approved"]}},

    # ─── notes / files ───────────────────────────────────────────────────────
    {"name": "file_write",
     "description": "Save a working file (draft, itinerary, export) under the files dir.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "content": {"type": "string"}},
         "required": ["name", "content"]}},
    {"name": "file_read", "description": "Read a saved working file.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
]

if os.environ.get("ENABLE_SHELL") == "1":
    SCHEMAS.append(
        {"name": "shell",
         "description": "Run a shell command on the agent's own machine. Needs approval.",
         "input_schema": {"type": "object", "properties": {
             "command": {"type": "string"}}, "required": ["command"]}})


# ─── helpers ──────────────────────────────────────────────────────────────────

def _expand(query: str) -> list[str]:
    """Grep misses paraphrases; ask a cheap model for literal surface forms."""
    try:
        out = llm.json_obj(
            f'Query: "{query}"\nReturn JSON {{"terms":[...]}} with up to 6 words or short '
            f'phrases a note on this topic would literally contain. No explanation.',
            default={}) or {}
        return [t for t in out.get("terms", []) if isinstance(t, str)][:6]
    except Exception:
        return []


def _summarize(name: str, args: dict) -> str:
    if name in ("gmail_send",):
        return f"Email {args.get('to')} — “{args.get('subject')}”\n\n{args.get('body','')[:500]}"
    if name == "gmail_reply":
        return f"Reply to message {args.get('message_id')}:\n\n{args.get('body','')[:500]}"
    if name == "calendar_create_event":
        who = ", ".join(args.get("attendees") or []) or "no guests"
        return f"Create event “{args.get('summary')}” {args.get('start')} → {args.get('end')} ({who})"
    if name == "browser_act":
        return f"{args.get('intent') or 'Browser actions'} on {args.get('url')} " \
               f"({len(args.get('steps') or [])} steps)"
    if name == "shell":
        return f"Run: {args.get('command')}"
    if name == "send_email":
        return f"Email {args.get('to')} — {args.get('subject')}"
    return f"{name} {json.dumps(args)[:300]}"


def _needs_approval(name: str, args: dict) -> bool:
    if config.AUTO_APPROVE:
        return False
    if name == "calendar_create_event":
        return bool(args.get("attendees"))      # solo blocks on your own calendar are safe
    return name in approvals.GATED


# ─── execution ────────────────────────────────────────────────────────────────

def execute(name: str, args: dict) -> dict:
    """Raw tool call, no approval gate. Used by dispatch and by granted approvals."""
    if name == "memory_search":
        q = args["query"]
        limit = args.get("limit", 8)
        hits = memory.search(q, args.get("kind"), limit)
        if len(hits) < 3:
            seen = {h["id"] for h in hits}
            hits += [h for h in memory.search(q, args.get("kind"), 8,
                                              extra_terms=_expand(q))
                     if h["id"] not in seen]
        return {"hits": hits[:limit]}

    if name == "memory_read":
        rec = memory.read(args["id"])
        if not rec:
            return {"error": f"no record '{args['id']}'"}
        return {"id": rec["id"], "meta": rec["meta"], "body": rec["body"],
                "history": memory.history(rec["id"])}

    if name == "memory_write":
        return memory.write(args["kind"], args["title"], args["body"],
                            args.get("aliases"), args.get("links"),
                            args.get("mode", "append"))

    if name == "memory_archive":
        return memory.archive(args["id"], args.get("reason", ""))

    if name == "current_time":
        utc = datetime.now(timezone.utc)
        return {"utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "weekday": utc.strftime("%A"), "timezone": config.TIMEZONE}

    if name == "schedule_followup":
        return tasks.add(args["when"], args["what"], args.get("channel", "whatsapp"),
                         int(args.get("repeat_hours", 0)))
    if name == "list_followups":
        return {"tasks": tasks.pending()}
    if name == "cancel_followup":
        return tasks.cancel(int(args["id"]))

    if name == "send_message":
        return channels.send(args["channel"], args["body"], args.get("subject", "yuval.bot"))

    if name == "gmail_search":
        return google.search(args["query"], args.get("limit", 8))
    if name == "gmail_send":
        return google.send(args["to"], args["subject"], args["body"])
    if name == "gmail_reply":
        return google.reply(args["message_id"], args["body"])
    if name == "gmail_archive":
        return google.modify(args["message_id"], remove=["INBOX"])
    if name == "calendar_events":
        return google.events(args["time_min"], args["time_max"], args.get("limit", 20))
    if name == "calendar_create_event":
        return google.create_event(args["summary"], args["start"], args["end"],
                                   args.get("description", ""), args.get("location", ""),
                                   args.get("attendees"))

    if name == "web_search":
        return web.search(args["query"], args.get("limit", 6))
    if name == "web_fetch":
        return web.fetch(args["url"])
    if name == "browser_read":
        return browser.read(args["url"])
    if name == "browser_act":
        return browser.act(args["url"], args.get("steps") or [])
    if name == "vault_list":
        return {"secrets": vault.names() if os.environ.get("VAULT_KEY") else [],
                "note": "reference as {{secret:NAME}} in browser_act fill values"}

    if name == "list_approvals":
        return {"pending": approvals.pending()}
    if name == "decide_approval":
        return approvals.decide(int(args["id"]), bool(args["approved"]))

    if name == "file_write":
        config.FILES_DIR.mkdir(parents=True, exist_ok=True)
        p = (config.FILES_DIR / Path(args["name"]).name)
        p.write_text(args["content"])
        return {"saved": str(p), "bytes": len(args["content"])}
    if name == "file_read":
        p = config.FILES_DIR / Path(args["name"]).name
        return {"content": p.read_text()[:8000]} if p.exists() else {"error": "no such file"}

    if name == "shell":
        if os.environ.get("ENABLE_SHELL") != "1":
            return {"error": "shell disabled (set ENABLE_SHELL=1)"}
        r = subprocess.run(args["command"], shell=True, capture_output=True,
                           text=True, timeout=120)
        return {"code": r.returncode, "stdout": r.stdout[:4000], "stderr": r.stderr[:2000]}

    return {"error": f"unknown tool {name}"}


def dispatch(name: str, args: dict) -> dict:
    """Tool call with the approval gate applied."""
    try:
        if _needs_approval(name, args):
            return approvals.request(name, args, _summarize(name, args))
        return execute(name, args)
    except Exception as e:
        log.error(f"tool {name} failed: {e}")
        return {"error": f"{type(e).__name__}: {e}"}
