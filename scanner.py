"""
YUVAL.BOT — Job Scanner
Searches Indeed, LinkedIn, Glassdoor, Google Jobs simultaneously via JobSpy.
Scores each job, tailors resume, writes LinkedIn message + cover letter.
"""

import os, re, json, time, sqlite3, logging, smtplib
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from jobspy import scrape_jobs

log = logging.getLogger("yuvalbot")

# ─── Resume + AI Prompt ───────────────────────────────────────────────────────

RESUME = """
YUVAL STEIMBERG
steimberg.yuval1@gmail.com | +972 54 772 2420 | Tel Aviv, Israel
Dual US/Israeli citizenship

EDUCATION
M.Sc. Electrical & Computer Engineering | Cornell University | 2024-2025 | GPA: 95
VLSI & Computer Architecture focus
• Full RTL-to-GDSII ASIC: SystemVerilog pipelined engine, ModelSim/Innovus/Virtuoso
• Computer Architecture: Top-Down microarchitectural analysis, cache hierarchy, pipeline performance

B.Sc. Materials Engineering | Ben-Gurion University | 2019-2023 | GPA: 90 | Dean's List
Electrical Properties of Semiconductors, Quantum Physics

EXPERIENCE
Product Qualification Engineer | KLA | 2025-Present
• Debugged electrical shorts on advanced optical semiconductor platforms
• Led proof-of-concept: owned test plan, improved throughput by 20%

Hardware Lead | Taktora (NYC AI startup) | 2024-2025
• Led hardware prototyping for real-time factory data collection
• Designed edge-device enclosures with sensors; synced hardware-software pipeline with ML team

Application Engineer | KLA | 2022-2024
• Defined accuracy requirements for laser drilling systems (semiconductor + PCB)
• Led Alpha and Beta product testing roadmap

SKILLS
SystemVerilog, Verilog, RTL architecture, FSM, pipelining, SoC integration
ModelSim, QuestaSim, Innovus, Virtuoso, waveform debugging
Python (OOP), TCL, C

MILITARY: IDF Elite Paratrooper — Commander & Combat Medic | 2015-2018 | Outstanding Soldier Award
"""

SYSTEM_PROMPT = """You are a senior chip industry recruiter reviewing 200 resumes a day.
Given a job and a candidate resume, return ONLY valid JSON (no markdown, no backticks) with:
{
  "fit_score": <0-100 integer>,
  "should_notify": <true if fit_score >= 72>,
  "tailored_resume": "<one page max, every bullet a measurable achievement, zero generic language, ALL CAPS section headers>",
  "linkedin_message": "<3 sentences, punchy opener not 'I hope this finds you well', specific to this role>",
  "cover_letter": "<3 short paragraphs, hook opener not 'I am writing to', confident and specific>"
}"""

# ─── Database ─────────────────────────────────────────────────────────────────

DB = Path("jobs.db")

def init_db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, title TEXT, company TEXT, location TEXT,
        url TEXT, description TEXT, fit_score INTEGER, date_found TEXT,
        status TEXT DEFAULT 'New', tailored_cv TEXT, linkedin_msg TEXT,
        cover_letter TEXT, source TEXT
    )""")
    con.commit(); con.close()

def job_exists(jid):
    con = sqlite3.connect(DB)
    r = con.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone()
    con.close(); return r is not None

def save_job(j):
    con = sqlite3.connect(DB)
    con.execute("""INSERT OR IGNORE INTO jobs
        (id,title,company,location,url,description,fit_score,date_found,
         status,tailored_cv,linkedin_msg,cover_letter,source)
        VALUES(:id,:title,:company,:location,:url,:description,:fit_score,
               :date_found,:status,:tailored_cv,:linkedin_msg,:cover_letter,:source)""", j)
    con.commit(); con.close()

def all_jobs():
    con = sqlite3.connect(DB)
    cur = con.execute("SELECT * FROM jobs ORDER BY date_found DESC")
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall(); con.close()
    return [dict(zip(cols, r)) for r in rows]

def set_status(jid, status):
    con = sqlite3.connect(DB)
    con.execute("UPDATE jobs SET status=? WHERE id=?", (status, jid))
    con.commit(); con.close()

# ─── Relevance filter ─────────────────────────────────────────────────────────

INCLUDE = ["design","verification","vlsi","asic","rtl","chip","logic","fpga",
           "hardware","silicon","semiconductor","digital"]
EXCLUDE = ["senior","sr.","principal","director","manager","staff engineer",
           "software","sales","marketing","devops","data scientist"]

def relevant(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in INCLUDE) and not any(k in t for k in EXCLUDE)

# ─── AI processing ────────────────────────────────────────────────────────────

def ai_process(job: dict) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY","")
    if not key:
        return {"fit_score":0,"should_notify":False,"tailored_resume":"",
                "linkedin_message":"","cover_letter":""}
    prompt = f"""JOB: {job['title']} at {job['company']} ({job['location']})
