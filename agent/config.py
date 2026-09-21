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

MEMORY_DIR = Path(os.environ.get("MEMORY_DIR", "memory"))
DB_PATH = Path(os.environ.get("AGENT_DB", "agent.db"))
FILES_DIR = Path(os.environ.get("FILES_DIR", "files"))

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5")
FAST_MODEL = os.environ.get("AGENT_FAST_MODEL", "claude-haiku-4-5-20251001")

# Anything that spends money, contacts a third party, or changes the world
# outside this box needs a human yes first.
AUTO_APPROVE = os.environ.get("AUTO_APPROVE", "0") == "1"


def have(*keys) -> bool:
    return all(os.environ.get(k) for k in keys)


def capabilities() -> dict:
    from . import browser
    return {
        "llm": have("ANTHROPIC_API_KEY"),
        "whatsapp": have("TWILIO_SID", "TWILIO_TOKEN", "YOUR_PHONE"),
        "email_out": have("RESEND_API_KEY", "EMAIL_TO"),
        "gmail": have("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"),
        "calendar": have("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"),
        "web_search": have("BRAVE_API_KEY") or have("SERPER_API_KEY") or True,
        "browser": browser.available(),
        "vault": have("VAULT_KEY"),
    }


def missing_summary() -> str:
    caps = capabilities()
    off = [k for k, v in caps.items() if not v]
    return f"disabled (no credentials): {', '.join(off)}" if off else "all integrations live"
