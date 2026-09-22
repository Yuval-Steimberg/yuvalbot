"""Find the human at a company, from the owner's own correspondence.

Writing to info@ is what you do when you have not looked. The person who has
been answering for years is in the mailbox already.
"""

import logging, re
from collections import defaultdict

from . import google

log = logging.getLogger("yuvalbot.contacts")

_ADDR = re.compile(r"([^<>,;\s]+@[^<>,;\s]+)")
_NAME = re.compile(r'^\s*"?([^"<]+?)"?\s*<')


def _parse(header: str) -> tuple[str, str]:
    addr = (_ADDR.search(header or "") or [None, ""])[1] if _ADDR.search(header or "") \
        else ""
    name = (_NAME.match(header or "").group(1).strip()
            if _NAME.match(header or "") else "")
    return name, addr.lower().strip("<>")


def find(organisation: str, limit: int = 5) -> dict:
    """People from that organisation who have actually corresponded, ranked."""
    if not google.configured():
        return {"error": "the native Google connection is not set up",
                "do_instead": "search the mail with whatever mail tool you have for "
                              f"'{organisation}' and read the senders yourself"}
    org = organisation.strip()
    queries = [f'from:({org})', f'to:({org})', f'"{org}"']
    people = defaultdict(lambda: {"name": "", "messages": 0, "last": "",
                                  "subjects": []})
    seen_any = False
    for q in queries:
        try:
            ids = google.list_ids(q, "", 25)["ids"]
        except Exception as e:
            log.info(f"contact search '{q}' failed: {e}")
            continue
        if not ids:
            continue
        seen_any = True
        for h in google.headers_of(ids[:25]):
            for header in (h.get("from", ""), h.get("to", "")):
                name, addr = _parse(header)
                if not addr or addr.endswith(("@gmail.com", "@googlemail.com")):
                    continue                      # the owner's own side of it
                p = people[addr]
                p["name"] = p["name"] or name
                p["messages"] += 1
                p["last"] = max(p["last"], h.get("date", ""))
                if h.get("subject") and len(p["subjects"]) < 3:
                    p["subjects"].append(h["subject"][:90])

    ranked = sorted(({"email": a, **v} for a, v in people.items()),
                    key=lambda p: (-p["messages"], p["last"]), reverse=False)[:limit]
    if not ranked:
        return {"organisation": org, "people": [],
                "searched": queries,
                "note": ("nothing in the mailbox from them" if seen_any else
                         "no messages matched — try the company's domain instead")}
    return {"organisation": org, "people": ranked,
            "note": "write to the person with the most recent, most frequent "
                    "correspondence, not to a generic address"}
