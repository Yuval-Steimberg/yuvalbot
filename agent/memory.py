"""
Git-backed markdown memory — the Instinct-style memory layer.

Design (mirrors the publicly reported Instinct architecture):
  • one markdown file per record, in a typed directory
  • frontmatter with aliases + links → a loose graph, not a vector index
  • retrieval is keyword/grep style over title, aliases and body
  • every write is a git commit, so corrections keep their history
Deliberate deviation: query expansion (agent/search_expand) to blunt the
"semantically similar phrasing misses the record" failure mode of pure grep.
"""

import os, re, json, subprocess, logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("yuvalbot.memory")

ROOT = Path(os.environ.get("MEMORY_DIR", "memory")).resolve()

KINDS = [
    "people",          # humans and their context
    "organizations",   # companies, schools, vendors
    "facts",           # durable, slow-changing truths
    "preferences",     # likes, dislikes, defaults
    "decisions",       # what was decided and why
    "communications",  # threads, messages, commitments made
    "timelines",       # dated sequences of events
    "workstreams",     # open loops currently being worked
]
INBOX = "_inbox"       # raw capture, digested by the consolidation pass

# Unicode, not ASCII: this agent's owner writes in Hebrew, and an ASCII-only
# tokenizer silently finds nothing rather than failing loudly.
_SLUG = re.compile(r"[^\w]+", re.UNICODE)
_WORD = re.compile(r"[\w']+", re.UNICODE)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text: str) -> str:
    return _SLUG.sub("-", (text or "").lower()).strip("-")[:60] or "untitled"


# ─── git ──────────────────────────────────────────────────────────────────────

def _git(*args, check=False):
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args],
                              capture_output=True, text=True, timeout=30,
                              check=check)
    except Exception as e:                       # pragma: no cover
        log.error(f"git {args[0]} failed: {e}")
        return None


def init():
    """Create the memory tree and its own git repo (idempotent)."""
    for k in KINDS + [INBOX]:
        (ROOT / k).mkdir(parents=True, exist_ok=True)
        keep = ROOT / k / ".gitkeep"
        if not keep.exists():
            keep.write_text("")
    if not (ROOT / ".git").exists():
        _git("init", "-q")
        _git("config", "user.name", os.environ.get("MEMORY_GIT_NAME", "yuvalbot"))
        _git("config", "user.email", os.environ.get("MEMORY_GIT_EMAIL", "bot@yuval.local"))
        commit("memory: init")
    log.info(f"🧠 memory at {ROOT}")


def commit(message: str):
    _git("add", "-A")
    r = _git("commit", "-q", "-m", message)
    if r and r.returncode not in (0, 1):
        log.error(f"memory commit failed: {r.stderr.strip()}")
    remote = os.environ.get("MEMORY_GIT_REMOTE", "")
    if remote:
        _git("push", remote, "HEAD:main")


def history(rid: str, limit: int = 10) -> str:
    r = _git("log", f"-{limit}", "--pretty=%h %ad %s", "--date=short", "--", f"{rid}.md")
    return (r.stdout if r else "") or "(no history)"


# ─── records ──────────────────────────────────────────────────────────────────

def _parse(text: str):
    """Return (meta dict, body str). Frontmatter is a flat key: value block."""
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                v = v.strip()
                if v.startswith("[") and v.endswith("]"):
                    meta[k.strip()] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
                else:
                    meta[k.strip()] = v
            body = text[end + 4:].lstrip("\n")
    return meta, body


def _render(meta: dict, body: str) -> str:
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: [{', '.join(v)}]" if isinstance(v, list) else f"{k}: {v}")
    lines += ["---", "", body.strip(), ""]
    return "\n".join(lines)


def path_of(rid: str) -> Path:
    p = (ROOT / f"{rid}.md").resolve()
    if ROOT not in p.parents:
        raise ValueError(f"record id escapes memory root: {rid}")
    return p


def read(rid: str) -> dict | None:
    p = path_of(rid)
    if not p.exists():
        return None
    meta, body = _parse(p.read_text())
    return {"id": rid, "meta": meta, "body": body,
            "text": f"{meta.get('title', rid)}\n\n{body}"}


