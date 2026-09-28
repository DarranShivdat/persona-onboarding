"""Output guard: runs on every phrased line before it is rendered or spoken.

Drops (never rewrites) any sentence that
  - mentions a tool / function name,
  - looks like JSON or code (braces, "key":, snake_case identifiers),
  - is or contains a stage direction / speaker label / markup tag,
  - makes a claim not backed by the approved facts or the turn's plan/state
    (prices, certifications, encryption, retention/sharing, dates/numbers, "connected"
    when Gmail is not, pretending to see the inbox or to have acted).

The caller falls back to a templated line if nothing survives. Deliberately
conservative: a dropped good sentence costs a templated re-ask; a leaked bad one
reaches a reviewer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ..brain.spec import TOOL_REGISTRY

MAX_SENTENCES = 3

TOOL_NAMES = sorted(set(TOOL_REGISTRY) | {"record_slots"})
_TOOL = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in TOOL_NAMES) + r")\b"
    r"|\b(tool|function)\s+(call|use)s?\b|\bcalling (the|a) (tool|function)\b",
    re.I,
)
_JSON = re.compile(r"[{}\[\]]|\"\w+\"\s*:|\b[a-z]+_[a-z0-9_]+\b|```")
_STAGE = re.compile(
    r"\*[^*]+\*"                                   # *smiles*
    r"|\((?:pause|pauses|beat|laughs?|chuckles?|smiles?|sighs?|warmly|cheerfully)[^)]*\)"
    r"|\[[A-Za-z][A-Za-z ,'-]*\]"                   # [laughs], [pause]
    r"|<[^>]+>"                                    # <thinking>, SSML-ish tags
    r"|^\s*(assistant|persona|agent|user|system|note|narrator)\s*:",
    re.I,
)
# Sensitive claim families (one family per claim, so approving one never approves
# another): allowed only if the same pattern also matches the approved-facts corpus (i.e. the claim is one Darran signed off on).
_CLAIMS: list[tuple[str, re.Pattern]] = [
    ("price", re.compile(r"\$|\bprices?\b|\bpricing\b|\bcosts?\b|\bfree\b|\bsubscriptions?\b|\bper month\b", re.I)),
    ("certification", re.compile(r"\bSOC\s?2\b|\bHIPAA\b|\bGDPR\b|\bISO\s?27001\b|\bcertifi\w*|\bcompliant\b", re.I)),
    ("encryption", re.compile(r"\bencrypt\w*", re.I)),
    ("end_to_end", re.compile(r"\bend-to-end\b|\bzero[- ]knowledge\b", re.I)),
    ("retention", re.compile(r"\bretain\w*|\bretention\b|\b(keep|store|hold)s?\b[^.]*\bfor (a|an|one|\d+|\w+ (days|weeks|months|years))\b", re.I)),
    ("selling", re.compile(r"\bsells?\b|\bselling\b|\bsold\b", re.I)),
    ("sharing", re.compile(r"\bshar(e|es|ing)\s+(your|it|them|with)\b|\bthird[- ]part(y|ies)\b", re.I)),
    ("deletion", re.compile(r"\bdelet\w*", re.I)),
    ("dates", re.compile(r"\bships?\b|\bshipping\b|\blaunch\w*|\breleas\w*|\bpre-?order\w*", re.I)),
    ("funding", re.compile(r"\bfund(ing|ed|raise)\b|\braised\b|\binvestors?\b|\bvaluation\b", re.I)),
    ("guarantee", re.compile(r"\bguarantee\w*|\b100\s?%|\bperfectly secure\b|\bunhackable\b", re.I)),
]
_DIGITS = re.compile(r"\d+")
# Onboarding can't see the inbox or act yet; any "I see / I've sent" is fabricated.
_FABRICATED = re.compile(
    r"\bI\s+(can\s+)?(see|noticed?|found)\b.*\b(inbox|emails?|messages?|calendar|meetings?)\b"
    r"|\byou (have|'ve got|got) \w+ (unread|new) (emails?|messages?)\b"
    r"|\bI(?:'ve| have)\s+(already\s+)?(sent|scheduled|booked|archived|drafted|emailed|replied|labell?ed|organi[sz]ed|deleted)\b",
    re.I,
)
_CONNECTED = re.compile(
    r"\b(gmail|inbox|email|account)\b.*\b(is|are|'s|now|all)\s+(now\s+)?(connected|linked|hooked up)\b"
    r"|\b(connected|linked) (your|the) (gmail|inbox|email|account)\b"
    r"|\byou're (now )?(connected|linked)\b",
    re.I,
)
# HONEST-001 (live 2026-09-28): a model line never claims or denies that the assistant can do
# the user's thing ("I can help with that", "I can't do that"). Fixed wording, not a judgment
# of what it can do: any such sentence is dropped and the templated line is used instead.
_CAPABILITY = re.compile(
    r"\b(?!you\b)(\w+)\s*(can(not|'t|’t)?|could(n't|n’t)?|will|'ll|’ll|won't|won’t)\s+"
    r"(definitely\s+|totally\s+|absolutely\s+|easily\s+)?(help|do|handle|manage|take care of|sort)\s+"
    r"(you\s+)?(with\s+)?(that|this|it|those|these)\b", re.I)
_CONDITIONAL = re.compile(r"\b(once|when|after|if|until|as soon as)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class GuardResult:
    text: str
    kept: list[str] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)   # (sentence, reason)


def check(sentence: str, *, allowed: str = "", gmail_connected: bool = False) -> str | None:
    """Reason to drop `sentence`, or None if it may be said."""
    if _TOOL.search(sentence):
        return "tool"
    if _STAGE.search(sentence):
        return "stage_direction"
    if _JSON.search(sentence):
        return "json"
    if _FABRICATED.search(sentence):
        return "claim:fabricated_action"
    if _CAPABILITY.search(sentence):
        return "claim:capability"
    if not gmail_connected and _CONNECTED.search(sentence) and not _CONDITIONAL.search(sentence):
        return "claim:gmail_connected"
    for name, pat in _CLAIMS:
        if pat.search(sentence) and not pat.search(allowed):
            return f"claim:{name}"
    for d in _DIGITS.findall(sentence):
        if d not in allowed:
            return "claim:number"
    return None


def sentences(text: str) -> Iterable[str]:
    for line in text.splitlines():
        line = line.strip().strip("-•").strip().replace("**", "")
        if not line:
            continue
        for s in _SENTENCE.split(line):
            if s.strip():
                yield s.strip()


def guard(text: str, *, allowed: str = "", gmail_connected: bool = False,
          max_sentences: int = MAX_SENTENCES) -> GuardResult:
    res = GuardResult(text="")
    for s in sentences(text or ""):
        reason = check(s, allowed=allowed, gmail_connected=gmail_connected)
        if reason is None and len(res.kept) >= max_sentences:
            reason = "too_long"
        if reason:
            res.dropped.append((s, reason))
        else:
            res.kept.append(s)
    res.text = " ".join(res.kept)
    return res
