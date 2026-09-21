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
| Telegram in + out (recommended) | `TELEGRAM_BOT_TOKEN` `TELEGRAM_CHAT_ID` `PUBLIC_URL` |
| WhatsApp in + out | `TWILIO_SID` `TWILIO_TOKEN` `YOUR_PHONE` (sandbox expires every 3 days) |
| Gmail, Calendar, Drive, Contacts | `GOOGLE_CLIENT_ID` `GOOGLE_CLIENT_SECRET` `GOOGLE_REFRESH_TOKEN` → `python scripts/google_setup.py` |
| Plain email out | `RESEND_API_KEY` `EMAIL_TO` `EMAIL_FROM` |
| Good web search | `BRAVE_API_KEY` or `SERPER_API_KEY` (falls back to DuckDuckGo) |
| Real browser | `pip install playwright && playwright install chromium` |
| Stored site logins | `VAULT_KEY` (any long string) |
| Shell on its own box | `ENABLE_SHELL=1` (approval-gated) |

Set `PUBLIC_URL` and the agent registers its own Telegram webhook at boot; message
the bot once and it tells you the `TELEGRAM_CHAT_ID` to pin it to you. Telegram is
the better front door for a proactive agent: no 24-hour reply window, no message
templates, no sandbox to re-join every three days. WhatsApp still works — point
Twilio at `/webhook/whatsapp`; inbound requests without a valid
`X-Twilio-Signature` are rejected, as are Telegram updates with the wrong secret
token or from any chat that is not yours.

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

**[DEPLOY.md](DEPLOY.md) is the step-by-step for Railway.** In short: the
`Dockerfile` (Chromium included) and `railway.json` are ready, you mount a volume
at `/data`, set `DATA_DIR=/data`, paste the variables from `.env.example`, and
generate a domain. Two things that bite people:

* **All state lives under `DATA_DIR`** — memory, the task DB, the vault, saved
  browser logins. Without a volume the container is wiped on every deploy and the
  agent starts each week a stranger. `/health` reports
  `persistent_storage: false` when that is the case, and the logs say so at boot.
  Belt and braces: set `MEMORY_GIT_REMOTE` and every memory commit is pushed to a
  private repo too.
* **One worker, one replica.** The scheduler runs in-process; two workers means
  every follow-up fires twice.

Inbound webhooks are acknowledged immediately and answered out of band, because
Telegram retries anything slower than ~60s and Twilio anything slower than 15s —
otherwise a long turn would run two or three times.

## What this is not

Instinct's real moat is not the memory format — it is a persistent machine with
your credentials cached, driving a browser and a phone, plus a model trained for
that texture. Two parts of that are not reproduced here and it is worth knowing
which: **no phone control** (no reading your screen, placing calls or sending
texts as you), and **no autonomous spending** — checkout flows are reachable by
`browser_act` but always stop at the approval gate. Everything else — the always
on conversation, the memory, the proactive follow-ups, mail and calendar,
browsing — is here and working.

Connected: Telegram, WhatsApp, Gmail, Google Calendar, Google Drive, Google
Contacts, Resend email, Brave/Serper/DuckDuckGo search, a real Chromium, and a
shell on its own box. Not connected: iMessage and SMS as you, Slack, Notion,
banking or payment rails, your screen, your location.