def write(kind: str, title: str, body: str, aliases=None, links=None,
          mode: str = "upsert", rid: str | None = None) -> dict:
    """mode: upsert (replace body) | append (add a dated section)."""
    if kind not in KINDS + [INBOX]:
        raise ValueError(f"unknown memory kind '{kind}' (use {', '.join(KINDS)})")
    rid = rid or f"{kind}/{slug(title)}"
    p = path_of(rid)
    p.parent.mkdir(parents=True, exist_ok=True)

    old_meta, old_body = ({}, "")
    if p.exists():
        old_meta, old_body = _parse(p.read_text())

    meta = {
        "id": rid,
        "title": title or old_meta.get("title", rid),
        "type": kind,
        "aliases": sorted(set((old_meta.get("aliases") or []) + (aliases or []))),
        "links": sorted(set((old_meta.get("links") or []) + (links or []))),
        "created": old_meta.get("created", now()),
        "updated": now(),
        "status": old_meta.get("status", "active"),
    }
    new_body = f"{old_body.rstrip()}\n\n## {now()[:10]}\n{body.strip()}" \
        if mode == "append" and old_body else body
    p.write_text(_render(meta, new_body))
    commit(f"memory: {'append' if p.exists() and mode == 'append' else 'write'} {rid}")
    return {"id": rid, "path": str(p.relative_to(ROOT)), "updated": meta["updated"]}


def archive(rid: str, reason: str = "") -> dict:
    rec = read(rid)
    if not rec:
        return {"error": f"no record {rid}"}
    rec["meta"]["status"] = "archived"
    rec["meta"]["updated"] = now()
    body = rec["body"] + (f"\n\n> archived {now()[:10]}: {reason}" if reason else "")
    path_of(rid).write_text(_render(rec["meta"], body))
    commit(f"memory: archive {rid} — {reason[:60]}")
    return {"id": rid, "status": "archived"}


def all_records(kind: str | None = None):
    kinds = [kind] if kind else KINDS + [INBOX]
    for k in kinds:
        for p in sorted((ROOT / k).glob("*.md")):
            meta, body = _parse(p.read_text())
            yield {"id": f"{k}/{p.stem}", "meta": meta, "body": body}


# ─── retrieval ────────────────────────────────────────────────────────────────

STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is",
        "are", "was", "my", "me", "i", "it", "that", "what", "do", "does",
        "he", "she", "they", "his", "her", "their", "with", "about", "any",
        "של", "את", "עם", "אני", "מה", "זה", "הוא", "היא", "יש", "לי", "על"}


def _terms(query: str):
    return [t for t in _WORD.findall((query or "").lower())
            if t not in STOP and len(t) > 1]


def search(query: str, kind: str | None = None, limit: int = 8,
           extra_terms: list[str] | None = None) -> list[dict]:
    """Grep-style scoring over title (×5), aliases (×4), links (×2), body (×1)."""
    terms = _terms(query) + [t.lower() for t in (extra_terms or [])]
    if not terms:
        return []
    pats = {t: re.compile(rf"\b{re.escape(t)}", re.I) for t in set(terms)}
    hits = []
    for rec in all_records(kind):
        meta, body = rec["meta"], rec["body"]
        title = (meta.get("title") or rec["id"]).lower()
        aliases = " ".join(meta.get("aliases") or []).lower()
        links = " ".join(meta.get("links") or []).lower()
        low = body.lower()
        score, matched = 0, []
        for t, pat in pats.items():
            n = len(pat.findall(low))
            s = (5 * len(pat.findall(title)) + 4 * len(pat.findall(aliases))
                 + 2 * len(pat.findall(links)) + min(n, 5))
            if s:
                score += s
                matched.append(t)
        if meta.get("status") == "archived":
            score = score // 2
        if score:
            i = min((low.find(t) for t in matched if low.find(t) >= 0), default=0)
            hits.append({"id": rec["id"], "title": meta.get("title", rec["id"]),
                         "type": meta.get("type", ""), "score": score,
                         "matched": matched, "updated": meta.get("updated", ""),
                         "snippet": body[max(0, i - 120): i + 280].strip()})
    return sorted(hits, key=lambda h: (-h["score"], h["id"]))[:limit]


def stats() -> dict:
    out = {k: len(list((ROOT / k).glob("*.md"))) for k in KINDS + [INBOX]}
    r = _git("rev-list", "--count", "HEAD")
    out["commits"] = int(r.stdout.strip()) if r and r.stdout.strip().isdigit() else 0
    return out


def capture(source: str, text: str) -> dict:
    """Drop raw material in the inbox for the daily consolidation pass."""
    return write(INBOX, f"{source} {now()}", text, aliases=[source],
                 rid=f"{INBOX}/{slug(source)}-{now().replace(':', '')}")
