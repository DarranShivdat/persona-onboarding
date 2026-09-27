"""Slot validators — pure functions, no I/O.

Each validator maps a raw extracted value to a `Validation`:
  - ``ok``        -> the slot may be `filled` with ``value`` (normalized)
  - ``confirm``   -> store as `candidate`; the user must affirm before it is filled
  - ``candidate`` -> store as `candidate`; only an out-of-band event can fill it (gmail)
  - ``unsure``    -> not a value; the user doesn't know yet (need -> unsure_need)
  - ``reject``    -> discard; ``reason`` tells the phrasing layer why
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

Outcome = Literal["ok", "confirm", "candidate", "unsure", "reject"]

# Voice STT confidence below this needs a spell-back before a name is accepted.
VOICE_NAME_MIN_CONFIDENCE = 0.8
# Agent names longer than this are allowed but confirmed ("very long").
AGENT_NAME_CONFIRM_LEN = 24
AGENT_NAME_MAX_LEN = 40
PERSON_NAME_MAX_LEN = 50
PERSON_NAME_MAX_WORDS = 5


@dataclass(frozen=True)
class Validation:
    outcome: Outcome
    value: Optional[str] = None
    reason: Optional[str] = None


# Deliberately small, word-boundary lists. The LLM does nuanced moderation upstream;
# these are the code-side floor so a bad name can never be *filled* silently.
_ABUSIVE = re.compile(
    r"\b(nazi|hitler|kkk|kys|kill\s*yourself|retard(ed)?|fag(got)?s?|n[i1]gg(er|a)s?)\b",
    re.IGNORECASE,
)
_PROFANE_OR_JOKE = re.compile(
    r"(\b(fuck\w*|shit\w*|bitch\w*|ass|asshole|dick\w*|cock\w*|piss\w*|crap\w*|damn|poop\w*|fart\w*|boob\w*|"
    r"butt\w*|turd\w*|weiner|wiener|sexy|69|420)\b|mcgee|mcface)",
    re.IGNORECASE,
)
_PERSON_NAME = re.compile(r"^[^\W\d_]+(?:[ '\-.’][^\W\d_]*)*$", re.UNICODE)
_SPELLED = re.compile(r"^(?:[^\W\d_][\s\-.,]+)+[^\W\d_]$", re.UNICODE)
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_UNSURE_NEED = {
    "idk", "i dont know", "i don't know", "i do not know", "dunno", "not sure", "no idea",
    "unsure", "nothing", "no clue", "i'm not sure", "im not sure", "?", "no", "none",
    "anything", "whatever",
}


def _clean(raw: Optional[str]) -> str:
    return (raw or "").strip().strip("\"'“”‘’`").strip()


def agent_name(raw: Optional[str], *, channel: str = "text", confidence: Optional[float] = None) -> Validation:
    v = _clean(raw)
    if not v:
        return Validation("reject", reason="empty")
    if len(v) > AGENT_NAME_MAX_LEN:
        return Validation("reject", reason="too_long")
    if _ABUSIVE.search(v):
        return Validation("reject", reason="abusive")
    if _PROFANE_OR_JOKE.search(v):
        return Validation("confirm", v, reason="joke_or_profane")
    if len(v) > AGENT_NAME_CONFIRM_LEN:
        return Validation("confirm", v, reason="very_long")
    return Validation("ok", v)


def _unspell(v: str) -> Optional[str]:
    """"S-I-O-B-H-A-N" / "s i o b h a n" -> "Siobhan"; None if not a spelled form."""
    if not _SPELLED.match(v):
        return None
    letters = re.sub(r"[\s\-.,]+", "", v)
    return letters[:1].upper() + letters[1:].lower()


def person_name(raw: Optional[str], *, channel: str = "text", confidence: Optional[float] = None) -> Validation:
    v = re.sub(r"\s+", " ", _clean(raw))
    if not v:
        return Validation("reject", reason="empty")
    spelled = _unspell(v)
    if spelled is not None:
        # A spelled name *is* the spell-back: accept regardless of STT confidence.
        return Validation("ok", spelled)
    if len(v) > PERSON_NAME_MAX_LEN or len(v.split(" ")) > PERSON_NAME_MAX_WORDS:
        return Validation("reject", reason="too_long")
    if not _PERSON_NAME.match(v):
        return Validation("reject", reason="charset")
    if _ABUSIVE.search(v):
        return Validation("reject", reason="abusive")
    if channel == "voice" and confidence is not None and confidence < VOICE_NAME_MIN_CONFIDENCE:
        return Validation("confirm", v, reason="low_confidence_spell_back")
    return Validation("ok", v)


def need(raw: Optional[str], *, channel: str = "text", confidence: Optional[float] = None) -> Validation:
    v = re.sub(r"\s+", " ", _clean(raw))
    if not v:
        return Validation("reject", reason="empty")
    if v.lower().rstrip(".!") in _UNSURE_NEED:
        return Validation("unsure", reason="unsure_need")
    if len(v) < 3:
        return Validation("reject", reason="too_short")
    return Validation("ok", v)


def gmail_oauth(
    raw: Optional[str], *, channel: str = "text", confidence: Optional[float] = None, oauth_verified: bool = False
) -> Validation:
    v = _clean(raw).lower().replace(" ", "")
    if not _EMAIL.match(v):
        return Validation("reject", reason="not_an_email")
    if oauth_verified:
        return Validation("ok", v)
    # Typed or spoken: a candidate for the Connect Gmail step, never "connected".
    return Validation("candidate", v, reason="needs_oauth")


VALIDATORS = {
    "agent_name": agent_name,
    "person_name": person_name,
    "need": need,
    "gmail_oauth": gmail_oauth,
}
