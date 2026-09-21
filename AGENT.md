# yuval.bot agent — an Instinct-style personal agent

A personal agent built on the memory architecture publicly reverse-engineered
from Instinct: **git-tracked markdown records, keyword retrieval, and a nightly
background pass that reorganizes what the agent knows.** It runs inside the
existing Flask app, so the job scanner becomes one of its tools rather than the
whole product.

## Architecture

```
agent/
  memory.py       git-backed markdown store: write / read / archive / grep search
  consolidate.py  nightly pass — digests the inbox into durable records
  brain.py        the agent loop (Anthropic tool use) + tick() for follow-ups
  tools.py        tool schemas + dispatch (memory, follow-ups, messaging, jobs)
  tasks.py        sqlite: scheduled follow-ups + conversation log
  channels.py     WhatsApp (Twilio) / email (Resend) out; Twilio signature check
  llm.py          Anthropic Messages API over requests
  cli.py          python -m agent.cli [chat|tick|consolidate|search|stats]
```

### Memory

One markdown file per record under `memory/<kind>/<slug>.md`, kinds:
`people, organizations, facts, preferences, decisions, communications,
timelines, workstreams`, plus `_inbox` for raw capture.

```markdown
---
id: preferences/dining-preferences
title: Dining preferences
type: preferences
aliases: [pasta, takeout, italian, noodles]
links: [people/shira-bahar]
created: 2026-09-21T19:06:19Z
updated: 2026-09-21T19:11:02Z
status: active
---
Yuval likes fresh pasta and cheap takeout ramen.

## 2026-09-21
Correction: now avoids dairy.
```

* **Retrieval is keyword scoring** (title ×5, aliases ×4, links ×2, body ×1,
  archived records halved) — no vector DB, no embedding service, no infra.
* **Every write is a git commit**, so a corrected fact keeps its old version and
  `memory_read` returns the record's history alongside its body.
* **Corrections append with a date**; only wholly dead records get archived.
* **Query expansion** (`tools._expand`) fixes grep's known weakness: a cheap
  model turns "Italian noodles I enjoy" into literal terms a record would
  contain, and only runs when the plain search returns thin results.

### Proactivity

`schedule_followup` lets the model book its own future turn. APScheduler calls
`brain.tick()` every 5 minutes; each due task re-enters the agent loop with the
instruction it wrote for itself, does the work unattended, and reaches the user
via `send_message` — or decides the update isn't worth sending.

### Consolidation

Every chat turn is captured into `memory/_inbox`. At 03:00 UTC
`consolidate.run()` hands the inbox plus the most related existing records to
the model, which returns write/archive ops: merge rather than duplicate, append
dated corrections, promote open loops into `workstreams`, drop chatter. The
inbox is cleared and the result committed in one commit.

## Endpoints

| route | purpose |
|---|---|
| `GET /agent` | chat UI (password-gated, same session as the dashboard) |
| `POST /api/agent/chat` | `{"message": "..."} → {"reply": "..."}` |
| `GET /api/agent/memory?q=` | memory stats + search |
| `POST /api/agent/consolidate` | run the nightly pass on demand |
| `POST /webhook/whatsapp` | Twilio inbound → agent turn → TwiML reply |

The webhook verifies `X-Twilio-Signature` (HMAC-SHA1) before doing anything;
without it, anyone with the URL could spoof `From` and drive an agent that can
send mail and write memory. Set `REQUIRE_TWILIO_SIGNATURE=0` only for local dev.

## Environment

| var | default | notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | required |
| `AGENT_MODEL` | `claude-sonnet-5` | main loop |
| `AGENT_FAST_MODEL` | `claude-haiku-4-5-20251001` | query expansion |
| `MEMORY_DIR` | `memory` | **put this on a persistent volume** |
| `MEMORY_GIT_REMOTE` | — | if set, every commit is pushed there |
| `AGENT_DB` | `agent.db` | follow-ups + turn log |
| `AGENT_TICK_MINUTES` | `5` | follow-up poll interval |
| `CONSOLIDATE_HOUR_UTC` | `3` | nightly pass |
| `TWILIO_SID` / `TWILIO_TOKEN` / `YOUR_PHONE` | — | WhatsApp in/out |
| `RESEND_API_KEY` / `EMAIL_TO` / `EMAIL_FROM` | — | email out |

## Deployment note

Railway containers have an **ephemeral filesystem**: without a mounted volume at
`MEMORY_DIR`, the memory repo is wiped on every deploy. Either mount a volume or
set `MEMORY_GIT_REMOTE` to a private repo so each commit is pushed off-box.

## What this is not

Instinct's differentiator is not the memory format — it is a persistent machine
with cached credentials driving a real browser and phone, plus a model trained
for that. This clone gives you the memory layer, the proactive scheduler and the
messaging front door. Browser automation, credential storage and device control
are deliberately absent; they are the expensive, risky half.
