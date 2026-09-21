"""Web search and page reading.

Search provider order: Brave → Serper → DuckDuckGo HTML (no key, best effort).
Page reading strips scripts/styles and returns text, which is what the model
actually needs; use browser.py when a page requires JavaScript or a login.
"""

import os, re, html, logging, requests

log = logging.getLogger("yuvalbot.web")
UA = {"User-Agent": "Mozilla/5.0 (compatible; yuvalbot/1.0)"}


def search(query: str, limit: int = 6) -> dict:
    brave, serper = os.environ.get("BRAVE_API_KEY"), os.environ.get("SERPER_API_KEY")
    try:
        if brave:
            r = requests.get("https://api.search.brave.com/res/v1/web/search",
                             headers={"X-Subscription-Token": brave,
                                      "Accept": "application/json"},
                             params={"q": query, "count": limit}, timeout=25)
            r.raise_for_status()
            return {"provider": "brave", "results": [
                {"title": w.get("title"), "url": w.get("url"),
                 "snippet": re.sub("<[^>]+>", "", w.get("description", ""))}
                for w in r.json().get("web", {}).get("results", [])[:limit]]}
        if serper:
            r = requests.post("https://google.serper.dev/search",
                              headers={"X-API-KEY": serper},
                              json={"q": query, "num": limit}, timeout=25)
            r.raise_for_status()
            return {"provider": "serper", "results": [
                {"title": o.get("title"), "url": o.get("link"),
                 "snippet": o.get("snippet")}
                for o in r.json().get("organic", [])[:limit]]}
        r = requests.get("https://duckduckgo.com/html/", params={"q": query},
                         headers=UA, timeout=25)
        r.raise_for_status()
        out = []
        for m in re.finditer(r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text):
            out.append({"title": html.unescape(re.sub("<[^>]+>", "", m.group(2))),
                        "url": html.unescape(m.group(1)), "snippet": ""})
            if len(out) >= limit:
                break
        return {"provider": "duckduckgo", "results": out}
    except Exception as e:
        return {"error": f"search failed: {e}",
                "hint": "set BRAVE_API_KEY or SERPER_API_KEY for reliable search"}


def fetch(url: str, max_chars: int = 6000) -> dict:
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
    except Exception as e:
        return {"error": f"fetch failed: {e}"}
    body = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", r.text)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    text = re.sub(r"\s{2,}", " ", html.unescape(body)).strip()
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", r.text)
    return {"url": url, "title": html.unescape(title.group(1)).strip() if title else "",
            "text": text[:max_chars], "truncated": len(text) > max_chars}
