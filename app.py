"""
Personal agent — web front door, WhatsApp front door, and the clock that makes
it proactive. All the thinking lives in agent/.
"""

import os, logging, threading
from collections import deque
from functools import wraps

from flask import Flask, jsonify, request, session, redirect
from apscheduler.schedulers.background import BackgroundScheduler

import agent
from agent import (brain, consolidate, memory, tasks, approvals, config, vault,
                   telegram)
from agent.channels import verify_twilio

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-8s %(message)s",
                    datefmt="%H:%M:%S", handlers=[logging.StreamHandler()])
log = logging.getLogger("yuvalbot")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me")

agent.boot()

_ok, _why = config.storage_ok()
(log.info if _ok else log.error)(f"💾 {_why}")
log.info(f"🧠 agent ready — {config.missing_summary()}")

if telegram.configured() and config.PUBLIC_URL:
    telegram.set_webhook(config.PUBLIC_URL)


# ─── auth ─────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if not session.get("ok"):
            return redirect("/login")
        return f(*a, **kw)
    return dec


@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        if request.form.get("password") == os.environ.get("DASHBOARD_PASSWORD", "changeme"):
            session["ok"] = True
            session.permanent = True
            return redirect("/")
        err = "Wrong password"
    return f"""<!DOCTYPE html><html><head><title>agent</title>
<meta name=viewport content="width=device-width,initial-scale=1"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0f;display:flex;align-items:center;justify-content:center;
min-height:100vh;font:14px ui-monospace,monospace;color:#ddd}}
.box{{background:#0d0d1a;border:1px solid #1a1a2e;border-radius:12px;padding:40px;width:330px}}
h1{{font-size:20px;color:#fff;margin-bottom:24px}}h1 span{{color:#00ff88}}
input{{width:100%;background:#060608;border:1px solid #1a1a2e;color:#fff;padding:11px;
border-radius:5px;font:inherit;margin-bottom:12px}}
button{{width:100%;background:#00ff88;color:#000;border:0;padding:12px;border-radius:5px;
font:inherit;font-weight:700;cursor:pointer}}.e{{color:#f55;font-size:12px;margin-top:10px}}
</style></head><body><div class=box><h1>agent<span>.</span></h1>
<form method=POST><input type=password name=password placeholder=Password autofocus>
<button>Enter</button><div class=e>{err}</div></form></div></body></html>"""


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ─── UI ───────────────────────────────────────────────────────────────────────

