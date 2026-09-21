"""Outbound message channels: WhatsApp (Twilio) and email (Resend)."""

import os, logging, requests

log = logging.getLogger("yuvalbot.channels")


def send_whatsapp(body: str) -> dict:
    sid, token = os.environ.get("TWILIO_SID", ""), os.environ.get("TWILIO_TOKEN", "")
    to = os.environ.get("YOUR_PHONE", "").replace("whatsapp:", "").strip()
    if not all([sid, token, to]):
        return {"ok": False, "error": "TWILIO_SID / TWILIO_TOKEN / YOUR_PHONE not set"}
    frm = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    try:
        r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                          auth=(sid, token),
                          data={"From": frm, "To": f"whatsapp:{to}", "Body": body[:1500]},
                          timeout=20)
        ok = r.status_code == 201
        if not ok:
            log.error(f"whatsapp {r.status_code}: {r.text[:200]}")
        return {"ok": ok, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_email(subject: str, body: str, to: str | None = None) -> dict:
    key = os.environ.get("RESEND_API_KEY", "")
    to = to or os.environ.get("EMAIL_TO", "")
    if not key or not to:
        return {"ok": False, "error": "RESEND_API_KEY / EMAIL_TO not set"}
    html = "<div style='font-family:system-ui,Arial,sans-serif;max-width:640px'>" + \
           "".join(f"<p>{line}</p>" for line in body.split("\n") if line.strip()) + "</div>"
    try:
        r = requests.post("https://api.resend.com/emails",
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"},
                          json={"from": os.environ.get("EMAIL_FROM", "onboarding@resend.dev"),
                                "to": [to], "subject": subject[:200], "html": html},
                          timeout=25)
        ok = r.status_code in (200, 201)
        if not ok:
            log.error(f"resend {r.status_code}: {r.text[:200]}")
        return {"ok": ok, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send(channel: str, body: str, subject: str = "your agent") -> dict:
    if channel == "email":
        return send_email(subject, body)
    if channel == "whatsapp":
        return send_whatsapp(body)
    if channel == "telegram":
        from . import telegram
        return telegram.send(body)
    if channel == "auto":
        from . import telegram
        if telegram.configured():
            return telegram.send(body)
        if os.environ.get("TWILIO_SID"):
            return send_whatsapp(body)
        return send_email(subject, body)
    return {"ok": True, "note": f"channel '{channel}' is local-only; nothing sent"}


def verify_twilio(url: str, params: dict, signature: str) -> bool:
    """Twilio request signature (HMAC-SHA1 over URL + sorted params).

    Without this anyone who finds the webhook URL can spoof the From header and
    drive the agent — and the agent can send mail and write memory.
    """
    import hmac, hashlib, base64
    token = os.environ.get("TWILIO_TOKEN", "")
    if not token:
        return not os.environ.get("REQUIRE_TWILIO_SIGNATURE", "1") == "1"
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = base64.b64encode(
        hmac.new(token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(digest, signature or "")
