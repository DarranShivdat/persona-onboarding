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
# Typed names are authoritative; only unusual ones (NAME-001) are confirmed. flow.yaml's
# user_name.confirm_policy may override these defaults.
TEXT_NAME_MIN_CONFIDENCE = 0.8
TEXT_NAME_MAX_WORDS = 2
# Agent names longer than this are allowed but confirmed ("very long").
AGENT_NAME_CONFIRM_LEN = 24
AGENT_NAME_MAX_LEN = 40
PERSON_NAME_MAX_LEN = 50
PERSON_NAME_MAX_WORDS = 5
# Offered with the text agent_name ask (DQ-04 chips; copy.md A-01). The last one asks us to pick.
AGENT_NAME_SUGGESTIONS = ("Juno", "Atlas", "Surprise me")
# "Surprise me" / "you pick": code picks the name, so the chip (or the words) never become the name.
SURPRISE_AGENT_NAME = "Juno"
_PICK_FOR_ME = re.compile(
    r"^(surprise me|you (pick|choose|decide)( one| for me)?|pick (one|for me)|dealer'?s choice|up to you)[.!]*$",
    re.IGNORECASE,
)


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
    if _PICK_FOR_ME.match(re.sub(r"\s+", " ", v)):
        return Validation("ok", SURPRISE_AGENT_NAME)
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


_NATO_LETTER = {
    "alpha": "a", "alfa": "a", "bravo": "b", "charlie": "c", "delta": "d", "echo": "e", "foxtrot": "f",
    "golf": "g", "hotel": "h", "india": "i", "juliet": "j", "juliett": "j", "kilo": "k", "lima": "l",
    "mike": "m", "november": "n", "oscar": "o", "papa": "p", "quebec": "q", "romeo": "r", "sierra": "s",
    "tango": "t", "uniform": "u", "victor": "v", "whiskey": "w", "xray": "x", "x-ray": "x",
    "yankee": "y", "zulu": "z",
}
_REPEAT = {"double": 2, "triple": 3}
_AS_IN = {("as", "in"), ("like", "in"), ("as", "for")}
_JOINERS = {"and", "then"}
_COPULAS = {"it's", "its", "is", "s", "was", "that's", "thats"}
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)


def assemble_spelling(text: Optional[str]) -> Optional[str]:
    """Letters spoken one by one -> the name: "D as in David, A, double R, A, N" -> "Darran".

    Takes the longest run of letter items (single letters, NATO words, "double r",
    "d as in david" / "d for david"); fillers ("no", "it's", "spelled") end a run.
    None unless the run has at least two letters (so "I'm Sam" is never a spelling).
    """
    toks = [t.lower() for t in _WORD.findall((text or "").replace("x-ray", "xray"))]
    best: list[str] = []
    run: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        letter, width = None, 1
        if t in _REPEAT and i + 1 < len(toks) and len(toks[i + 1]) == 1:
            letter, width = toks[i + 1] * _REPEAT[t], 2
        elif len(t) == 1 and not (t == "a" and not run and i and toks[i - 1] in _COPULAS):
            letter = t
        elif t in _NATO_LETTER:
            letter = _NATO_LETTER[t]
        if letter is None:
            if not (run and t in _JOINERS):
                best, run = max(best, run, key=len), []
            i += 1
            continue
        run.append(letter)
        i += width
        # "D as in David" / "D for David": the example word only names the letter.
        if i + 2 < len(toks) and tuple(toks[i:i + 2]) in _AS_IN:
            i += 3
        elif i + 1 < len(toks) and toks[i] == "for" and len(toks[i + 1]) > 1:
            i += 2
    letters = "".join(max(best, run, key=len))
    if len(letters) < 2:
        return None
    return letters[:1].upper() + letters[1:]


def _looks_unusual(v: str, confidence: Optional[float], utterance: Optional[str],
                   min_conf: float, max_words: int) -> Optional[str]:
    """Why a *typed* name deserves a "did I get that right?" (None: take it as typed)."""
    if confidence is not None and confidence < min_conf:
        return "low_confidence"
    if len(v.split(" ")) > max_words:
        return "many_words"
    if _SENTENCE.search(v) or v.endswith("?"):
        return "sentence"
    if utterance and v.lower() not in re.sub(r"\s+", " ", utterance).lower():
        # The typed text is authoritative: a name the user didn't literally type was rewritten.
        return "not_as_typed"
    return None


_SENTENCE = re.compile(
    r"\b(i|i'm|im|me|my|is|am|are|the|and|you|your|name|call|it|this|that|not|just)\b", re.IGNORECASE)


def person_name(raw: Optional[str], *, channel: str = "text", confidence: Optional[float] = None,
                utterance: Optional[str] = None, confirm_policy: Optional[dict] = None) -> Validation:
    """Confirm policy (flow.yaml user_name.confirm_policy): voice `always` reads the name back
    (spelled) before it is filled; text `unusual` confirms only odd names. A spelled name
    *is* the spell-back and is accepted as is on either channel."""
    pol = confirm_policy or {}
    v = re.sub(r"\s+", " ", _clean(raw)).rstrip(".!")
    if not v:
        return Validation("reject", reason="empty")
    spelled = _unspell(v)
    if spelled is not None:
        return Validation("ok", spelled)
    if len(v) > PERSON_NAME_MAX_LEN or len(v.split(" ")) > PERSON_NAME_MAX_WORDS:
        return Validation("reject", reason="too_long")
    if not _PERSON_NAME.match(v):
        if re.search(r"[^\W\d_]", v) and not re.search(r"[<>{}\[\]\\/@#$%^*=|~]", v):
            # Letters with a stray digit/symbol ("Dar4an"): never filled silently.
            return Validation("confirm", v, reason="unusual_charset")
        return Validation("reject", reason="charset")
    if _ABUSIVE.search(v):
        return Validation("reject", reason="abusive")
    mode = pol.get(channel)
    if channel == "voice":
        if mode == "always":
            return Validation("confirm", v, reason="voice_read_back")
        if confidence is not None and confidence < VOICE_NAME_MIN_CONFIDENCE:
            return Validation("confirm", v, reason="low_confidence_spell_back")
        return Validation("ok", v)
    if mode == "unusual":
        why = _looks_unusual(v, confidence, utterance,
                             float(pol.get("text_min_confidence", TEXT_NAME_MIN_CONFIDENCE)),
                             int(pol.get("text_max_words", TEXT_NAME_MAX_WORDS)))
        if why:
            return Validation("confirm", v, reason=why)
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
