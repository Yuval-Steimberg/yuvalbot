"""A browser you can drive from your phone, that stays logged in.

Sites without an API — Airbnb, an airline, a municipality, a bank — are reachable
only by actually using them. Handing the agent a password is the wrong answer:
it breaks on two-factor, and it means storing a credential that can do anything.

Instead this keeps one Chromium alive with a saved session. You open a page,
sign in yourself with your own fingers, and the cookies persist. From then on the
agent can act on that site through browser_act, without ever knowing the password.

Playwright's sync API is not thread-safe, so everything runs on one worker thread
and callers post commands to it.
"""

import base64, logging, queue, threading, time
from pathlib import Path

from . import config

log = logging.getLogger("yuvalbot.livebrowser")

IDLE_SECONDS = int(__import__("os").environ.get("BROWSER_IDLE_SECONDS", "900"))
VIEWPORT = {"width": 1100, "height": 1600}          # portrait: it is used on a phone


class _Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.jobs: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.error = ""
        self._last_used = time.time()
        self._pw = self._browser = self._ctx = self._page = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def run(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.error = "playwright is not installed in this image"
            self.ready.set()
            return
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            state = Path(config.BROWSER_STATE)
            self._ctx = self._browser.new_context(
                storage_state=str(state) if state.exists() else None,
                viewport=VIEWPORT, locale="he-IL",
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0 Safari/537.36"))
            self._page = self._ctx.new_page()
            self.error = ""
        except Exception as e:
            self.error = f"could not start the browser: {e}"
            self.ready.set()
            return
        self.ready.set()
        log.info("🌐 live browser ready")

        while True:
            try:
                fn, args, box = self.jobs.get(timeout=30)
            except queue.Empty:
                if time.time() - self._last_used > IDLE_SECONDS:
                    self._shutdown()
                    return
                continue
            self._last_used = time.time()
            try:
                box["result"] = fn(self, *args)
            except Exception as e:
                box["result"] = {"error": str(e)[:300]}
            box["done"].set()

    def _shutdown(self):
        try:
            self.save_state()
            self._ctx.close(); self._browser.close(); self._pw.stop()
            log.info("🌐 live browser closed after idle")
        except Exception:
            pass

    def call(self, fn, *args, timeout: int = 60):
        self.ready.wait(timeout=90)
        if self.error:
            return {"error": self.error}
        box = {"done": threading.Event(), "result": None}
        self.jobs.put((fn, args, box))
        if not box["done"].wait(timeout=timeout):
            return {"error": "the browser did not answer in time"}
        return box["result"]

    # ── the operations themselves, all run on the worker thread ──────────────

    def goto(self, url: str):
        if not url.startswith("http"):
            url = "https://" + url
        self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
        return {"url": self._page.url, "title": self._page.title()}

    def shot(self):
        png = self._page.screenshot(type="jpeg", quality=55, full_page=False)
        return {"image": base64.b64encode(png).decode(), "url": self._page.url,
                "title": self._page.title(), "viewport": VIEWPORT}

    def click(self, x: float, y: float):
        self._page.mouse.click(x, y)
        self._page.wait_for_timeout(600)
        return {"ok": True, "url": self._page.url}

    def type_text(self, text: str):
        self._page.keyboard.type(text, delay=25)
        return {"ok": True}

    def press(self, key: str):
        self._page.keyboard.press(key)
        self._page.wait_for_timeout(600)
        return {"ok": True, "url": self._page.url}

    def scroll(self, dy: int):
        self._page.mouse.wheel(0, dy)
        self._page.wait_for_timeout(300)
        return {"ok": True}

    def back(self):
        self._page.go_back(wait_until="domcontentloaded", timeout=30000)
        return {"ok": True, "url": self._page.url}

    def save_state(self):
        Path(config.BROWSER_STATE).parent.mkdir(parents=True, exist_ok=True)
        self._ctx.storage_state(path=str(config.BROWSER_STATE))
        hosts = sorted({c["domain"].lstrip(".")
                        for c in self._ctx.cookies()})[:40]
        return {"saved": True, "signed_in_to": hosts}

    def text(self, max_chars: int = 4000):
        return {"url": self._page.url, "title": self._page.title(),
                "text": self._page.inner_text("body")[:max_chars]}


_worker: _Worker | None = None
_lock = threading.Lock()


def worker() -> _Worker:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = _Worker()
            _worker.start()
        return _worker


def available() -> bool:
    try:
        import playwright                                    # noqa: F401
        return True
    except ImportError:
        return False


def sessions() -> list[str]:
    """Which sites this browser is already signed in to."""
    state = Path(config.BROWSER_STATE)
    if not state.exists():
        return []
    try:
        import json
        data = json.loads(state.read_text())
        return sorted({c["domain"].lstrip(".") for c in data.get("cookies", [])})
    except Exception:
        return []
