# Deploying to Railway

Roughly 20 minutes, most of it waiting on Google's OAuth screen. Do the steps in
order — the volume has to exist before the first real deploy or the agent starts
out amnesiac.

## 1. Get the keys you need (5 min)

| key | where |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys |
| Telegram bot token | Telegram → message **@BotFather** → `/newbot` → copy the token |
| Brave search key (optional) | brave.com/search/api — free tier is enough |

## 2. Create the Railway service

1. railway.com → **New Project** → **Deploy from GitHub repo** → `Yuval-Steimberg/yuvalbot`
2. **Settings → Source → Branch**: `claude/instinct-ai-agent-20udyy`
3. Railway will detect the `Dockerfile` and start building. Let it fail or finish,
   it does not matter yet — the variables are not set.

## 3. Add the volume — do this before you care about the logs

**Service → Settings → Volumes → New Volume**

* Mount path: `/data`
* Size: 1 GB is plenty (memory is markdown; 1 GB holds years)

This is the part that makes the agent remember. Containers are wiped on every
deploy; `/data` is not.

## 4. Set the variables

**Service → Variables → Raw Editor**, paste this and fill in the blanks:

```
ANTHROPIC_API_KEY=sk-ant-...
DASHBOARD_PASSWORD=<long password for the web UI>
SECRET_KEY=<any long random string>
OWNER_NAME=Yuval
TIMEZONE=Asia/Jerusalem
DATA_DIR=/data
TELEGRAM_BOT_TOKEN=<from BotFather>
TELEGRAM_WEBHOOK_SECRET=<any random string>
AGENT_MODEL=claude-sonnet-5
AGENT_TICK_MINUTES=5
JOB_TICK_SECONDS=60
JOB_SLICE_SECONDS=45
DAILY_BRIEF=1
AUTO_APPROVE=0
```

## 5. Get a public URL, then tell the agent about it

1. **Settings → Networking → Generate Domain** → copy it
   (e.g. `https://yuvalbot-production.up.railway.app`)
2. Nothing to copy: Railway exposes the domain as `RAILWAY_PUBLIC_DOMAIN` and the
   agent registers its own Telegram webhook from it at boot. Set `PUBLIC_URL`
   only to override that (a custom domain, or a host that is not Railway).
3. `/api/telegram` reports the webhook state and says what is missing if the bot
   is not answering; `POST /api/telegram/register` re-registers it without a
   redeploy.

Check `https://<domain>/health`. You want `"status": "ok"` and
`"persistent_storage": true`. If storage is false, the volume is not mounted at
the path `DATA_DIR` points to.

## 6. Link your Telegram chat

Message your bot anything. It will reply:

> Not linked yet. Set `TELEGRAM_CHAT_ID=123456789` in the deployment's variables…

Add that variable, redeploy, message it again. Now it is your agent and nobody
else's — a bot username is public, so this step is what keeps strangers out.

## 7. Connect Google (Gmail, Calendar, Drive, Contacts)

