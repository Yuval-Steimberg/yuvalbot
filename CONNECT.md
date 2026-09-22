# Connecting everything

Every step below is a browser or a phone. The only terminal command in this file
is the test suite, and you never need to run it.

Your deployment: `https://web-production-e8204.up.railway.app`

## 0. Is it ready?

Open **`/api/ready`**. It answers in one line:

```json
{"ready": true, "verdict": "Ready.", "optional_off": ["mail", "apps"]}
```

Five things must be green, and it names any that are not:

| check | what it means if red |
|---|---|
| `storage` | no volume at `/data` — memory dies on every deploy |
| `model` | `ANTHROPIC_API_KEY` missing or rejected |
| `can_reach_you` | no Telegram, WhatsApp or email — it cannot message you first |
| `scheduler` | the follow-up loop has not run — **reminders will not fire** |
| `job_runner` | background jobs will not progress |

`/api/selftest` goes deeper (model call, tool schemas, memory write, database).
`/api/telegram` explains a silent bot. `/api/followups` shows reminders and what
they did. `/api/mcp` lists connected apps.

## 1. Telegram — two taps

1. Telegram → **@BotFather** → `/newbot` → any name → username ending in `bot`
2. Open **`/connect`** → Telegram card → paste the token → **Save**
3. The card turns into a button: **Open @yourbot and link** → tap it → Telegram
   opens → press **Start**

That is it. The deep link carries a single-use code, so the chat links itself and
nobody else can drive the agent.

## 2. Google — pick one route

### Route A: a connector service (fewest steps, they hold the tokens)

1. Sign up at **composio.dev**
2. Connect **Gmail** there — their account chooser, one tap, no Google console
3. Copy your **API key** (Settings)
4. **`/connect`** → Composio card → paste the key → **Save**
5. The card lists servers to **Link**, or offers **Create MCP server**

Gives you: find mail, read threads, draft, send, calendar. Not the bulk jobs.

### Route B: self-hosted (five console taps, tokens stay on your volume)

In console.cloud.google.com, once:

1. New project
2. **APIs & Services → Library**: enable **Gmail**, **Google Calendar**,
   **Google Drive**, **People**
3. **OAuth consent screen** → External → add your own Gmail as a test user
4. **Credentials → Create credentials → OAuth client ID → Web application**
5. Authorised redirect URI, exactly:
   `https://web-production-e8204.up.railway.app/oauth/google/callback`

Then **`/connect`** → Google card → paste client id + secret → **Connect Google**
→ approve (Google calls it unverified because it is your own project:
**Advanced → Go to (unsafe)**).

Gives you everything, including the bulk jobs: inbox triage over thousands of
messages, the subscription sweep, Drive de-duplication. **Route B is the one that
does the heavy work** — batch operations are not exposed over MCP.

Doing both is fine.

## 3. Other apps — pick from a list

**`/connect`** → Apps (MCP) → choose **Notion**, **Linear**, **Todoist**,
**Slack** or **Airbnb search** → paste that app's token → **Connect**. It appears
with a live tool count and a remove button.

For anything else, paste its MCP URL into the One-tap apps card and choose the
header it wants (`Authorization: Bearer` or `x-api-key`).

## 4. Nice to have

| what | where |
|---|---|
| Search that is not rate limited | `/connect` → Brave card (brave.com/search/api) |
| Email without Gmail | `/connect` → Resend card |
| Site logins for the browser | set `VAULT_KEY` in Railway, then the agent uses `{{secret:NAME}}` |
| Memory backed up off the volume | `MEMORY_GIT_REMOTE=https://<user>:<token>@github.com/<you>/agent-memory.git` |

## 5. First conversation

In Telegram:

```
remember that I drink my coffee black and I hate morning meetings
what do you know about me?
in 2 minutes, message me a one-line joke
```

Then, once Google is connected:

```
כמה פרסומות יש לי בתיבה?
סדר לי את האינבוקס — רק פרסומות
מצא את כל המנויים שאני משלם עליהם
מצא את המייל על ביטוח החיים ותכין טיוטת תשובה
```

You can also send it a **photo** of a form or a **PDF** and ask what it says.

## 6. What it will not do

- **Voice notes** — it says so and asks for text.
- **Your phone or screen** — no reading the screen, no calls, no SMS as you.
- **Spend money on its own** — a checkout is reachable through the browser tool
  but always stops for a yes.
- **Delete anything permanently** — bulk mail goes to Trash (30 days), files go
  to Drive's trash, memory records are archived in git.

## For developers

```bash
python tests/test_all.py    # 35 checks, everything external stubbed
python -m agent.cli         # talk to it locally
python -m agent.cli tick    # run due follow-ups now
```
