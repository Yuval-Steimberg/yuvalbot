"""Thin Anthropic Messages API client (requests only — no extra deps)."""

import os, json, logging, requests

log = logging.getLogger("yuvalbot.llm")

API = "https://api.anthropic.com/v1/messages"
BIG = os.environ.get("AGENT_MODEL", "claude-sonnet-5")
SMALL = os.environ.get("AGENT_FAST_MODEL", "claude-haiku-4-5-20251001")


class LLMError(RuntimeError):
    pass


def call(messages, system="", tools=None, model=None, max_tokens=4000, timeout=120):
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise LLMError("ANTHROPIC_API_KEY not set")
    payload = {"model": model or BIG, "max_tokens": max_tokens, "messages": messages}
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = tools
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    # An org-level key (not created inside a workspace) is rejected unless the
    # request names the workspace to bill and scope it to.
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    if workspace:
        headers["anthropic-workspace-id"] = workspace

    r = requests.post(API, headers=headers, json=payload, timeout=timeout)
    if r.status_code != 200:
        detail = r.text[:400]
        if "anthropic-workspace-id" in detail:
            detail += ("  →  This key is not scoped to a workspace. Either create "
                       "a new key inside a workspace in the Anthropic Console, or "
                       "set ANTHROPIC_WORKSPACE_ID (Console > Settings > "
                       "Workspaces, an id like wrkspc_...).")
        raise LLMError(f"{r.status_code}: {detail}")
    return r.json()


def text(prompt, system="", model=None, max_tokens=1500) -> str:
    resp = call([{"role": "user", "content": prompt}], system=system,
                model=model or SMALL, max_tokens=max_tokens)
    return "".join(b.get("text", "") for b in resp.get("content", []))


def json_obj(prompt, system="", model=None, max_tokens=2000, default=None):
    raw = text(prompt, system=system, model=model, max_tokens=max_tokens).strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return default
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        log.warning("model returned unparseable JSON")
        return default
