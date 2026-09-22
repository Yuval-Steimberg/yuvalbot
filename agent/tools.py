"""The agent's tool surface: memory, follow-ups, messaging, Google, web, browser.

dispatch() is what the loop calls — it routes gated tools through approvals.
execute() is the raw call, used by dispatch and by an approval being granted.
"""

import os, json, logging, subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import (memory, tasks, channels, llm, web, browser, google, vault,
               approvals, config, mcp, jobs, workers, docs)

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
         "channel": {"type": "string", "enum": ["auto", "telegram", "whatsapp",
                                                "email", "silent"], "default": "auto"},
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
         "channel": {"type": "string", "enum": ["auto", "telegram", "whatsapp", "email"],
                     "default": "auto"},
         "subject": {"type": "string"}, "body": {"type": "string"}},
         "required": ["body"]}},

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
    {"name": "gmail_thread",
     "description": "Read an entire mail thread — every message, dates, and what "
                    "attachments each carries. Use this before answering anything "
                    "about an ongoing matter: the gap in a thread (no confirmation, "
                    "no reply) is usually the thing that matters.",
     "input_schema": {"type": "object", "properties": {
         "thread_id": {"type": "string"}}, "required": ["thread_id"]}},
    {"name": "gmail_draft",
     "description": "Write an email and SAVE IT AS A DRAFT without sending. This is "
                    "the default way to write mail for the owner: returns a Gmail "
                    "link they can open, edit and send themselves.",
     "input_schema": {"type": "object", "properties": {
         "to": {"type": "string"}, "subject": {"type": "string"},
         "body": {"type": "string"}}, "required": ["to", "subject", "body"]}},
    {"name": "gmail_draft_reply",
     "description": "Draft a reply inside the existing thread of a message, so the "
                    "other side already has all the earlier context. Preferred over "
                    "starting a new email about an ongoing matter.",
     "input_schema": {"type": "object", "properties": {
         "message_id": {"type": "string"}, "body": {"type": "string"}},
         "required": ["message_id", "body"]}},
    {"name": "gmail_send_draft",
     "description": "Send a draft the owner has approved. Needs approval.",
     "input_schema": {"type": "object", "properties": {
         "draft_id": {"type": "string"}}, "required": ["draft_id"]}},
    {"name": "gmail_drafts", "description": "List saved drafts with their links.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "default": 10}}}},
    {"name": "gmail_attachments",
     "description": "What files are attached to a message.",
     "input_schema": {"type": "object", "properties": {
         "message_id": {"type": "string"}}, "required": ["message_id"]}},
    {"name": "gmail_save_attachment",
     "description": "Download an attachment so you can read it with read_document.",
     "input_schema": {"type": "object", "properties": {
         "message_id": {"type": "string"}, "attachment_id": {"type": "string"},
         "filename": {"type": "string"}},
         "required": ["message_id", "attachment_id", "filename"]}},
    {"name": "read_document",
     "description": "Extract the text of a saved PDF, docx or text file — policy "
                    "documents, forms, statements. Say so plainly if it is a scan "
                    "with no text layer.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "file name in the agent's files dir, "
                                                   "or a full path"}},
         "required": ["name"]}},
    {"name": "watch_thread",
     "description": "Watch a mail thread and tell the owner the moment the other "
                    "side replies. Use whenever you send or draft something that "
                    "needs an answer — that is how a request stops being forgotten.",
     "input_schema": {"type": "object", "properties": {
         "thread_id": {"type": "string"},
         "title": {"type": "string", "description": "what this is about, for the report"},
         "check_minutes": {"type": "integer", "default": 20},
         "max_days": {"type": "integer", "default": 14}},
         "required": ["thread_id"]}},
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

    {"name": "drive_search",
     "description": "Search the owner's Google Drive by full-text content.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "limit": {"type": "integer", "default": 10}},
         "required": ["query"]}},
    {"name": "drive_read",
     "description": "Read a Drive file's text (Docs/Sheets/Slides are exported).",
     "input_schema": {"type": "object", "properties": {"file_id": {"type": "string"}},
                      "required": ["file_id"]}},
    {"name": "contacts_search",
     "description": "Look up one of the owner's Google Contacts — email, phone, org.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "limit": {"type": "integer", "default": 8}},
         "required": ["query"]}},

    # ─── long-running work ───────────────────────────────────────────────────
    {"name": "job_start",
     "description": "Start a background job for work too big for one reply — "
                    "thousands of emails, a whole Drive, anything that takes hours. "
                    "It survives quota limits and resumes by itself, and reports "
                    "progress to the owner. Kinds: gmail_triage (move bulk mail out "
                    "of the inbox under labels), gmail_subscriptions (find recurring "
                    "charges), gmail_purge (trash mail matching a query — needs "
                    "approval), drive_dedupe (find duplicate files), "
                    "drive_purge_dupes (trash all but the newest of each duplicate "
                    "group from a finished drive_dedupe — needs approval), "
                    "agent_task (any other multi-hour goal, in your own words).",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": list(workers.HANDLERS)},
         "goal": {"type": "string", "description": "required for agent_task"},
         "params": {"type": "object",
                    "description": "gmail_triage: {categories, label_prefix}; "
                                   "gmail_subscriptions: {months}; gmail_purge: "
                                   "{query}; drive_purge_dupes: {from_job}"}},
         "required": ["kind"]}},
    {"name": "job_status", "description": "Progress of one background job.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}},
                      "required": ["id"]}},
    {"name": "job_list", "description": "Background jobs and their progress.",
     "input_schema": {"type": "object", "properties": {
         "active_only": {"type": "boolean", "default": True}}}},
    {"name": "job_cancel", "description": "Stop a background job.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}},
                      "required": ["id"]}},

    # ─── bulk mail ───────────────────────────────────────────────────────────
    {"name": "gmail_count",
     "description": "How many messages match a Gmail query (an estimate, one call). "
                    "Use this before proposing bulk work so you quote real numbers.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"]}},
    {"name": "gmail_labels", "description": "List the mailbox's labels.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "gmail_bulk_label",
     "description": "Apply/remove a label across every message matching a query "
                    "(reversible; use 'remove_inbox' to file mail out of the inbox). "
                    "For more than a few thousand, start a gmail_triage job instead.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "label": {"type": "string"},
         "remove_inbox": {"type": "boolean", "default": False},
         "max": {"type": "integer", "default": 500}},
         "required": ["query", "label"]}},
    {"name": "gmail_bulk_trash",
     "description": "Move every message matching a query to Trash (recoverable for "
                    "30 days). Needs approval. Say the count first.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}, "max": {"type": "integer", "default": 500}},
         "required": ["query"]}},

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
    {"name": "mcp_status",
     "description": "Which connected apps (MCP servers) are live and how many "
                    "tools each exposes. Use when an mcp__ tool errors.",
     "input_schema": {"type": "object", "properties": {}}},
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