SOURCE: {job['source']}
DESCRIPTION: {(job.get('description') or '')[:1500]}
URL: {job['url']}

CANDIDATE RESUME:
{RESUME}"""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version":"2023-06-01",
                 "content-type":"application/json"},
        json={"model":"claude-sonnet-4-20250514","max_tokens":2000,
              "system":SYSTEM_PROMPT,"messages":[{"role":"user","content":prompt}]},
        timeout=60)
    resp.raise_for_status()
    raw = resp.json()["content"][0]["text"]
    raw = re.sub(r"^```(?:json)?\s*","",raw.strip(),flags=re.MULTILINE)
    raw = re.sub(r"\s*```$","",raw.strip(),flags=re.MULTILINE)
    try:
        return json.loads(raw)
    except:
        return {"fit_score":0,"should_notify":False,"tailored_resume":raw,
                "linkedin_message":"","cover_letter":""}

# ─── Notifications ────────────────────────────────────────────────────────────

def send_email(job: dict, ai: dict):
    efrom = os.environ.get("EMAIL_FROM","")
    epwd  = os.environ.get("EMAIL_PASSWORD","")
    eto   = os.environ.get("EMAIL_TO","")
    if not all([efrom,epwd,eto]): return
    sc = ai["fit_score"]
    col = "#00aa55" if sc>=88 else "#e6a817" if sc>=72 else "#cc4444"
    html = f"""<div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto">
  <div style="background:#0f0f1e;padding:24px;border-radius:8px 8px 0 0">
    <h1 style="color:#00ff88;margin:0;font-size:20px">⚡ YUVAL.BOT — New Match</h1>
    <p style="color:#666;margin:4px 0 0;font-size:11px">{datetime.now().strftime('%b %d %Y %H:%M')} · {job['source'].upper()}</p>
  </div>
  <div style="background:#fff;padding:24px;border-left:5px solid #00aa55">
    <h2 style="color:#111;margin:0 0 4px">{job['title']}</h2>
    <p style="color:#666;font-size:13px;margin:0 0 16px">{job['company']} · {job['location']}</p>
    <div style="background:#f0fff4;display:inline-block;padding:12px 20px;border-radius:8px;margin-bottom:20px">
      <span style="font-size:30px;font-weight:bold;color:{col}">{sc}%</span>
      <span style="color:#666;font-size:12px;margin-left:6px">match</span>
    </div>
    <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#333">✅ Tailored Resume</h3>
    <pre style="background:#f5f5f5;padding:14px;border-radius:6px;font-size:11px;line-height:1.7;white-space:pre-wrap">{ai.get('tailored_resume','')}</pre>
    <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#333;margin-top:20px">💼 LinkedIn Message</h3>
    <div style="background:#e8f4fd;padding:14px;border-radius:6px;font-size:13px;font-style:italic">{ai.get('linkedin_message','')}</div>
    <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#333;margin-top:20px">✉ Cover Letter</h3>
    <pre style="background:#f5f5f5;padding:14px;border-radius:6px;font-size:12px;line-height:1.8;white-space:pre-wrap">{ai.get('cover_letter','')}</pre>
    <div style="text-align:center;margin-top:24px">
      <a href="{job['url']}" style="background:#00aa55;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:bold">→ APPLY NOW</a>
    </div>
  </div>
  <div style="background:#eee;padding:10px 24px;border-radius:0 0 8px 8px;font-size:10px;color:#999">
    YUVAL.BOT · Auto-scan · Open dashboard to manage all jobs
  </div>
