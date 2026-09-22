"""Central config + capability reporting.

The agent tells the user honestly what it can and cannot do right now, which
depends entirely on which credentials are present.
"""

import os
from pathlib import Path

OWNER_NAME = os.environ.get("OWNER_NAME", "Yuval")
OWNER_PHONE = os.environ.get("YOUR_PHONE", "").replace("whatsapp:", "").strip()
OWNER_EMAIL = os.environ.get("EMAIL_TO", "")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Jerusalem")

# Everything stateful lives under DATA_DIR. On Railway that is the mounted
# volume; without it the container's filesystem is wiped on every deploy and the
# agent forgets everything, so we fail loudly rather than quietly amnesiac.
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data" if os.path.isdir("/data") else "."))

MEMORY_DIR = Path(os.environ.get("MEMORY_DIR", DATA_DIR / "memory"))
DB_PATH = Path(os.environ.get("AGENT_DB", DATA_DIR / "agent.db"))
FILES_DIR = Path(os.environ.get("FILES_DIR", DATA_DIR / "files"))
VAULT_PATH = Path(os.environ.get("VAULT_PATH", DATA_DIR / "vault.enc"))
BROWSER_STATE = Path(os.environ.get("BROWSER_STATE", DATA_DIR / "browser_state.json"))
PLACEHOLDERS = ("your-app", "your-domain", "yourdomain", "example.com",
                "<", "changeme", "my-app.up.railway.app")


def _placeholder(value: str) -> bool:
    """A copied-from-the-example value is worse than no value: it registers a
    webhook against somebody else's domain and everything looks configured."""
    low = value.lower()
    return any(p in low for p in PLACEHOLDERS)


def _public_url() -> str:
    """Where this deployment can be reached from the internet.

    Railway hands us RAILWAY_PUBLIC_DOMAIN once a domain exists, so the Telegram
    webhook registers itself without anyone having to copy the domain into a
    second variable — a step that silently leaves the bot deaf when skipped.
    """
    explicit = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
    if explicit and not _placeholder(explicit):
        return explicit if explicit.startswith("http") else f"https://{explicit}"
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip().rstrip("/")
    return f"https://{domain}" if domain else ""


PUBLIC_URL = _public_url()


def storage_ok() -> tuple[bool, str]:
    """Is state on something that survives a redeploy?"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = DATA_DIR / ".write-test"
        probe.write_text("ok"); probe.unlink()
    except Exception as e:
        return False, f"DATA_DIR {DATA_DIR} is not writable: {e}"
    if os.environ.get("RAILWAY_ENVIRONMENT") and str(DATA_DIR) in (".", "/app"):
        return False, ("running on Railway with no volume mounted — memory will be "
                       "wiped on every deploy. Mount a volume and set DATA_DIR to it.")
    return True, f"state in {DATA_DIR}"

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5")
FAST_MODEL = os.environ.get("AGENT_FAST_MODEL", "claude-haiku-4-5-20251001")

# Anything that spends money, contacts a third party, or changes the world
# outside this box needs a human yes first.
AUTO_APPROVE = os.environ.get("AUTO_APPROVE", "0") == "1"


def setting(env: str, default: str = "") -> str:
    """A credential, from the environment or from what was connected in the UI."""
    from . import store
    return os.environ.get(env) or store.store_str(env.lower()) or default


def have(*keys) -> bool:
    return all(setting(k) for k in keys)


def google_ready() -> bool:
    from . import google
    try:
        return google.configured()
    except Exception:
        return False


def capabilities() -> dict:
    from . import browser
    return {
        "llm": have("ANTHROPIC_API_KEY"),
        "whatsapp": have("TWILIO_SID", "TWILIO_TOKEN", "YOUR_PHONE"),
        "email_out": have("RESEND_API_KEY", "EMAIL_TO"),
        "gmail": google_ready(),
        "calendar": google_ready(),
        "web_search": have("BRAVE_API_KEY") or have("SERPER_API_KEY") or True,
        "telegram": have("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        "drive": google_ready(),
        "contacts": google_ready(),
        "browser": browser.available(),
        "vault": have("VAULT_KEY"),
        "persistent_storage": storage_ok()[0],
    }


# What each capability actually needs, so the agent can name the missing piece
# instead of speculating about dashboards and integrations it does not have.
REQUIREMENTS = {
    "llm": "ANTHROPIC_API_KEY",
    "whatsapp": "TWILIO_SID + TWILIO_TOKEN + YOUR_PHONE",
    "telegram": "open /connect and link Telegram in two taps",
    "email_out": "a Resend key on /connect",
    "gmail": "open the /connect page and press Connect Google",
    "calendar": "open the /connect page and press Connect Google",
    "drive": "open the /connect page and press Connect Google",
    "contacts": "open the /connect page and press Connect Google",
    "browser": "the playwright package in the image",
    "vault": "VAULT_KEY",
    "mcp": "add a server on /connect",
    "web_search": "a Brave or Serper key on /connect (DuckDuckGo otherwise)",
    "persistent_storage": "a volume mounted at DATA_DIR",
}


def missing_summary() -> str:
    caps = capabilities()
    off = [k for k, v in caps.items() if not v]
    return f"disabled (no credentials): {', '.join(off)}" if off else "all integrations live"
