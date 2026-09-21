# agent.

A personal AI agent modelled on Instinct: one ongoing conversation over WhatsApp
(or the web UI), a memory that actually persists, and the ability to act — read
and send your mail, manage your calendar, search and drive the web — with a
human approval gate in front of anything irreversible.

```
you ──WhatsApp/web──►  brain.py  ──►  tools  ──►  Gmail · Calendar · web · browser
                          │                          │
                    memory (git+md)            approvals gate
                          │                          │
                    consolidation            you say yes ──► it happens
                          │
                    scheduler ──► it comes back to you on its own
```

## Quick start

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # required
export DASHBOARD_PASSWORD=something        # web UI
python app.py                              # http://localhost:5000
python -m agent.cli                        # or talk in the terminal
```

Everything else is optional and turns on a capability when present — the status
tab shows what is live and what is dark.

| what it unlocks | variables |
|---|---|
| WhatsApp in + out | `TWILIO_SID` `TWILIO_TOKEN` `YOUR_PHONE` (Twilio sandbox works) |
| Gmail + Calendar | `GOOGLE_CLIENT_ID` `GOOGLE_CLIENT_SECRET` `GOOGLE_REFRESH_TOKEN` → `python scripts/google_setup.py` |
| Plain email out | `RESEND_API_KEY` `EMAIL_TO` `EMAIL_FROM` |
| Good web search | `BRAVE_API_KEY` or `SERPER_API_KEY` (falls back to DuckDuckGo) |
| Real browser | `pip install playwright && playwright install chromium` |
| Stored site logins | `VAULT_KEY` (any long string) |
| Shell on its own box | `ENABLE_SHELL=1` (approval-gated) |

Point Twilio's WhatsApp sandbox webhook at `https://<your-host>/webhook/whatsapp`
and the agent is reachable from your phone. Inbound requests are rejected unless
they carry a valid `X-Twilio-Signature`.

## The four pieces

### 1. Memory — git-tracked markdown, found with keywords

One file per subject under `memory/<kind>/<slug>.md`, kinds: `people,
organizations, facts, preferences, decisions, communications, timelines,
workstreams`, plus `_inbox` for raw capture.

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
Likes fresh pasta and cheap takeout ramen.

## 2026-09-21
Correction: now avoids dairy.
```

No vector database, no embedding service, no infra: scoring is word-boundary
keyword matching (title ×5, aliases ×4, links ×2, body ×1, archived halved).
Every write is a git commit, so a corrected fact keeps its old version and
`memory_read` hands the model the record's history with its body. Grep's known
weakness — a paraphrase missing the record — is covered by query expansion that
only fires when the plain search comes back thin. You can read the entire memory
with `cat` and audit it with `git log`.

### 2. Proactivity — it books its own return visits

`schedule_followup` lets the agent write an instruction to its future self. The
scheduler runs due follow-ups every 5 minutes: the agent re-enters the loop with
nobody watching, does the work, and messages you only if the result earns the
interruption. A daily review (05:00 UTC) looks at calendar, new mail, open
workstreams and pending approvals, and stays silent when nothing is worth saying.

### 3. Consolidation — memory that ages well

Every turn is captured to `memory/_inbox`. At 03:00 UTC the consolidation pass
hands the inbox plus the most related existing records to the model, which
returns write/archive operations: merge rather than duplicate, append dated
corrections, promote open loops into `workstreams`, drop chatter. One commit.

### 4. Approvals — the brake

Sending mail to a third party, inviting people to an event, driving a form on a
website, running a shell command: the tool call returns `awaiting_approval`
instead of executing. The agent tells you what it wants to do; you approve in the
UI or just reply "yes" and it calls `decide_approval`, which executes the stored
call verbatim. Stored site credentials live in a Fernet-encrypted vault and reach
the browser as `{{secret:NAME}}` placeholders — the model never sees a value.

## Using it

```
python -m agent.cli              chat in the terminal
python -m agent.cli tick         run due follow-ups now
python -m agent.cli consolidate  run the nightly pass now
python -m agent.cli search "japan"
python -m agent.cli stats
```

Web UI tabs: **chat**, **memory** (search what it knows), **approvals**,
**status** (which integrations are live).

## Deployment

`Procfile` and `nixpacks.toml` are set up for Railway. Two things will bite you:

* **The filesystem is ephemeral.** Mount a volume at `MEMORY_DIR`, or set
  `MEMORY_GIT_REMOTE` to a private repo so every memory commit is pushed off-box.
  Without one of those, the agent forgets everything on each deploy.
* **One worker only.** The scheduler runs in-process; multiple gunicorn workers
  would run every follow-up several times.

## What this is not

Instinct's real moat is not the memory format — it is a persistent machine with
your credentials cached, driving a browser and a phone, plus a model trained for
that texture. Two parts of that are not reproduced here and it is worth knowing
which: **no phone control** (no reading your screen, placing calls or sending
texts as you), and **no autonomous spending** — checkout flows are reachable by
`browser_act` but always stop at the approval gate. Everything else — the always
on conversation, the memory, the proactive follow-ups, mail and calendar,
browsing — is here and working.
