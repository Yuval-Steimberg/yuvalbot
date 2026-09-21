"""Tool surface exposed to the model, plus dispatch."""

import json, logging
from . import memory, tasks, channels, llm

log = logging.getLogger("yuvalbot.tools")

SCHEMAS = [
    {
        "name": "memory_search",
        "description": ("Keyword search across long-term memory. ALWAYS call this before "
                        "answering anything about the user, their people, plans or past "
                        "decisions. Returns ranked records with snippets."),
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "kind": {"type": "string", "enum": memory.KINDS,
                     "description": "optional: restrict to one memory directory"},
            "limit": {"type": "integer", "default": 8}},
            "required": ["query"]},
    },
    {
        "name": "memory_read",
        "description": "Read one full memory record by id, e.g. 'people/shira-bahar'.",
        "input_schema": {"type": "object", "properties": {
            "id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "memory_write",
        "description": ("Save a durable fact. Use the narrowest true kind. Write one "
                        "subject per record; use links to connect records. Do not store "
                        "throwaway chatter — the daily consolidation pass handles that."),
        "input_schema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": memory.KINDS},
            "title": {"type": "string"},
            "body": {"type": "string", "description": "markdown; dated, sourced, specific"},
            "aliases": {"type": "array", "items": {"type": "string"},
                        "description": "other words the user might search with"},
            "links": {"type": "array", "items": {"type": "string"},
                      "description": "ids of related records"},
            "mode": {"type": "string", "enum": ["upsert", "append"], "default": "append"}},
            "required": ["kind", "title", "body"]},
    },
    {
        "name": "memory_archive",
        "description": "Mark a record obsolete (kept in git history, demoted in search).",
        "input_schema": {"type": "object", "properties": {
            "id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["id"]},
    },
    {
        "name": "schedule_followup",
        "description": ("Schedule your own future turn. 'what' is the instruction you will "
                        "receive then — write it so it stands alone. Use for reminders, "
                        "check-ins and dropped threads."),
        "input_schema": {"type": "object", "properties": {
            "when": {"type": "string", "description": "ISO8601 UTC, or 'in 2h' / 'in 3d'"},
            "what": {"type": "string"},
            "channel": {"type": "string", "enum": ["whatsapp", "email", "silent"],
                        "default": "whatsapp"},
            "repeat_hours": {"type": "integer", "default": 0}},
            "required": ["when", "what"]},
    },
    {
        "name": "list_followups",
        "description": "List scheduled follow-ups.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_followup",
        "description": "Cancel a scheduled follow-up by id.",
        "input_schema": {"type": "object", "properties": {
            "id": {"type": "integer"}}, "required": ["id"]},
    },
    {
        "name": "send_message",
        "description": ("Message the user out of band (WhatsApp or email). Use this when "
                        "acting on a scheduled follow-up; in a live chat just reply."),
        "input_schema": {"type": "object", "properties": {
            "channel": {"type": "string", "enum": ["whatsapp", "email"]},
            "subject": {"type": "string"},
            "body": {"type": "string"}},
            "required": ["channel", "body"]},
    },
    {
        "name": "scan_jobs",
        "description": "Run the job scanner (Indeed/LinkedIn) and return new matches.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _expand(query: str) -> list[str]:
    """Grep misses paraphrases. Ask a cheap model for alternate surface forms."""
    try:
        out = llm.json_obj(
            f'Query: "{query}"\nReturn JSON {{"terms": [...]}} with up to 6 single words '
            f'or short phrases that a note about this topic would literally contain '
            f'(synonyms, brand names, categories). No explanation.',
            default={}) or {}
        return [t for t in out.get("terms", []) if isinstance(t, str)][:6]
    except Exception as e:
        log.debug(f"expansion skipped: {e}")
        return []


def dispatch(name: str, args: dict) -> dict:
    try:
        if name == "memory_search":
            q = args["query"]
            hits = memory.search(q, args.get("kind"), args.get("limit", 8))
            if len(hits) < 3:
                extra = _expand(q)
                seen = {h["id"] for h in hits}
                hits += [h for h in memory.search(q, args.get("kind"), 8, extra_terms=extra)
                         if h["id"] not in seen]
            return {"hits": hits[: args.get("limit", 8)]}

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

        if name == "schedule_followup":
            return tasks.add(args["when"], args["what"],
                             args.get("channel", "whatsapp"),
                             int(args.get("repeat_hours", 0)))

        if name == "list_followups":
            return {"tasks": tasks.pending()}

        if name == "cancel_followup":
            return tasks.cancel(int(args["id"]))

        if name == "send_message":
            return channels.send(args["channel"], args["body"],
                                 args.get("subject", "yuval.bot"))

        if name == "scan_jobs":
            from scanner import run_scan
            new, total = run_scan()
            return {"new_matches": new, "scanned": total}

        return {"error": f"unknown tool {name}"}
    except Exception as e:
        log.error(f"tool {name} failed: {e}")
        return {"error": f"{type(e).__name__}: {e}"}