**From a browser, including your phone**: open `https://<domain>/connect`, or ask
the agent "how do I connect my Gmail" and tap the link it sends. The page walks
through creating a Google OAuth client (five steps in the Google console, which
cannot be skipped: Google will not issue credentials for someone else's app),
you paste the client id and secret once, then press **Connect Google** and
approve. The refresh token is stored encrypted on the volume. Google will warn
that the app is unverified — expected for your own project: **Advanced > Go to
(unsafe)**.

The redirect URI to register in the console is exactly
`https://<domain>/oauth/google/callback`.

`scripts/google_setup.py` still exists for anyone who prefers a terminal, and
`GOOGLE_*` environment variables still win over anything connected in the
browser. The old route, on your own laptop:

1. console.cloud.google.com → new project
2. **APIs & Services → Library**: enable **Gmail API**, **Google Calendar API**,
   **Google Drive API**, **People API**
3. **OAuth consent screen**: External, add your own Gmail as a test user
4. **Credentials → Create credentials → OAuth client ID → Desktop app** → copy
   the client id and secret (a Desktop client allows the `http://localhost`
   redirect the script uses; Google no longer permits the old paste-the-code flow)
5. Then:

```bash
git clone -b claude/instinct-ai-agent-20udyy https://github.com/Yuval-Steimberg/yuvalbot
cd yuvalbot && pip install requests
GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... python scripts/google_setup.py
```

It opens your browser, you approve, and it prints `GOOGLE_REFRESH_TOKEN=...`.
If Google says the app is unverified, that is expected for your own test-user
client: **Advanced → Go to (unsafe)**.
Put all three (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`)
into Railway variables and redeploy.

## 8. Optional extras

* **Web search that does not get rate limited**: `BRAVE_API_KEY=...`
* **Site logins for the browser**: set `VAULT_KEY=<long string>`, then store a
  secret from the web UI's console or locally:
  `python -c "from agent import vault; vault.put('AIRLINE_PW','...')"`.
  The agent references it as `{{secret:AIRLINE_PW}}` and never sees the value.
* **Memory backup off-box**: create a private empty GitHub repo and set
  `MEMORY_GIT_REMOTE=https://<user>:<token>@github.com/<you>/agent-memory.git`.
  Every memory commit is pushed there, so the volume is no longer a single point
  of failure.

## 9. Connect your other apps (MCP)

**The easy way**: open `/connect`, pick the app from the list (Notion, Linear,
Todoist, Slack, Airbnb search), paste its token, press Connect. It appears in the
list with a live tool count and can be removed the same way.

**The manual way**, for a server that is not in that list — get the vendor's MCP
config from their docs, merge them into one object, and put it in a single
Railway variable:

```
MCP_SERVERS={"mcpServers":{"notion":{"command":"npx","args":["-y","@notionhq/notion-mcp-server"],"env":{"NOTION_TOKEN":"ntn_..."}},"linear":{"url":"https://mcp.linear.app/mcp","headers":{"Authorization":"Bearer ..."}}}}
```

It must be valid JSON on one line. `mcp.json.example` in the repo has the shapes
for Notion, Linear, Todoist, Slack, a read-only Airbnb search server and a
banking placeholder. After redeploying, open the **status** tab: each server
shows green with a tool count, or red with the reason.

Banking: point it at a read-only account-data server only. A server that can move
money is a server that can move money when something goes wrong — and MCP tool
descriptions are third-party text the model reads. Writes stay gated no matter
what the server says about itself.

## 10. Smoke test in production

Message the bot, in this order:

1. `remember that I drink my coffee black and I hate morning meetings`
   → it should write a `preferences/...` record.
2. `what do you know about me?` → it should search memory and answer from it.
3. `in 3 minutes, message me a one-line joke` → it books a follow-up; the message
   should arrive unprompted. **This is the test that matters** — it proves the
   scheduler, the LLM loop and outbound messaging all work while you are away.
4. `email shira@example.com asking if she's free Thursday` → it should come back
   with a draft and "say yes" rather than sending. Reply `yes` and check it sent.
5. Open `https://<domain>/` → chat, memory, approvals and status tabs.

## Costs

* Railway: the container is small but always on — roughly $5/month on the Hobby
  plan, plus about $0.15/GB for the volume.
* Anthropic: the daily review and consolidation are two turns a day; the rest is
  what you actually use. Expect single-digit dollars a month unless you lean on
  it hard.

## When something is wrong

| symptom | cause |
|---|---|
| `This API key is not scoped to a workspace` | the key is an org-level key. Make a new key inside a workspace, or set `ANTHROPIC_WORKSPACE_ID` |
| bot replies "Broke on my side: ..." | that line names the failure. `/api/selftest` isolates it further: model call, tool schemas, memory write, database |
| `persistent_storage: false` | volume not mounted at `DATA_DIR` |
| bot silent | open `/api/telegram` — it names the cause and what to do. On Railway the public URL is derived from `RAILWAY_PUBLIC_DOMAIN`, so it only breaks if no domain is generated |
| "Not linked yet" every time | `TELEGRAM_CHAT_ID` not set |
| every reminder arrives twice | more than one gunicorn worker or replica |
| gmail tools error | refresh token was minted without the API enabled; redo step 7 |
| `no refresh_token returned` | you already approved this client once — revoke it at myaccount.google.com/permissions and rerun |
| browser tools unavailable | image built with `INSTALL_BROWSER=0`, or the service is on the Nixpacks fallback |
| `'$PORT' is not a valid port number` | a **Custom Start Command** is saved on the service and overrides the image. Settings > Deploy > Custom Start Command: clear it (or set it to `/app/start.sh`). Redeploying alone will not fix this — the stale command outlives every deploy |
| "The Dockerfile failed validation" | pull the latest `main` — a `VOLUME` instruction and non-ASCII comments tripped Railway's validator. If it still fails, Settings > Build > Builder > **Nixpacks**: everything works except the browser tools and npx MCP servers |
| MCP server red in status | bad package name, missing token, or it needs a runtime that is not in the image |
| `MCP_SERVERS` ignored | not valid JSON — the boot log says so |