UI = """<!DOCTYPE html><html><head><title>agent</title>
<meta name=viewport content="width=device-width,initial-scale=1"><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0a0f;color:#dde;font:14px/1.65 ui-monospace,SFMono-Regular,monospace;
display:flex;flex-direction:column;height:100vh}
header{display:flex;gap:18px;align-items:center;padding:12px 18px;border-bottom:1px solid #1a1a2e}
header b{color:#00ff88}header a{color:#556;text-decoration:none;cursor:pointer}
header a.on{color:#fff}header .sp{flex:1}header small{color:#445}
main{flex:1;overflow-y:auto;padding:18px}
.pane{display:none;flex-direction:column;gap:14px}.pane.on{display:flex}
.m{max-width:820px;white-space:pre-wrap;word-wrap:break-word}
.u{color:#fff;border-left:3px solid #00ff88;padding-left:12px}
.a{color:#9aa;border-left:3px solid #1a1a2e;padding-left:12px}
form{display:flex;gap:8px;padding:14px;border-top:1px solid #1a1a2e}
input{flex:1;background:#060608;border:1px solid #1a1a2e;color:#fff;padding:12px;
border-radius:6px;font:inherit}
button{background:#00ff88;color:#000;border:0;padding:0 20px;border-radius:6px;
font:inherit;font-weight:700;cursor:pointer}
.card{border:1px solid #1a1a2e;border-radius:8px;padding:14px;background:#0d0d16}
.card h4{color:#fff;font-size:13px;margin-bottom:6px}
.card .meta{color:#556;font-size:11px}
.row{display:flex;gap:8px;margin-top:10px}
.row button{padding:7px 14px;font-size:12px}
.no{background:#2a1a1e;color:#f77}
</style></head><body>
<header><b>agent.</b>
<a id=t-chat class=on onclick="tab('chat')">chat</a>
<a id=t-mem onclick="tab('mem')">memory</a>
<a id=t-appr onclick="tab('appr')">approvals <i id=badge></i></a>
<a id=t-cap onclick="tab('cap')">status</a>
<span class=sp></span><small id=hint></small><a href=/logout>exit</a></header>
<main>
 <div id=p-chat class="pane on"></div>
 <div id=p-mem class=pane></div>
 <div id=p-appr class=pane></div>
 <div id=p-cap class=pane></div>
</main>
<form id=f><input id=i placeholder="Talk to your agent…" autocomplete=off autofocus>
<button>Send</button></form>
<script>
let cur='chat';
function tab(n){cur=n;for(const x of ['chat','mem','appr','cap']){
 document.getElementById('p-'+x).className='pane'+(x==n?' on':'');
 document.getElementById('t-'+x).className=(x==n?'on':'')}
 document.getElementById('f').style.display=n=='chat'?'flex':'none';
 if(n=='mem')loadMem();if(n=='appr')loadAppr();if(n=='cap')loadCap()}
function add(cls,txt){const d=document.createElement('div');d.className='m '+cls;
 d.textContent=txt;const p=document.getElementById('p-chat');p.appendChild(d);
 p.scrollTop=p.scrollHeight;document.querySelector('main').scrollTop=1e9;return d}
document.getElementById('f').onsubmit=async e=>{e.preventDefault();
 const i=document.getElementById('i'),t=i.value.trim();if(!t)return;i.value='';add('u',t);
 const p=add('a','…');
 try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({message:t})});const j=await r.json();
  p.textContent=j.reply||j.error||'(no reply)'}catch(e){p.textContent='error: '+e}
 poll()}
async function loadMem(){const q=prompt?null:null;const r=await fetch('/api/memory?q=');
 const j=await r.json();const p=document.getElementById('p-mem');
 p.innerHTML='<div class=card><h4>memory</h4><div class=meta>'+
  Object.entries(j.stats).map(([k,v])=>k+' '+v).join(' · ')+'</div></div>'+
  '<input id=mq placeholder="search memory…" style="padding:11px;background:#060608;'+
  'border:1px solid #1a1a2e;color:#fff;border-radius:6px">'+'<div id=mres></div>';
 document.getElementById('mq').onkeydown=async ev=>{if(ev.key!='Enter')return;
  const r=await fetch('/api/memory?q='+encodeURIComponent(ev.target.value));const j=await r.json();
  document.getElementById('mres').innerHTML=j.hits.length?j.hits.map(h=>
   '<div class=card><h4>'+h.title+'</h4><div class=meta>'+h.id+' · score '+h.score+
   ' · '+h.updated+'</div><div style="margin-top:8px;color:#9aa">'+
   h.snippet.replace(/</g,'&lt;')+'</div></div>').join(''):'<div class=card>no hits</div>'}}
async function loadAppr(){const j=await(await fetch('/api/approvals')).json();
 document.getElementById('p-appr').innerHTML=j.pending.length?j.pending.map(a=>
  '<div class=card><h4>#'+a.id+' '+a.tool+'</h4><div class=meta>'+a.created+'</div>'+
  '<div style="margin-top:8px;white-space:pre-wrap">'+a.summary.replace(/</g,'&lt;')+'</div>'+
  '<div class=row><button onclick="decide('+a.id+',true)">Approve</button>'+
  '<button class=no onclick="decide('+a.id+',false)">Deny</button></div></div>').join('')
  :'<div class=card>nothing waiting</div>'}
async function decide(id,ok){await fetch('/api/approvals/'+id,{method:'POST',
 headers:{'Content-Type':'application/json'},body:JSON.stringify({approved:ok})});
 loadAppr();poll()}
async function loadCap(){const j=await(await fetch('/api/status')).json();
 document.getElementById('p-cap').innerHTML='<div class=card><h4>integrations</h4>'+
  Object.entries(j.capabilities).map(([k,v])=>'<div>'+(v?'🟢':'⚪')+' '+k+'</div>').join('')+
  '</div><div class=card><h4>follow-ups</h4>'+(j.followups.length?j.followups.map(t=>
  '<div class=meta>#'+t.id+' '+t.due+' — '+t.what+'</div>').join(''):'<div class=meta>none</div>')+
  '</div><div class=card><h4>secrets stored</h4><div class=meta>'+
  (j.secrets.join(', ')||'none')+'</div></div>'}
async function poll(){const j=await(await fetch('/api/approvals')).json();
 document.getElementById('badge').textContent=j.pending.length?'('+j.pending.length+')':'';
 document.getElementById('hint').textContent=j.pending.length?'action needed':''}
poll();setInterval(poll,20000);
</script></body></html>"""


@app.route("/")
@login_required
def index():
    return UI


# ─── API ──────────────────────────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    msg = (request.get_json(silent=True) or {}).get("message", "").strip()
    if not msg:
        return jsonify({"error": "empty message"}), 400
    try:
        return jsonify({"reply": brain.run(msg, channel="web")})
    except Exception as e:
        log.error(f"chat failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/memory")
@login_required
def api_memory():
    q = request.args.get("q", "")
    return jsonify({"stats": memory.stats(), "hits": memory.search(q, limit=12) if q else []})


@app.route("/api/memory/<path:rid>")
@login_required
def api_memory_read(rid):
    rec = memory.read(rid)
    return jsonify(rec or {"error": "not found"}), (200 if rec else 404)


@app.route("/api/approvals")
@login_required
def api_approvals():
    return jsonify({"pending": approvals.pending()})


@app.route("/api/approvals/<int:aid>", methods=["POST"])
@login_required
def api_decide(aid):
    ok = bool((request.get_json(silent=True) or {}).get("approved"))
    return jsonify(approvals.decide(aid, ok))


