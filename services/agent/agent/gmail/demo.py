"""Read-only value demo: 1–3 recent inbox messages as safe metadata only.

Returns exactly {id, from, subject, snippet, date} per message — never bodies, attachments
or other headers. Never fabricates: an empty inbox returns [] and the phrasing layer says less.
"""
from __future__ import annotations

from .client import GmailClient

SNIPPET_MAX = 140
FIELDS = ("id", "from", "subject", "snippet", "date")


def value_demo(client: GmailClient, *, limit: int = 3) -> list[dict]:
    limit = max(1, min(3, limit))
    out = []
    for ref in client.list_messages(max_results=limit)[:limit]:
        msg = client.get_metadata(ref["id"])
        headers = {h.get("name", "").lower(): h.get("value", "") for h in (msg.get("payload") or {}).get("headers", [])}
        snippet = " ".join(str(msg.get("snippet") or "").split())
        if len(snippet) > SNIPPET_MAX:
            snippet = snippet[:SNIPPET_MAX - 1].rstrip() + "…"
        out.append({"id": str(msg.get("id") or ref["id"]), "from": headers.get("from", ""),
                    "subject": headers.get("subject", ""), "snippet": snippet, "date": headers.get("date", "")})
    return out
