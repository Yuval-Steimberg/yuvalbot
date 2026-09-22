"""Sign into a site on the owner's behalf, using credentials they stored.

The owner should not have to drive a browser on their phone. If they have put a
username and password in the vault, the agent does the signing in itself and
only comes back for what genuinely needs a human: a verification code.

Selectors are deliberately generic. Login forms differ, so every step reports
what it actually found rather than claiming success.
"""

import logging, re

from . import livebrowser, vault

log = logging.getLogger("yuvalbot.signin")

LOGIN_URLS = {
    "airbnb.com": "https://www.airbnb.com/login",
    "booking.com": "https://account.booking.com/sign-in",
    "linkedin.com": "https://www.linkedin.com/login",
    "amazon.com": "https://www.amazon.com/ap/signin",
    "ebay.com": "https://signin.ebay.com",
}

USER_SELECTORS = ("input[type=email]", "input[name=email]", "input[id*=email i]",
                  "input[name=username]", "input[id*=user i]", "input[type=tel]")
PASS_SELECTORS = ("input[type=password]", "input[name=password]",
                  "input[id*=password i]")
SUBMIT_SELECTORS = ("button[type=submit]", "input[type=submit]",
                    "button:has-text('Continue')", "button:has-text('Log in')",
                    "button:has-text('Sign in')", "button:has-text('המשך')",
                    "button:has-text('התחבר')")
CODE_HINTS = ("verification code", "confirmation code", "one-time", "6-digit",
              "enter the code", "קוד אימות", "קוד אישור")


def _slug(site: str) -> str:
    """airbnb.com -> AIRBNB, so the vault entry is the name a person would pick."""
    host = re.sub(r"^https?://", "", site or "").split("/")[0].lower()
    host = host.replace("www.", "")
    label = host.split(".")[0] if "." in host else host
    return re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_")


def credentials(site: str) -> tuple[str, str, list[str]]:
    """Returns (user, password, the names we looked for)."""
    base = _slug(site)
    user_names = [f"{base}_EMAIL", f"{base}_USERNAME", f"{base}_USER"]
    pass_names = [f"{base}_PASSWORD", f"{base}_PASS"]
    user = next((vault.get(n) for n in user_names if vault.get(n)), "")
    pw = next((vault.get(n) for n in pass_names if vault.get(n)), "")
    return user, pw, user_names[:1] + pass_names[:1]


def _first(page, selectors):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return el, sel
        except Exception:
            continue
    return None, ""


def _login_url(site: str) -> str:
    host = re.sub(r"^https?://", "", site or "").split("/")[0].lower().replace("www.", "")
    return LOGIN_URLS.get(host, "")


def _sign_in(worker, site: str, user: str, pw: str):
    page = worker._page
    url = _login_url(site)
    page.goto(url or ("https://" + site.replace("https://", "")),
              wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)

    el, sel = _first(page, USER_SELECTORS)
    if not el:
        return {"ok": False, "stage": "no login form found",
                "url": page.url,
                "text": page.inner_text("body")[:600]}
    el.fill(user)
    pwel, _ = _first(page, PASS_SELECTORS)
    if not pwel:                                  # two-step forms: continue first
        btn, _ = _first(page, SUBMIT_SELECTORS)
        if btn:
            btn.click()
            page.wait_for_timeout(2500)
        pwel, _ = _first(page, PASS_SELECTORS)
    if not pwel:
        return {"ok": False, "stage": "no password field appeared",
                "url": page.url, "text": page.inner_text("body")[:600]}
    pwel.fill(pw)
    btn, _ = _first(page, SUBMIT_SELECTORS)
    if btn:
        btn.click()
    page.wait_for_timeout(5000)

    body = page.inner_text("body")[:2000]
    low = body.lower()
    if any(h in low for h in CODE_HINTS):
        return {"ok": False, "stage": "needs a verification code",
                "url": page.url, "text": body[:400],
                "next": "ask the owner for the code, then browser_enter_code"}
    still_password, _ = _first(page, PASS_SELECTORS)
    if still_password:
        return {"ok": False, "stage": "still on the login page — wrong details, "
                                      "or the site blocked an automated sign-in",
                "url": page.url, "text": body[:400]}
    worker.save_state()
    return {"ok": True, "stage": "signed in", "url": page.url,
            "signed_in_to": livebrowser.sessions()[:10]}


def _enter_code(worker, code: str):
    page = worker._page
    el, _ = _first(page, ("input[autocomplete='one-time-code']", "input[type=tel]",
                          "input[name*=code i]", "input[id*=code i]",
                          "input[type=text]"))
    if not el:
        return {"ok": False, "stage": "no code field on this page",
                "url": page.url}
    el.fill(code)
    btn, _ = _first(page, SUBMIT_SELECTORS)
    if btn:
        btn.click()
    page.wait_for_timeout(5000)
    still, _ = _first(page, PASS_SELECTORS)
    body = page.inner_text("body")[:800]
    if still or any(h in body.lower() for h in CODE_HINTS):
        return {"ok": False, "stage": "the code was not accepted", "url": page.url,
                "text": body[:300]}
    worker.save_state()
    return {"ok": True, "stage": "signed in", "url": page.url}


def sign_in(site: str) -> dict:
    user, pw, wanted = credentials(site)
    if not (user and pw):
        return {"ok": False, "stage": "no credentials stored",
                "need": wanted,
                "next": "send vault_link so they can store these two, then try "
                        "again yourself — do not ask them to drive a browser"}
    if not livebrowser.available():
        return {"error": "no browser in this image"}
    return livebrowser.worker().call(_sign_in, site, user, pw, timeout=150)


def enter_code(code: str) -> dict:
    if not livebrowser.available():
        return {"error": "no browser in this image"}
    return livebrowser.worker().call(_enter_code, code, timeout=90)
