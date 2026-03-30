"""
YUVAL.BOT — Main App
Fixed: init_db() called at module level so gunicorn initializes DB on startup.
Fixed: Glassdoor/ZipRecruiter removed (blocked for Israel).
Fixed: Scan button no longer freezes — polls every 10s for completion.
"""

import os, logging, threading
from datetime import datetime
from functools import wraps
from flask import Flask, render_template_string, jsonify, request, session, redirect
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import init_db, all_jobs, set_status, run_scan

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("yuvalbot")

# ─── Flask ────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "yuval-bot-2026-secret")

SCAN_STATUS = {"last": "Never", "running": False, "next": "–", "count": 0}

# ── CRITICAL FIX: init DB at module level so gunicorn workers pick it up ──────
init_db()
log.info("✅ Database ready")

# ─── Auth ─────────────────────────────────────────────────────────────────────
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
        if request.form.get("password") == os.environ.get("DASHBOARD_PASSWORD", "yuval2026"):
            session["ok"] = True
            return redirect("/")
        err = "Wrong password"
    return f"""<!DOCTYPE html><html><head><title>YUVAL.BOT</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@800&display=swap');
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0f;display:flex;align-items:center;justify-content:center;min-height:100vh;font-family:'JetBrains Mono',monospace}}
.box{{background:#0d0d1a;border:1px solid #1a1a2e;border-radius:12px;padding:44px;width:340px;text-align:center}}
.logo{{font-family:'Syne',sans-serif;font-size:26px;font-weight:800;color:#fff;margin-bottom:4px}}
.logo span{{color:#00ff88}}.sub{{color:#444;font-size:11px;margin-bottom:32px}}
input{{width:100%;background:#060608;border:1px solid #1a1a2e;color:#fff;padding:11px 14px;border-radius:5px;font-family:inherit;font-size:13px;margin-bottom:12px}}
input:focus{{outline:none;border-color:#00ff88}}
button{{width:100%;background:#00ff88;color:#000;border:none;padding:12px;border-radius:5px;font-family:inherit;font-weight:700;font-size:13px;cursor:pointer}}
.err{{color:#ff5555;font-size:11px;margin-top:10px}}
</style></head><body>
<div class="box">
  <div class="logo">YUVAL<span>.BOT</span></div>
  <div class="sub">Job Hunter Dashboard</div>
  <form method="POST">
    <input type="password" name="password" placeholder="Enter password" autofocus>
    <button>Enter →</button>
    <div class="err">{err}</div>
  </form>
</div></body></html>"""

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ─── Dashboard ────────────────────────────────────────────────────────────────
DASH = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YUVAL.BOT</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600;700&family=Syne:wght@600;700;800&display=swap');
:root{--g:#00ff88;--bg:#0a0a0f;--card:#0d0d1a;--b:#1a1a2e;--t:#c8c8d0;--d:#555;--y:#ffe44d;--r:#ff5555;--bl:#4d9fff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--t);font-family:'JetBrains Mono',monospace;min-height:100vh}
nav{background:#0d0d1a;border-bottom:1px solid var(--b);padding:13px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.logo{font-family:'Syne',sans-serif;font-weight:800;font-size:17px;color:#fff}.logo span{color:var(--g)}
.nav-r{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.live{display:inline-flex;align-items:center;gap:5px;background:rgba(0,255,136,.08);border:1px solid rgba(0,255,136,.2);color:var(--g);font-size:10px;padding:4px 10px;border-radius:3px}
.dot{width:6px;height:6px;border-radius:50%;background:var(--g);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.btn{cursor:pointer;border:none;font-family:inherit;font-size:11px;font-weight:700;padding:7px 14px;border-radius:4px;transition:all .15s;letter-spacing:.04em}
.btn-g{background:var(--g);color:#000}.btn-g:hover{opacity:.85}
.btn-o{background:transparent;border:1px solid var(--g);color:var(--g)}.btn-o:hover{background:rgba(0,255,136,.08)}
.btn-y{background:transparent;border:1px solid var(--y);color:var(--y)}
.btn-r{background:transparent;border:1px solid var(--r);color:var(--r)}
.btn-sm{padding:4px 9px;font-size:10px}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;padding:16px 24px;border-bottom:1px solid var(--b)}
.stat{background:var(--card);border:1px solid var(--b);border-radius:6px;padding:12px 16px}
.sn{font-family:'Syne',sans-serif;font-size:24px;font-weight:800;color:#fff;line-height:1}
.sl{font-size:9px;color:var(--d);margin-top:3px;text-transform:uppercase;letter-spacing:.08em}
.bar{background:#0d0d1a;border-bottom:1px solid var(--b);padding:6px 24px;font-size:10px;color:var(--d);display:flex;gap:16px;flex-wrap:wrap}
.bar span{color:var(--t)}
.scanning{display:none;background:#0a1a12;border-bottom:1px solid var(--g);padding:8px 24px;font-size:11px;color:var(--g);align-items:center;gap:10px}
.scanning.show{display:flex}
.filters{padding:12px 24px;display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--b)}
.fb{cursor:pointer;font-family:inherit;font-size:10px;padding:4px 11px;border-radius:3px;border:1px solid var(--b);background:transparent;color:var(--d);transition:all .15s;text-transform:uppercase;letter-spacing:.06em}
.fb:hover,.fb.active{border-color:var(--g);color:var(--g);background:rgba(0,255,136,.05)}
.table-w{padding:0 24px 60px;overflow-x:auto}
table{width:100%;border-collapse:collapse;margin-top:14px}
th{font-size:9px;color:var(--d);text-transform:uppercase;letter-spacing:.1em;padding:9px 10px;border-bottom:1px solid var(--b);text-align:left;white-space:nowrap}
td{padding:11px 10px;border-bottom:1px solid #111;font-size:12px;vertical-align:middle}
tr:hover td{background:#0d0d1a}
.sc{font-family:'Syne',sans-serif;font-weight:800;font-size:15px}
.sc.hi{color:var(--g)}.sc.mi{color:var(--y)}.sc.lo{color:var(--r)}
.sb{display:inline-block;padding:2px 7px;border-radius:3px;font-size:10px;font-weight:600;white-space:nowrap}
.s-New{background:rgba(77,159,255,.1);border:1px solid rgba(77,159,255,.3);color:var(--bl)}
.s-Applied{background:rgba(0,255,136,.1);border:1px solid rgba(0,255,136,.3);color:var(--g)}
.s-Skipped{background:rgba(85,85,85,.1);border:1px solid #222;color:var(--d)}
.src{font-size:9px;padding:2px 6px;border-radius:2px;border:1px solid var(--b);color:var(--d);white-space:nowrap}
.jt{color:#fff;font-weight:600;font-size:12px}.co{color:var(--d);font-size:11px;margin-top:2px}
.acts{display:flex;gap:5px;flex-wrap:wrap;margin-top:5px}
.mo{display:none;position:fixed;inset:0;background:rgba(0,0,0,.88);z-index:1000;align-items:flex-start;justify-content:center;padding:20px;overflow-y:auto}
.mo.open{display:flex}
.mbox{background:#0d0d1a;border:1px solid var(--b);border-radius:10px;width:100%;max-width:740px}
.mh{background:#111;padding:18px 22px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--b);border-radius:10px 10px 0 0}
.mh h2{font-family:'Syne',sans-serif;font-size:15px;color:#fff}
.mc{cursor:pointer;color:var(--d);font-size:16px;background:transparent;border:none;font-family:inherit}.mc:hover{color:#fff}
.mb{padding:22px}
.mt{display:flex;gap:0;margin-bottom:18px;border-bottom:1px solid var(--b)}
.mtb{cursor:pointer;background:transparent;border:none;font-family:inherit;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--d);padding:7px 13px;border-bottom:2px solid transparent;margin-bottom:-1px;transition:all .15s}
.mtb:hover,.mtb.a{color:var(--g);border-bottom-color:var(--g)}
.mcon{display:none}.mcon.a{display:block}
pre.cv{background:#060608;border:1px solid var(--b);border-radius:6px;padding:14px;font-size:11px;line-height:1.75;white-space:pre-wrap;color:#ccc;max-height:400px;overflow-y:auto}
.cpb{background:transparent;border:1px solid var(--g);color:var(--g);font-family:inherit;font-size:10px;padding:4px 9px;border-radius:3px;cursor:pointer;float:right;margin-bottom:6px}
.cpb:hover{background:rgba(0,255,136,.08)}
.toast{position:fixed;bottom:20px;right:20px;background:#00aa55;color:#fff;padding:11px 18px;border-radius:6px;font-size:11px;z-index:9999;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--b);border-top-color:var(--g);border-radius:50%;animation:sp .7s linear infinite;vertical-align:middle;margin-right:5px}
@keyframes sp{to{transform:rotate(360deg)}}
@media(max-width:700px){.stats{grid-template-columns:repeat(2,1fr)}.table-w{padding:0 12px 40px}nav{padding:10px 14px}}
</style></head><body>

<nav>
  <div class="logo">YUVAL<span>.BOT</span></div>
  <div class="nav-r">
    <span class="live"><span class="dot"></span>LIVE</span>
    <button class="btn btn-g" id="scanBtn" onclick="doScan()">⟳ Scan Now</button>
    <button class="btn btn-r btn-sm" onclick="clearDB()" style="border-color:#ff8c42;color:#ff8c42">🗑 Clear DB</button>
    <a href="/logout"><button class="btn btn-r btn-sm">Logout</button></a>
  </div>
</nav>

<div class="scanning" id="scanBanner">
  <span class="spin"></span>
  Scanning Indeed · LinkedIn · Google Jobs — jobs appear automatically every 10s
</div>

<div class="bar">
  <div>Last scan: <span id="lastScan">–</span></div>
  <div>Next auto-scan: <span id="nextScan">–</span></div>
  <div>Sources: <span style="color:var(--g)">Indeed · LinkedIn · Google Jobs</span></div>
</div>

<div class="stats">
  <div class="stat"><div class="sn" id="s0">–</div><div class="sl">Total Found</div></div>
  <div class="stat"><div class="sn" id="s1" style="color:var(--bl)">–</div><div class="sl">New</div></div>
  <div class="stat"><div class="sn" id="s2" style="color:var(--g)">–</div><div class="sl">Applied</div></div>
  <div class="stat"><div class="sn" id="s3" style="color:var(--y)">–</div><div class="sl">Avg Score</div></div>
  <div class="stat"><div class="sn" id="s4" style="color:var(--d)">–</div><div class="sl">Skipped</div></div>
</div>

<div class="filters">
  <button class="fb active" onclick="filt('all',this)">All</button>
  <button class="fb" onclick="filt('New',this)">New</button>
  <button class="fb" onclick="filt('Applied',this)">Applied</button>
  <button class="fb" onclick="filt('Skipped',this)">Skipped</button>
  <button class="fb" onclick="filt('indeed',this)">Indeed</button>
  <button class="fb" onclick="filt('linkedin',this)">LinkedIn</button>
  <button class="fb" onclick="filt('google',this)">Google</button>
</div>

<div class="table-w">
  <table><thead><tr>
    <th>Score</th><th>Job</th><th>Source</th><th>Found</th><th>Status</th><th>Actions</th>
  </tr></thead>
  <tbody id="tbody"><tr><td colspan="6" style="text-align:center;color:var(--d);padding:40px">
    Click ⟳ Scan Now to search for jobs
  </td></tr></tbody></table>
</div>

<div class="mo" id="modal" onclick="if(event.target===this)closeMo()">
  <div class="mbox">
    <div class="mh"><h2 id="mtitle">Details</h2><button class="mc" onclick="closeMo()">✕</button></div>
    <div class="mb">
      <div class="mt">
        <button class="mtb a" onclick="swTab('cv',this)">Tailored CV</button>
        <button class="mtb" onclick="swTab('cl',this)">Cover Letter</button>
        <button class="mtb" onclick="swTab('li',this)">LinkedIn Msg</button>
      </div>
      <div class="mcon a" id="tab-cv"><button class="cpb" onclick="cp('cv-t')">COPY</button><pre class="cv" id="cv-t"></pre></div>
      <div class="mcon" id="tab-cl"><button class="cpb" onclick="cp('cl-t')">COPY</button><pre class="cv" id="cl-t"></pre></div>
      <div class="mcon" id="tab-li"><button class="cpb" onclick="cp('li-t')">COPY</button><pre class="cv" id="li-t"></pre></div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
let jobs=[], cur='all', pollTimer=null;

async function load(){
  try{
    const [jr,sr]=await Promise.all([fetch('/api/jobs'),fetch('/api/scan-status')]);
    jobs=await jr.json();
    const si=await sr.json();
    document.getElementById('lastScan').textContent=si.last||'Never';
    document.getElementById('nextScan').textContent=si.next||'–';
    const banner=document.getElementById('scanBanner');
    const btn=document.getElementById('scanBtn');
    if(si.running){
      banner.classList.add('show');
      btn.disabled=true;
      btn.innerHTML='<span class="spin"></span>Scanning...';
    } else {
      banner.classList.remove('show');
      btn.disabled=false;
      btn.textContent='⟳ Scan Now';
    }
    stats(); render();
  }catch(e){console.error(e);}
}

function stats(){
  document.getElementById('s0').textContent=jobs.length;
  document.getElementById('s1').textContent=jobs.filter(j=>j.status==='New').length;
  document.getElementById('s2').textContent=jobs.filter(j=>j.status==='Applied').length;
  document.getElementById('s4').textContent=jobs.filter(j=>j.status==='Skipped').length;
  const a=jobs.length?Math.round(jobs.reduce((s,j)=>s+(j.fit_score||0),0)/jobs.length):0;
  document.getElementById('s3').textContent=a+'%';
}

function render(){
  const srcs=['indeed','linkedin','glassdoor','google','zip_recruiter'];
  const f=cur==='all'?jobs:srcs.includes(cur)?jobs.filter(j=>j.source===cur):jobs.filter(j=>j.status===cur);
  if(!f.length){document.getElementById('tbody').innerHTML='<tr><td colspan="6" style="text-align:center;color:var(--d);padding:40px">No jobs yet — click ⟳ Scan Now</td></tr>';return;}
  document.getElementById('tbody').innerHTML=f.map(j=>{
    const sc=j.fit_score||0,cls=sc>=88?'hi':sc>=72?'mi':'lo';
    const dt=(j.date_found||'').split('T')[0];
    return `<tr>
      <td><span class="sc ${cls}">${sc}%</span></td>
      <td><div class="jt">${j.title}</div><div class="co">${j.company} · ${j.location}</div></td>
      <td><span class="src">${j.source||'–'}</span></td>
      <td style="color:var(--d);font-size:11px">${dt}</td>
      <td><span class="sb s-${j.status}">${j.status}</span></td>
      <td><div class="acts">
        <button class="btn btn-o btn-sm" onclick="show('${j.id}')">View CV</button>
        <a href="${j.url}" target="_blank"><button class="btn btn-g btn-sm">Apply ↗</button></a>
        <button class="btn btn-y btn-sm" onclick="mark('${j.id}','Applied')">✓</button>
        <button class="btn btn-r btn-sm" onclick="mark('${j.id}','Skipped')">✕</button>
      </div></td></tr>`;
  }).join('');
}

function filt(f,el){cur=f;document.querySelectorAll('.fb').forEach(b=>b.classList.remove('active'));el.classList.add('active');render();}

function show(id){
  const j=jobs.find(x=>x.id===id);if(!j)return;
  document.getElementById('mtitle').textContent=j.title+' @ '+j.company;
  document.getElementById('cv-t').textContent=j.tailored_cv||'Not generated yet.';
  document.getElementById('cl-t').textContent=j.cover_letter||'Not generated yet.';
  document.getElementById('li-t').textContent=j.linkedin_msg||'Not generated yet.';
  swTab('cv',document.querySelector('.mtb'));
  document.getElementById('modal').classList.add('open');
}
function closeMo(){document.getElementById('modal').classList.remove('open');}
function swTab(n,el){
  document.querySelectorAll('.mcon').forEach(c=>c.classList.remove('a'));
  document.querySelectorAll('.mtb').forEach(t=>t.classList.remove('a'));
  document.getElementById('tab-'+n).classList.add('a');if(el)el.classList.add('a');
}
async function mark(id,st){
  await fetch('/api/mark/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:st})});
  const j=jobs.find(x=>x.id===id);if(j)j.status=st;
  stats();render();toast(st==='Applied'?'✓ Marked Applied!':'Marked Skipped');
}
async function doScan(){
  const r=await fetch('/api/scan',{method:'POST'});
  const d=await r.json();
  if(!d.ok){toast('Already scanning — check the banner above');return;}
  toast('🔍 Scan started! Jobs will appear every 10s');
  if(pollTimer)clearInterval(pollTimer);
  pollTimer=setInterval(async()=>{
    await load();
    const sr=await fetch('/api/scan-status');
    const si=await sr.json();
    if(!si.running){
      clearInterval(pollTimer);pollTimer=null;
      toast('✅ Scan complete — '+si.count+' new jobs found!');
    }
  },10000);
}
function cp(id){navigator.clipboard.writeText(document.getElementById(id).textContent);toast('Copied!');}
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),4000);}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeMo();});
async function clearDB(){
  if(!confirm('Clear all jobs and rescan from scratch?'))return;
  await fetch('/api/clear',{method:'POST'});
  toast('🗑 Database cleared — click Scan Now to rescan');
  jobs=[];stats();render();
}
setInterval(load,30000);
load();
</script></body></html>"""

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template_string(DASH)

@app.route("/api/jobs")
@login_required
def api_jobs():
    return jsonify(all_jobs())

@app.route("/api/scan-status")
@login_required
def api_scan_status():
    return jsonify(SCAN_STATUS)

@app.route("/api/mark/<jid>", methods=["POST"])
@login_required
def api_mark(jid):
    set_status(jid, request.get_json()["status"])
    return jsonify({"ok": True})

@app.route("/api/scan", methods=["POST"])
@login_required
def api_scan():
    if SCAN_STATUS["running"]:
        return jsonify({"ok": False, "msg": "Already scanning"})
    def go():
        SCAN_STATUS["running"] = True
        SCAN_STATUS["last"] = datetime.now().strftime("%b %d %H:%M")
        SCAN_STATUS["count"] = 0
        try:
            new, _ = run_scan()
            SCAN_STATUS["count"] = new
        except Exception as e:
            log.error(f"Scan error: {e}")
        finally:
            SCAN_STATUS["running"] = False
    threading.Thread(target=go, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/health")
def health():
    return "OK", 200


@app.route("/api/clear", methods=["POST"])
@login_required
def api_clear():
    import sqlite3
    from scanner import DB
    con = sqlite3.connect(DB)
    con.execute("DELETE FROM jobs")
    con.commit()
    con.close()
    log.info("🗑 Database cleared by user")
    return jsonify({"ok": True})

# ─── Scheduler ────────────────────────────────────────────────────────────────

def scheduled_scan():
    if not SCAN_STATUS["running"]:
        SCAN_STATUS["running"] = True
        SCAN_STATUS["last"] = datetime.now().strftime("%b %d %H:%M")
        try:
            new, _ = run_scan()
            SCAN_STATUS["count"] = new
        except Exception as e:
            log.error(f"Scheduled scan error: {e}")
        finally:
            SCAN_STATUS["running"] = False

def start_scheduler():
    hours = int(os.environ.get("SCAN_INTERVAL_HOURS", "4"))
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_scan, "interval", hours=hours,
                      id="scan", replace_existing=True)
    scheduler.start()
    SCAN_STATUS["next"] = f"Every {hours}h"
    log.info(f"⏱  Scheduler: every {hours} hours")
    return scheduler

_scheduler = start_scheduler()

# ─── Local dev ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log.info(f"🚀 http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