</div>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚀 {sc}% — {job['title']} @ {job['company']} [{job['source'].upper()}]"
    msg["From"] = efrom; msg["To"] = eto
    msg.attach(MIMEText(html,"html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com",465) as s:
            s.login(efrom,epwd); s.send_message(msg)
        log.info(f"📧 Email sent: {job['title']} @ {job['company']}")
    except Exception as e:
        log.error(f"Email failed: {e}")

def send_whatsapp(job: dict, ai: dict):
    sid   = os.environ.get("TWILIO_SID","")
    token = os.environ.get("TWILIO_TOKEN","")
    frm   = os.environ.get("TWILIO_FROM","whatsapp:+14155238886")
    to    = os.environ.get("YOUR_PHONE","")
    if not all([sid,token,to]): return
    body = (f"🤖 *YUVAL.BOT*\n\n"
            f"📌 *{job['title']}*\n"
            f"🏢 {job['company']} · {job['location']}\n"
            f"📊 Match: *{ai['fit_score']}%*\n"
            f"🌐 Source: {job['source'].upper()}\n\n"
            f"✅ Full resume + cover letter in your email!\n\n"
            f"👉 {job['url']}")
    try:
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid,token),
            data={"From":frm,"To":f"whatsapp:{to}","Body":body},
            timeout=15)
        resp.raise_for_status()
        log.info(f"💬 WhatsApp sent: {job['title']}")
    except Exception as e:
        log.error(f"WhatsApp failed: {e}")

# ─── Main scan ────────────────────────────────────────────────────────────────

QUERIES = [
    "junior ASIC design engineer",
    "junior RTL design engineer",
    "junior verification engineer SystemVerilog",
    "chip design engineer entry level",
    "logic design engineer new graduate",
    "VLSI engineer graduate",
    "junior hardware design engineer",
    "FPGA design engineer junior",
]

SITES = ["indeed", "linkedin"]  # google rate-limits heavily; indeed+linkedin sufficient

def run_scan():
    log.info("="*50)
    log.info(f"🔍 Scan started {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    new, notified = 0, 0
    seen = set()
    min_score = int(os.environ.get("MIN_FIT_SCORE","72"))

    for query in QUERIES:
        log.info(f"  Searching: '{query}'")
        try:
            df = scrape_jobs(
                site_name=SITES,
                search_term=query,
                location="Israel",
                country_indeed="Israel",
                results_wanted=10,
                hours_old=72,
                description_format="markdown",
            )
        except Exception as e:
            log.warning(f"  Scrape error for '{query}': {e}")
            try:
                # Fallback: Indeed only
                df = scrape_jobs(
                    site_name=["indeed"],
                    search_term=query,
                    location="Israel",
                    country_indeed="Israel",
                    results_wanted=10,
                    hours_old=72,
                )
            except Exception as e2:
                log.warning(f"  Fallback also failed: {e2}")
                continue

        for _, row in df.iterrows():
            jid = str(row.get("id") or f"{row.get('site','')}_{hash(str(row.get('job_url',''))%99999)}")
            if jid in seen: continue
            seen.add(jid)
            if job_exists(jid): continue

            title = str(row.get("title",""))
            if not relevant(title):
                continue

            job = {
                "id": jid,
                "title": title,
                "company": str(row.get("company","Unknown")),
                "location": str(row.get("location","Israel")),
                "url": str(row.get("job_url") or row.get("job_url_direct","")),
                "description": str(row.get("description",""))[:2000],
                "source": str(row.get("site","unknown")),
                "fit_score": 0,
                "date_found": datetime.now().isoformat(),
                "status": "New",
                "tailored_cv": "",
                "linkedin_msg": "",
                "cover_letter": "",
            }
            new += 1
            log.info(f"  🆕 {title} @ {job['company']} [{job['source']}]")

            try:
                ai = ai_process(job)
                job["fit_score"]  = ai.get("fit_score", 0)
                job["tailored_cv"]  = ai.get("tailored_resume","")
                job["linkedin_msg"] = ai.get("linkedin_message","")
                job["cover_letter"] = ai.get("cover_letter","")
                log.info(f"     AI score: {job['fit_score']}%")
            except Exception as e:
                log.error(f"     AI error: {e}")
                ai = {"fit_score":0,"should_notify":False}

            save_job(job)

            if ai.get("should_notify") and job["fit_score"] >= min_score:
                notified += 1
                send_email(job, ai)
                send_whatsapp(job, ai)
                time.sleep(2)

        time.sleep(8)  # polite delay between queries

    log.info(f"✅ Done. New={new} Notified={notified}")
    return new, notified
