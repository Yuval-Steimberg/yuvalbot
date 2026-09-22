# yuvalbot — personal agent

An Instinct-style personal agent for Yuval: one ongoing conversation over
Telegram (or the web UI), memory that persists, and real actions with a human
approval gate in front of anything irreversible. Deployed on Railway from `main`.

## Layout

```
app.py              Flask: chat UI, /api/*, Telegram + WhatsApp webhooks, the scheduler
agent/
  brain.py          the agent loop (Anthropic tool use) + tick() + daily_briefing()
  tools.py          45 tool schemas, dispatch() (approval gate) and execute() (raw)
  memory.py         git-backed markdown store: write/read/archive/keyword search
  consolidate.py    nightly pass: _inbox captures → durable records
  tasks.py          follow-ups + turn log (sqlite)
  jobs.py           durable job runner: slices, checkpoints, quota-aware resume
  workers.py        job handlers (7 kinds) — the bulk mail / Drive / watch work
  approvals.py      pending-action queue; GATED names + MCP writes
  google.py         Gmail + Calendar + Drive + Contacts over REST, no SDK
  docs.py           text out of PDF / docx / plain files
  oauth.py          browser Google connection + signed tap-to-connect links
  store.py          encrypted settings on the volume (browser-supplied secrets)
  mcp.py            MCP client (stdio + streamable HTTP), tools as mcp__<server>__<tool>
  telegram.py channels.py web.py browser.py vault.py llm.py config.py cli.py
scripts/google_setup.py   mints GOOGLE_REFRESH_TOKEN (loopback OAuth)
DEPLOY.md           the Railway walkthrough — keep it in step with reality
```

## Rules this codebase holds itself to

* **Nothing irreversible without a human yes.** `approvals.GATED` + destructive job
  kinds (`workers.DESTRUCTIVE`) + every MCP write, which `AUTO_APPROVE=1` cannot
  lift. Bulk mail is trashed (recoverable 30 days), never hard-deleted. Outbound
  mail is drafted first and sent only on approval.
* **State lives under `config.DATA_DIR`** (the Railway volume at `/data`): memory,
  `agent.db`, the vault, saved browser logins, downloaded files. Anything written
  elsewhere is lost on redeploy. `/health` reports `persistent_storage`.
* **One gunicorn worker, one replica.** The scheduler is in-process; two workers
  fire every follow-up and job slice twice.
* **Webhooks answer immediately** and do the work out of band (Telegram retries
  after ~60s, Twilio after 15s), deduped by update/message id.
* **Memory is keyword-searched markdown, not vectors.** Records carry aliases
  because retrieval is literal; corrections are dated appends, never overwrites;
  every write is a git commit.
* **Long work is a job, not a turn.** More than ~100 items or a couple of minutes
  → `job_start`. Handlers must be resumable from `state` alone, because Google's
  daily quota will stop them mid-run.
* **MCP servers are untrusted.** Their tool descriptions are third-party text; the
  system prompt says so, and writes stay gated.

## Job kinds (`workers.HANDLERS`)

`thread_watch` · `gmail_triage` · `gmail_subscriptions` · `gmail_purge`* ·
`drive_dedupe` · `drive_purge_dupes`* · `agent_task`   (* = approval-gated)

## Conventions

* **The start command lives in the Dockerfile's `CMD` (`/app/start.sh`) and
  nowhere else.** Railway runs a start command without a shell, so an inline
  `$PORT` reaches gunicorn as a literal string. Worse, a `startCommand` in
  `railway.json` is saved onto the service and keeps overriding the image after
  the repo changes — so `railway.json` deliberately defines none, and a stale one
  has to be cleared by hand in Settings > Deploy.
* **The Dockerfile stays plain ASCII and declares no `VOLUME`** — Railway manages
  the volume and rejects Dockerfiles that declare one. `nixpacks.toml` is the
  fallback builder (no Chromium, no npx MCP servers).
* No new dependencies unless unavoidable — Google, Telegram and MCP are all plain
  `requests`. Current deps: flask, gunicorn, apscheduler, requests, cryptography,
  playwright (optional), pypdf.
* **Due dates are compared as strings in sqlite**, so anything that is not an ISO
  UTC timestamp never comes due. `tasks._parse_due` raises `BadDueDate` rather
  than storing something that would silently never fire.
* **Anything connectable is connectable from `/connect`** — Google OAuth,
  Telegram (a `t.me` deep link carrying a single-use code), API keys, and MCP
  apps from a catalog. A terminal is never the only route.
* **Credentials resolve environment-first, then the encrypted store**, so a
  browser connection never silently overrides what an operator set explicitly.
* Every capability degrades honestly: missing credentials disable a tool and are
  reported in `config.capabilities()`, never crash a turn.
* Prompt changes live in `brain.SYSTEM`. It is the product as much as the code is.
* Test handlers and tools by stubbing `agent.google` / `agent.llm.call`; there are
  no live credentials in dev.

## State of play (2026-09-22)

Verified by stub: the agent loop, approval gate end to end, MCP round trip,
job runner under a simulated daily-quota interruption, drive dedupe keeping the
newest copy, thread watching, draft→approve→send, PDF reading.

Boot itself is now verified for real: `start.sh` -> gunicorn -> `/health`
returning ok. **Still never run against live credentials.** First real use
should be a narrow `gmail_triage` (`categories: ["promotions"]`) before anything
touches the whole mailbox.

Known rough edges: `gmail_search` may need Hebrew *and* English query variants to
find old threads; no OCR, so scanned PDFs report themselves as scans; no phone,
screen or location access, by design.
