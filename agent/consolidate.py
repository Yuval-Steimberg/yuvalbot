"""Daily consolidation — the background pass that makes memory age well.

Reads everything captured in _inbox since the last run, decides what deserves a
durable record, applies the writes, then clears the inbox. Mirrors the reported
Instinct behaviour: promote transient detail into workstreams, correct facts
with dated entries, compact records, keep history in git.
"""

import json, logging
from . import memory, llm

log = logging.getLogger("yuvalbot.consolidate")

SYSTEM = """You are the memory consolidation pass for a personal agent.

You receive raw captures (chat transcripts, notes) plus the existing records
they most plausibly touch. Decide what becomes durable memory.

Rules:
- Merge into an existing record whenever one fits; do not create near-duplicates.
- A changed fact is an APPEND with today's date and the correction, never a
  silent overwrite. Archive only records that are wholly obsolete.
- Drop chatter with no future value. Writing nothing is a correct answer.
- Aliases matter: list every word the user might search with later.
- Put anything with an open next step in `workstreams`.

Return ONLY JSON:
{"ops":[{"op":"write","kind":"people","title":"...","body":"...",
         "aliases":["..."],"links":["..."],"mode":"append"},
        {"op":"archive","id":"facts/old-thing","reason":"..."}],
 "note":"one line on what you did"}"""


def run(max_inbox: int = 40) -> dict:
    inbox = list(memory.all_records(memory.INBOX))[:max_inbox]
    if not inbox:
        return {"ops": 0, "note": "inbox empty"}

    raw = "\n\n---\n\n".join(f"[{r['id']}]\n{r['body'][:2500]}" for r in inbox)

    # Pull in the records this material is most likely to touch, so the pass can
    # merge instead of duplicating.
    seed = memory.search(raw[:4000], limit=12)
    existing = "\n\n".join(
        f"[{h['id']}] {h['title']}\n{(memory.read(h['id']) or {}).get('body', '')[:1200]}"
        for h in seed) or "(none yet)"

    plan = llm.json_obj(
        f"<existing_records>\n{existing}\n</existing_records>\n\n"
        f"<new_captures>\n{raw}\n</new_captures>",
        system=SYSTEM, model=llm.BIG, max_tokens=4000, default={"ops": []}) or {"ops": []}

    applied, errors = 0, []
    for op in plan.get("ops", []):
        try:
            if op.get("op") == "archive":
                memory.archive(op["id"], op.get("reason", "consolidation"))
            else:
                memory.write(op["kind"], op["title"], op["body"],
                             op.get("aliases"), op.get("links"),
                             op.get("mode", "append"))
            applied += 1
        except Exception as e:
            errors.append(f"{op.get('title') or op.get('id')}: {e}")

    for r in inbox:
        memory.path_of(r["id"]).unlink(missing_ok=True)
    memory.commit(f"memory: consolidate {len(inbox)} captures → {applied} ops")

    out = {"captures": len(inbox), "ops": applied, "errors": errors,
           "note": plan.get("note", "")}
    log.info(f"🌙 consolidation: {out}")
    return out
