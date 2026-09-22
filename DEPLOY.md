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
2. Add one more variable: `PUBLIC_URL=https://<that domain>`
3. Redeploy. At boot the agent registers its own Telegram webhook — you do not
   need to call the Telegram API by hand.

Check `https://<domain>/health`. You want `"status": "ok"` and
`"persistent_storage": true`. If storage is false, the volume is not mounted at
the path `DATA_DIR` points to.

## 6. Link your Telegram chat

Message your bot anything. It will reply:

> Not linked yet. Set `TELEGRAM_CHAT_ID=123456789` in the deployment's variables…

Add that variable, redeploy, message it again. Now it is your agent and nobody
else's — a bot username is public, so this step is what keeps strangers out.

## 7. Connect Google (Gmail, Calendar, Drive, Contacts)

On your own laptop, not on Railway:

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

Get each vendor's MCP config from their docs, merge them into one object, and put
it in a single Railway variable:

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
| `persistent_storage: false` | volume not mounted at `DATA_DIR` |
| bot silent, logs show nothing | `PUBLIC_URL` missing or wrong → webhook never registered |
| "Not linked yet" every time | `TELEGRAM_CHAT_ID` not set |
| every reminder arrives twice | more than one gunicorn worker or replica |
| gmail tools error | refresh token was minted without the API enabled; redo step 7 |
| `no refresh_token returned` | you already approved this client once — revoke it at myaccount.google.com/permissions and rerun |
| browser tools unavailable | image built with `INSTALL_BROWSER=0`, or the service is on the Nixpacks fallback |
| `'$PORT' is not a valid port number` | old commit — the start command must be `/app/start.sh`, which expands PORT itself; Railway runs the start command without a shell |
| "The Dockerfile failed validation" | pull the latest `main` — a `VOLUME` instruction and non-ASCII comments tripped Railway's validator. If it still fails, Settings > Build > Builder > **Nixpacks**: everything works except the browser tools and npx MCP servers |
| MCP server red in status | bad package name, missing token, or it needs a runtime that is not in the image |
| `MCP_SERVERS` ignored | not valid JSON — the boot log says so |
