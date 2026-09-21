"""Real browser control (Playwright + Chromium), used only when a page needs it.

Optional: if Playwright is not installed the tools report themselves unavailable
instead of failing mid-task. Every action that changes state on a site is gated
through approvals, and credentials are injected from the vault, never from the
model's own text.
"""

import os, logging
from . import vault

log = logging.getLogger("yuvalbot.browser")

HEADLESS = os.environ.get("BROWSER_HEADLESS", "1") == "1"
STATE = os.environ.get("BROWSER_STATE", "browser_state.json")


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _page(pw):
    kwargs = {"headless": HEADLESS}
    exe = os.environ.get("CHROMIUM_PATH")
    if exe:
        kwargs["executable_path"] = exe
    b = pw.chromium.launch(**kwargs)
    ctx = b.new_context(storage_state=STATE if os.path.exists(STATE) else None)
    return b, ctx, ctx.new_page()


def read(url: str, max_chars: int = 6000) -> dict:
    """Render a JS page and return its visible text."""
    if not available():
        return {"error": "playwright not installed", "fallback": "use web_fetch"}
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            b, ctx, page = _page(pw)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1200)
                text = page.inner_text("body")[:max_chars]
                return {"url": page.url, "title": page.title(), "text": text}
            finally:
                ctx.storage_state(path=STATE)
                b.close()
    except Exception as e:
        return {"error": f"browser read failed: {e}"}


def act(url: str, steps: list, max_chars: int = 4000) -> dict:
    """Run a short script of steps against a page.

    steps: [{"click": "selector"} | {"fill": "selector", "value": "text or
            {{secret:NAME}}"} | {"press": "Enter"} | {"wait": 1500} |
            {"goto": "url"}]
    """
    if not available():
        return {"error": "playwright not installed"}
    from playwright.sync_api import sync_playwright
    done = []
    try:
        with sync_playwright() as pw:
            b, ctx, page = _page(pw)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                for s in steps[:25]:
                    if "goto" in s:
                        page.goto(s["goto"], wait_until="domcontentloaded", timeout=45000)
                    elif "click" in s:
                        page.click(s["click"], timeout=15000)
                    elif "fill" in s:
                        page.fill(s["fill"], vault.fill(str(s.get("value", ""))),
                                  timeout=15000)
                    elif "press" in s:
                        page.keyboard.press(s["press"])
                    elif "wait" in s:
                        page.wait_for_timeout(min(int(s["wait"]), 10000))
                    done.append({k: ("***" if k == "fill" else v) for k, v in s.items()})
                    page.wait_for_timeout(400)
                return {"url": page.url, "title": page.title(),
                        "steps_done": done, "text": page.inner_text("body")[:max_chars]}
            finally:
                ctx.storage_state(path=STATE)
                b.close()
    except Exception as e:
        return {"error": f"browser act failed: {e}", "steps_done": done}
