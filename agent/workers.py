"""Job handlers. Each one advances its own state by a time-boxed slice.

A handler gets (job, params, state) and returns
    {"state": ..., "done": bool, "progress": str, "report": str?, "summary": str?}
It must be resumable from `state` alone: the process can die, the daily quota
can run out, and the next slice picks up mid-mailbox without redoing work.
"""

import time, json, logging
from . import google, llm, memory, config

log = logging.getLogger("yuvalbot.workers")

SLICE = int(__import__("os").environ.get("JOB_SLICE_SECONDS", "45"))
CATEGORIES = {"promotions": "category:promotions", "social": "category:social",
              "updates": "category:updates", "forums": "category:forums"}


def _deadline():
    return time.time() + SLICE


# ─── inbox triage: get the bulk mail out of the inbox ─────────────────────────

def gmail_triage(job, params, state):
    """Move Gmail's own bulk categories out of the inbox under real labels.

    Nothing is deleted. Messages keep their category and gain a label, they just
    stop occupying the inbox — which is what "organize my mailbox" means for the
    9,000 promotions nobody will ever read.
    """
    cats = params.get("categories") or ["promotions", "social", "updates", "forums"]
    prefix = params.get("label_prefix", "Sorted")
    state.setdefault("done_cats", [])
    state.setdefault("moved", {})
    state.setdefault("page", "")
    end = _deadline()

    for cat in cats:
        if cat in state["done_cats"]:
            continue
        label = google.label_id(f"{prefix}/{cat.capitalize()}")
        while time.time() < end:
            page = google.list_ids(f"{CATEGORIES.get(cat, cat)} in:inbox",
                                   state["page"], 500)
            ids = page["ids"]
            if not ids:
                state["done_cats"].append(cat)
                state["page"] = ""
                break
            google.batch_modify(ids, add=[label], remove=["INBOX"])
            state["moved"][cat] = state["moved"].get(cat, 0) + len(ids)
            # Messages leave the inbox, so the next query returns fresh ones:
            # paging tokens would point into a list that just changed.
            state["page"] = ""
        else:
            break

    total = sum(state["moved"].values())
    progress = ", ".join(f"{k} {v:,}" for k, v in state["moved"].items()) or "nothing yet"
    done = len(state["done_cats"]) >= len(cats)
    out = {"state": state, "done": done, "progress": progress}
    if done:
        out["summary"] = (f"Inbox sorted: {total:,} messages moved out of the inbox "
                          f"({progress}). Nothing deleted — everything is under "
                          f"'{prefix}/'.")
        memory.write("facts", "Mailbox organisation",
                     f"Inbox triaged by the agent: {progress}. Bulk mail lives under "
                     f"the '{prefix}/' labels, nothing was deleted.",
                     aliases=["gmail", "inbox", "mail", "labels", "cleanup"])
    elif total and total // 2000 > state.get("reported_at", 0):
        state["reported_at"] = total // 2000
        out["report"] = f"{total:,} messages moved out of the inbox so far ({progress})."
    return out


# ─── what am I paying for ─────────────────────────────────────────────────────

SUB_QUERIES = [
    "subject:(receipt OR invoice OR payment OR subscription OR renewal OR חשבונית OR קבלה)",
    "from:(billing OR invoice OR receipts OR no-reply) (subscription OR renew OR plan)",
]
SUB_PROMPT = """From these email headers, extract recurring paid subscriptions only.
Ignore one-off purchases, shipping notices and marketing.

Return JSON: {"subs":[{"service":"...","amount":"e.g. $9.99 or ₪23.90",
"cycle":"monthly|yearly|usage","evidence":"subject line you used"}]}
Return {"subs":[]} if none. Never invent an amount that is not in the text."""


def gmail_subscriptions(job, params, state):
    months = int(params.get("months", 12))
    state.setdefault("qi", 0)
    state.setdefault("page", "")
    state.setdefault("seen", 0)
    state.setdefault("subs", {})
    end = _deadline()

    while time.time() < end and state["qi"] < len(SUB_QUERIES):
        q = f"{SUB_QUERIES[state['qi']]} newer_than:{months * 30}d"
        page = google.list_ids(q, state["page"], 100)
        ids = page["ids"]
        state["page"] = page["next_page_token"]
        if ids:
            heads = google.headers_of(ids[:40])
            state["seen"] += len(heads)
            blob = "\n".join(f"- from {h['from']} | {h['subject']}" for h in heads)
            found = llm.json_obj(blob, system=SUB_PROMPT, default={"subs": []}) or {}
            for s in found.get("subs", []):
                key = (s.get("service") or "").strip().lower()
                if key:
                    state["subs"][key] = s
        if not state["page"]:
            state["qi"] += 1

    done = state["qi"] >= len(SUB_QUERIES)
    subs = list(state["subs"].values())
    progress = f"{state['seen']:,} billing emails read, {len(subs)} subscriptions found"
    out = {"state": state, "done": done, "progress": progress}
    if done:
        lines = [f"• {s.get('service')} — {s.get('amount', '?')} {s.get('cycle', '')}"
                 for s in sorted(subs, key=lambda x: str(x.get('service')))]
        body = "\n".join(lines) or "nothing recurring found"
        out["summary"] = f"Your active subscriptions ({len(subs)}):\n{body}"
        memory.write("facts", "Subscriptions and recurring charges", body,
                     aliases=["subscription", "subscriptions", "billing", "recurring",
                              "payments", "מנוי", "מנויים"], mode="upsert")
        out["result"] = {"subscriptions": subs}
    return out


# ─── drive duplicates ─────────────────────────────────────────────────────────