@app.route("/api/status")
@login_required
def api_status():
    return jsonify({"capabilities": config.capabilities(),
                    "memory": memory.stats(),
                    "followups": tasks.pending(),
                    "secrets": vault.names() if os.environ.get("VAULT_KEY") else []})


@app.route("/api/secrets", methods=["POST"])
@login_required
def api_secrets():
    d = request.get_json(silent=True) or {}
    if not d.get("name") or not d.get("value"):
        return jsonify({"error": "name and value required"}), 400
    try:
        return jsonify(vault.put(d["name"], d["value"]))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/consolidate", methods=["POST"])
@login_required
def api_consolidate():
    return jsonify(consolidate.run())


@app.route("/webhook/whatsapp", methods=["POST"])
def webhook_whatsapp():
    """Twilio inbound → agent turn → TwiML reply. The main front door."""
    if not verify_twilio(request.url, request.form.to_dict(),
                         request.headers.get("X-Twilio-Signature", "")):
        log.warning("rejected unsigned inbound webhook")
        return "forbidden", 403
    frm = (request.form.get("From") or "").replace("whatsapp:", "").strip()
    body = (request.form.get("Body") or "").strip()
    if config.OWNER_PHONE and frm != config.OWNER_PHONE:
        log.warning(f"ignored inbound from {frm}")
        return "<Response/>", 200, {"Content-Type": "application/xml"}
    sid = request.form.get("MessageSid")
    if sid in _SEEN:
        return "<Response/>", 200, {"Content-Type": "application/xml"}
    _SEEN.append(sid)
    from agent.channels import send_whatsapp
    _answer_async(body, "whatsapp", send_whatsapp)
    return "<Response/>", 200, {"Content-Type": "application/xml"}


# Telegram retries an update the webhook does not answer within ~60s and Twilio
# within 15s — an agent turn routinely takes longer. So every inbound message is
# acknowledged immediately and answered out of band, and repeats are dropped.
_SEEN = deque(maxlen=500)


def _answer_async(text: str, channel: str, deliver):
    def go():
        try:
            reply = brain.run(text, channel=channel)
        except Exception as e:
            log.error(f"{channel} turn failed: {e}")
            reply = "Something broke on my side — try again."
        deliver(reply)
    threading.Thread(target=go, daemon=True).start()


@app.route("/webhook/telegram", methods=["POST"])
def webhook_telegram():
    """Telegram inbound → agent turn → reply. The main front door in production."""
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
        log.warning("rejected telegram update with bad secret token")
        return "forbidden", 403
    update = request.get_json(silent=True) or {}
    uid = update.get("update_id")
    if uid in _SEEN:
        return jsonify({"ok": True})
    _SEEN.append(uid)
    chat_id, text, who = telegram.parse(update)
    allowed = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not allowed:
        # A bot username is public: anyone can message it. Until the owner's chat
        # is pinned down, the agent answers nobody — it just hands over the id.
        log.warning(f"TELEGRAM_CHAT_ID unset — refusing to act (chat {chat_id})")
        telegram.send(f"Not linked yet. Set TELEGRAM_CHAT_ID={chat_id} in the "
                      f"deployment's variables, then message me again.", chat_id)
        return jsonify({"ok": True})
    if chat_id != allowed:
        log.warning(f"ignored telegram message from chat {chat_id} ({who})")
        return jsonify({"ok": True})
    if not text:
        return jsonify({"ok": True})
    _answer_async(text, "telegram", lambda r: telegram.send(r, chat_id))
    return jsonify({"ok": True})


@app.route("/health")
def health():
    ok, why = config.storage_ok()
    return jsonify({"status": "ok" if ok else "degraded", "storage": why,
                    "capabilities": config.capabilities()}), (200 if ok else 200)


# ─── the clock ────────────────────────────────────────────────────────────────

def agent_tick():
    try:
        done = brain.tick()
        if done:
            log.info(f"⏰ ran {len(done)} follow-up(s)")
    except Exception as e:
        log.error(f"tick error: {e}")


def nightly_consolidation():
    try:
        consolidate.run()
    except Exception as e:
        log.error(f"consolidation error: {e}")


def morning_review():
    try:
        brain.daily_briefing()
    except Exception as e:
        log.error(f"daily briefing error: {e}")


def start_scheduler():
    s = BackgroundScheduler(timezone="UTC")
    s.add_job(agent_tick, "interval",
              minutes=int(os.environ.get("AGENT_TICK_MINUTES", "5")),
              id="tick", replace_existing=True)
    s.add_job(nightly_consolidation, "cron",
              hour=int(os.environ.get("CONSOLIDATE_HOUR_UTC", "3")),
              id="consolidate", replace_existing=True)
    if os.environ.get("DAILY_BRIEF", "1") == "1":
        s.add_job(morning_review, "cron",
                  hour=int(os.environ.get("BRIEF_HOUR_UTC", "5")),
                  id="brief", replace_existing=True)
    s.start()
    log.info("⏱  scheduler: follow-ups every "
             f"{os.environ.get('AGENT_TICK_MINUTES', '5')}m, nightly consolidation, "
             "daily review")
    return s


_scheduler = start_scheduler()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log.info(f"🚀 http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
