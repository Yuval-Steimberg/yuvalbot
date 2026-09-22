"""Telegram front door.

Preferred over WhatsApp for a proactive agent: no 24-hour reply window, no
message templates, no sandbox that expires every three days. The agent can text
you first, at 3am, for free.
"""

import os, logging, requests

log = logging.getLogger("yuvalbot.telegram")


def token() -> str:
    from . import config
    return config.setting("TELEGRAM_BOT_TOKEN")


def chat_id() -> str:
    from . import config
    return config.setting("TELEGRAM_CHAT_ID")


def configured() -> bool:
    return bool(token())


def me() -> dict:
    """Who this bot is — used to build the t.me deep link."""
    return _api("getMe")


def _api(method: str, **payload):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set"}
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                          json=payload, timeout=25)
        d = r.json()
        if not d.get("ok"):
            log.error(f"telegram {method}: {str(d)[:200]}")
        return d
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send(text: str, chat: str | None = None) -> dict:
    target = chat or chat_id()
    if not target:
        return {"ok": False, "error": "Telegram is not linked yet"}
    # Telegram caps a message at 4096 chars; split rather than truncate.
    chunks = [text[i:i + 3900] for i in range(0, len(text) or 1, 3900)] or [""]
    out = {}
    for c in chunks:
        out = _api("sendMessage", chat_id=target, text=c,
                   disable_web_page_preview=True)
    return {"ok": bool(out.get("ok")), "chunks": len(chunks)}


def set_webhook(public_url: str) -> dict:
    """Point Telegram at this deployment. Called once at boot."""
    from . import config
    secret = config.setting("TELEGRAM_WEBHOOK_SECRET")
    d = _api("setWebhook", url=f"{public_url}/webhook/telegram",
             secret_token=secret, drop_pending_updates=True,
             allowed_updates=["message"])
    if d.get("ok"):
        log.info(f"📡 telegram webhook → {public_url}/webhook/telegram")
    return d


def webhook_info() -> dict:
    tok = token()
    if not tok:
        return {"ok": False, "error": "no Telegram bot token yet"}
    try:
        return requests.get(f"https://api.telegram.org/bot{tok}/getWebhookInfo",
                            timeout=20).json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def download(file_id: str) -> tuple[bytes, str]:
    """Fetch a photo or document the owner sent. Returns (bytes, filename)."""
    tok = token()
    info = _api("getFile", file_id=file_id)
    path = (info.get("result") or {}).get("file_path", "")
    if not path:
        raise RuntimeError(f"telegram would not give the file: {str(info)[:120]}")
    r = requests.get(f"https://api.telegram.org/file/bot{tok}/{path}", timeout=60)
    r.raise_for_status()
    return r.content, path.split("/")[-1]


def media(update: dict) -> dict:
    """What the owner attached: a photo to look at, or a file to read."""
    msg = update.get("message") or update.get("edited_message") or {}
    if msg.get("photo"):
        biggest = sorted(msg["photo"], key=lambda p: p.get("file_size", 0))[-1]
        return {"kind": "photo", "file_id": biggest["file_id"],
                "caption": msg.get("caption", "")}
    doc = msg.get("document")
    if doc:
        return {"kind": "document", "file_id": doc["file_id"],
                "filename": doc.get("file_name", "file"),
                "mime": doc.get("mime_type", ""), "caption": msg.get("caption", "")}
    if msg.get("voice") or msg.get("audio"):
        return {"kind": "voice"}
    return {}


def parse(update: dict) -> tuple[str, str, str]:
    """(chat_id, text, sender_name) from an incoming update."""
    msg = update.get("message") or update.get("edited_message") or {}
    chat = str((msg.get("chat") or {}).get("id", ""))
    frm = msg.get("from") or {}
    return chat, (msg.get("text") or "").strip(), frm.get("username") or frm.get("first_name", "")