def all_schemas() -> list[dict]:
    """Native tools plus whatever the configured MCP servers expose right now."""
    try:
        return SCHEMAS + mcp.schemas()
    except Exception as e:
        log.error(f"mcp schema load failed: {e}")
        return SCHEMAS


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
    if mcp.is_mcp(name):
        server, tool = mcp.split(name)
        return f"[{server}] {tool} — {json.dumps(args)[:400]}"
    if name in ("gmail_send",):
        return f"Email {args.get('to')} — “{args.get('subject')}”\n\n{args.get('body','')[:500]}"
    if name == "gmail_reply":
        return f"Reply to message {args.get('message_id')}:\n\n{args.get('body','')[:500]}"
    if name == "gmail_send_draft":
        try:
            d = next((x for x in google.list_drafts(20)["drafts"]
                      if x["draft_id"] == args.get("draft_id")), None)
        except Exception:
            d = None
        return (f"Send the draft to {d['to']} — “{d['subject']}”\n{d['snippet']}"
                if d else f"Send draft {args.get('draft_id')}")
    if name == "calendar_create_event":
        who = ", ".join(args.get("attendees") or []) or "no guests"
        return f"Create event “{args.get('summary')}” {args.get('start')} → {args.get('end')} ({who})"
    if name == "browser_act":
        return f"{args.get('intent') or 'Browser actions'} on {args.get('url')} " \
               f"({len(args.get('steps') or [])} steps)"
    if name == "shell":
        return f"Run: {args.get('command')}"
    if name == "gmail_bulk_trash":
        return f"Move every message matching '{args.get('query')}' to Trash " \
               f"(up to {args.get('max', 500)}, recoverable for 30 days)"
    if name == "job_start":
        return f"Start a {args.get('kind')} job — {args.get('goal') or args.get('params')}"
    if name == "send_email":
        return f"Email {args.get('to')} — {args.get('subject')}"
    return f"{name} {json.dumps(args)[:300]}"


def _needs_approval(name: str, args: dict) -> bool:
    if mcp.is_mcp(name):
        # An MCP write is gated even under AUTO_APPROVE: these are third-party
        # servers whose tool text the model reads, and money moves through some
        # of them.
        return mcp.needs_approval(name)
    if config.AUTO_APPROVE:
        return False
    if name == "calendar_create_event":
        return bool(args.get("attendees"))      # solo blocks on your own calendar are safe
    if name == "job_start":
        return args.get("kind") in workers.DESTRUCTIVE
    return name in approvals.GATED