def drive_dedupe(job, params, state):
    """Page the whole Drive and group by content hash. Reports, never deletes."""
    state.setdefault("page", "")
    state.setdefault("seen", 0)
    state.setdefault("by_hash", {})
    end = _deadline()

    while time.time() < end:
        page = google.drive_page(state["page"], 200)
        for f in page["files"]:
            h = f.get("md5Checksum")
            if not h:
                continue                     # Google-native docs have no checksum
            key = f"{h}:{f.get('size', '')}"
            state["by_hash"].setdefault(key, []).append(
                {"id": f["id"], "name": f.get("name"), "size": f.get("size"),
                 "modified": f.get("modifiedTime"), "link": f.get("webViewLink")})
        state["seen"] += len(page["files"])
        state["page"] = page["next_page_token"]
        if not state["page"]:
            break

    dupes = {k: v for k, v in state["by_hash"].items() if len(v) > 1}
    wasted = sum(int(v[0].get("size") or 0) * (len(v) - 1) for v in dupes.values())
    progress = f"{state['seen']:,} files scanned, {len(dupes)} duplicate groups"
    out = {"state": state, "done": not state["page"], "progress": progress}
    if out["done"]:
        groups = [f"• {v[0]['name']} ×{len(v)}" for v in list(dupes.values())[:25]]
        out["summary"] = (f"Drive scan done: {state['seen']:,} files, {len(dupes)} "
                          f"duplicate groups, about {wasted / 1e6:.0f} MB wasted.\n"
                          + "\n".join(groups) +
                          ("\n…" if len(dupes) > 25 else "") +
                          "\n\nNothing was touched. Say the word and I will trash all "
                          "but the newest copy in each group — recoverable for 30 days.")
        out["result"] = {"duplicate_groups": len(dupes), "wasted_bytes": wasted,
                         "groups": {k: v for k, v in list(dupes.items())[:200]}}
    elif state["seen"] // 2000 > state.get("reported_at", 0):
        state["reported_at"] = state["seen"] // 2000
        out["report"] = progress
    return out


def drive_purge_dupes(job, params, state):
    """Trash all but the newest copy in each duplicate group of a finished scan."""
    from . import jobs
    src = jobs.get(int(params["from_job"]))
    if not src or src["status"] != "done":
        return {"state": state, "done": True,
                "summary": f"Job #{params.get('from_job')} has not finished a scan."}
    groups = (json.loads(src.get("result") or "{}")).get("groups", {})
    state.setdefault("keys", list(groups))
    state.setdefault("trashed", 0)
    end = _deadline()

    while state["keys"] and time.time() < end:
        key = state["keys"].pop()
        copies = sorted(groups[key], key=lambda f: f.get("modified") or "")
        for f in copies[:-1]:                    # keep the newest
            google.drive_trash(f["id"])
            state["trashed"] += 1

    done = not state["keys"]
    return {"state": state, "done": done,
            "progress": f"{state['trashed']} copies trashed",
            "summary": (f"Trashed {state['trashed']} duplicate copies, kept the newest "
                        f"of each. They sit in Drive's trash for 30 days if you want "
                        f"any back.") if done else None}


# ─── bulk purge of old bulk mail (only ever reachable through an approval) ─────

def gmail_purge(job, params, state):
    query = params.get("query") or "category:promotions older_than:1y"
    state.setdefault("trashed", 0)
    end = _deadline()
    while time.time() < end:
        page = google.list_ids(query, "", 500)
        if not page["ids"]:
            break
        google.batch_trash(page["ids"])
        state["trashed"] += len(page["ids"])
    done = state["trashed"] and not google.list_ids(query, "", 1)["ids"]
    return {"state": state, "done": bool(done),
            "progress": f"{state['trashed']:,} messages trashed",
            "summary": (f"Trashed {state['trashed']:,} messages matching "
                        f"'{query}'. Recoverable from Gmail's trash for 30 days.")
                       if done else None}


# ─── anything else: the agent working on its own over hours ───────────────────

def agent_task(job, params, state):
    """A goal the agent works at across many turns, keeping its own notes.

    Each slice is one agent turn with the notes from last time. The agent ends a
    turn with DONE: <summary> when it is finished, otherwise NOTES: <what to
    remember> and the work continues on the next slice.
    """
    from . import brain
    state.setdefault("round", 0)
    state.setdefault("notes", "")
    state["round"] += 1
    if state["round"] > int(params.get("max_rounds", 40)):
        return {"state": state, "done": True,
                "summary": f"Stopping after {state['round']} rounds on: {job['goal']}"}

    out = brain.run(
        f"[BACKGROUND JOB #{job['id']}, round {state['round']} — nobody is watching]\n"
        f"Goal: {job['goal']}\n\n"
        f"Your notes from last round:\n{state['notes'] or '(none yet)'}\n\n"
        "Do the next concrete chunk of work now, using your tools. Do not ask "
        "questions — if something needs the owner's decision, use send_message.\n"
        "End your reply with exactly one of:\n"
        "DONE: <one-line summary>   (the goal is met)\n"
        "NOTES: <what the next round needs to know, including where you stopped>",
        channel=f"job:{job.get('channel') or 'auto'}")

    if "DONE:" in out:
        return {"state": state, "done": True,
                "summary": out.split("DONE:", 1)[1].strip()[:800]}
    state["notes"] = (out.split("NOTES:", 1)[1].strip() if "NOTES:" in out else out)[:4000]
    return {"state": state, "done": False,
            "progress": f"round {state['round']}"}


HANDLERS = {
    "gmail_triage": gmail_triage,
    "gmail_subscriptions": gmail_subscriptions,
    "gmail_purge": gmail_purge,
    "drive_dedupe": drive_dedupe,
    "drive_purge_dupes": drive_purge_dupes,
    "agent_task": agent_task,
}

DESTRUCTIVE = {"gmail_purge", "drive_purge_dupes"}
