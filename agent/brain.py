"""The agent loop: tool-use turns over git-backed memory."""

import logging
from . import memory, tasks, tools, llm

log = logging.getLogger("yuvalbot.brain")

SYSTEM = """You are yuval.bot, a personal agent for Yuval Steimberg.

MEMORY IS THE PRODUCT.
- Before answering anything about Yuval, his people, plans, preferences or past
  decisions, call memory_search. Never answer from assumption.
- After learning something durable, call memory_write. Pick the narrowest kind:
  people, organizations, facts, preferences, decisions, communications,
  timelines, workstreams.
- Write records a stranger could read: dated, sourced, specific. Add aliases —
  every word Yuval might later search with — because retrieval is keyword based.
- When a new fact contradicts an old one, append a dated correction to the same
  record (mode "append"); archive only when the whole record is obsolete. Git
  keeps the old version.

BE PROACTIVE, NOT CHATTY.
- Every open loop gets a schedule_followup with a concrete date. A promise with
  no follow-up scheduled is a dropped thread.
- When a scheduled follow-up fires you are talking to no one: do the work, then
  use send_message to reach Yuval, and only if you have something worth his
  attention. Silence is a valid outcome — say so and stop.

STYLE: short, direct, no filler. Say what you did and what is next. If you are
missing something you need, ask one specific question."""


def _context() -> str:
    s = memory.stats()
    pend = tasks.pending(8)
    lines = [f"Memory: {sum(v for k, v in s.items() if k != 'commits')} records "
             f"({', '.join(f'{k} {v}' for k, v in s.items() if v and k != 'commits')}), "
             f"{s['commits']} commits."]
    if pend:
        lines.append("Scheduled follow-ups: " +
                     "; ".join(f"#{t['id']} {t['due'][:16]} {t['what'][:60]}" for t in pend))
    return "\n".join(lines)


def run(user_input: str, channel: str = "web", history_turns: int = 10,
        max_steps: int = 8) -> str:
    """One agent turn. Returns the final text reply."""
    msgs = []
    for t in tasks.recent_turns(history_turns):
        if t["content"].strip():
            msgs.append({"role": "assistant" if t["role"] == "assistant" else "user",
                         "content": t["content"][:4000]})
    msgs.append({"role": "user", "content": user_input})
    tasks.log_turn("user", user_input, channel)

    system = f"{SYSTEM}\n\n<current_state>\n{_context()}\n</current_state>"
    reply_parts = []

    for step in range(max_steps):
        resp = llm.call(msgs, system=system, tools=tools.SCHEMAS)
        content = resp.get("content", [])
        msgs.append({"role": "assistant", "content": content})
        reply_parts += [b["text"] for b in content if b.get("type") == "text"]

        calls = [b for b in content if b.get("type") == "tool_use"]
        if not calls:
            break
        results = []
        for c in calls:
            log.info(f"🔧 {c['name']} {str(c.get('input'))[:120]}")
            out = tools.dispatch(c["name"], c.get("input") or {})
            results.append({"type": "tool_result", "tool_use_id": c["id"],
                            "content": str(out)[:6000]})
        msgs.append({"role": "user", "content": results})
    else:
        reply_parts.append("(stopped: hit the tool-step limit)")

    reply = "\n".join(p.strip() for p in reply_parts if p.strip()) or "(no reply)"
    tasks.log_turn("assistant", reply, channel)
    memory.capture(f"chat-{channel}", f"USER: {user_input}\n\nBOT: {reply}")
    return reply


def tick() -> list[dict]:
    """Run every due follow-up. Called by the scheduler — this is the proactivity."""
    done = []
    for t in tasks.due_now():
        log.info(f"⏰ follow-up #{t['id']}: {t['what'][:80]}")
        prompt = (f"[SCHEDULED FOLLOW-UP #{t['id']}, due {t['due']}]\n{t['what']}\n\n"
                  f"Yuval is not watching. Do the work now. If it is worth telling him, "
                  f"send_message on channel '{t['channel']}'. Otherwise say why not.")
        try:
            out = run(prompt, channel=f"followup:{t['channel']}")
        except Exception as e:
            out = f"failed: {e}"
            log.error(f"follow-up #{t['id']} failed: {e}")
        tasks.finish(t["id"], out)
        done.append({"id": t["id"], "result": out[:400]})
    return done