# ─── execution ────────────────────────────────────────────────────────────────

def execute(name: str, args: dict) -> dict:
    """Raw tool call, no approval gate. Used by dispatch and by granted approvals."""
    if mcp.is_mcp(name):
        return mcp.call(name, args)

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
        try:
            return tasks.add(args["when"], args["what"], args.get("channel", "auto"),
                             int(args.get("repeat_hours", 0)))
        except tasks.BadDueDate as e:
            return {"error": str(e), "retry": "call schedule_followup again with a "
                                              "time in one of those forms"}
    if name == "list_followups":
        return {"tasks": tasks.pending()}
    if name == "cancel_followup":
        return tasks.cancel(int(args["id"]))

    if name == "send_message":
        return channels.send(args.get("channel", "auto"), args["body"],
                             args.get("subject", "your agent"))

    if name == "gmail_search":
        return google.search(args["query"], args.get("limit", 8))
    if name == "gmail_send":
        return google.send(args["to"], args["subject"], args["body"])
    if name == "gmail_reply":
        return google.reply(args["message_id"], args["body"])
    if name == "gmail_thread":
        return google.thread(args["thread_id"])
    if name == "gmail_draft":
        return google.create_draft(args["to"], args["subject"], args["body"])
    if name == "gmail_draft_reply":
        return google.reply_draft(args["message_id"], args["body"])
    if name == "gmail_send_draft":
        return google.send_draft(args["draft_id"])
    if name == "gmail_drafts":
        return google.list_drafts(args.get("limit", 10))
    if name == "gmail_attachments":
        return google.attachments(args["message_id"])
    if name == "gmail_save_attachment":
        return google.download_attachment(args["message_id"], args["attachment_id"],
                                          args["filename"], config.FILES_DIR)
    if name == "read_document":
        n = args["name"]
        path = n if "/" in n else str(config.FILES_DIR / n)
        return docs.read(path)
    if name == "watch_thread":
        return jobs.start("thread_watch", params={
            "thread_id": args["thread_id"], "title": args.get("title", ""),
            "check_minutes": args.get("check_minutes", 20),
            "max_days": args.get("max_days", 14)})

    if name == "gmail_archive":
        return google.modify(args["message_id"], remove=["INBOX"])
    if name == "calendar_events":
        return google.events(args["time_min"], args["time_max"], args.get("limit", 20))
    if name == "calendar_create_event":
        return google.create_event(args["summary"], args["start"], args["end"],
                                   args.get("description", ""), args.get("location", ""),
                                   args.get("attendees"))

    if name == "job_start":
        return jobs.start(args["kind"], args.get("goal", ""), args.get("params") or {},
                          args.get("channel", "auto"))
    if name == "job_status":
        j = jobs.get(int(args["id"]))
        return j or {"error": f"no job #{args['id']}"}
    if name == "job_list":
        return {"jobs": jobs.listing(active_only=args.get("active_only", True))}
    if name == "job_cancel":
        return jobs.cancel(int(args["id"]))

    if name == "gmail_count":
        return {"query": args["query"],
                "approx_count": google.list_ids(args["query"], "", 1)["estimate"]}
    if name == "gmail_labels":
        return google.labels()
    if name == "gmail_bulk_label":
        ids = google.list_ids(args["query"], "", min(args.get("max", 500), 500))["ids"]
        if not ids:
            return {"modified": 0, "note": "nothing matched"}
        return google.batch_modify(ids, add=[google.label_id(args["label"])],
                                   remove=["INBOX"] if args.get("remove_inbox") else [])
    if name == "gmail_bulk_trash":
        ids = google.list_ids(args["query"], "", min(args.get("max", 500), 500))["ids"]
        return google.batch_trash(ids) if ids else {"modified": 0, "note": "nothing matched"}

    if name == "drive_search":
        return google.drive_search(args["query"], args.get("limit", 10))
    if name == "drive_read":
        return google.drive_read(args["file_id"])
    if name == "contacts_search":
        return google.contacts_search(args["query"], args.get("limit", 8))

    if name == "web_search":
        return web.search(args["query"], args.get("limit", 6))
    if name == "web_fetch":
        return web.fetch(args["url"])
    if name == "browser_read":
        return browser.read(args["url"])
    if name == "browser_act":
        return browser.act(args["url"], args.get("steps") or [])
    if name == "mcp_status":
        return mcp.status()
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
