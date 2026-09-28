"""Templated lines: critical content (readbacks, NATO email chunks, graduation
summary) and the fallback for every free-text line when the LLM fails or the guard
drops everything. These are constants — never LLM output — so they are not guarded.
"""
from __future__ import annotations

from typing import Optional

from ..brain.spec import FlowSpec
from ..brain.state import Channel, SessionState
from ..brain.validators import AGENT_NAME_SUGGESTIONS

ASK: dict[str, dict[str, str]] = {
    "agent_name": {"text": "What would you like to call me?"},
    "user_name": {"text": "What should I call you?", "voice": "What should I call you?"},
    "need": {
        "text": "What's one thing you'd most like help with first?",
        "voice": "What's one thing you'd most like help with first?",
    },
    "gmail": {
        "text": "Next, connect your Gmail with the Connect Gmail button whenever you're ready.",
        "voice": "I've put a Connect Gmail button on your screen. Tap it whenever you're ready.",
    },
}
# copy.md A-01 / A-03 / A-05 (DESIGN-002 tone pass).
OFFER_CALL = "The rest goes faster out loud. Quick call? Texting works just as well."
GREET = "Hi! I'm your new assistant. I'll help with email, your calendar, and the everyday stuff."
FIRST_ASK_AGENT_NAME = "First things first: what would you like to call me?"
AGENT_NAME_NUDGE = "No pressure. How about {0}, or {1}? Anything you like works."
RESUME = {"text": "Welcome back, let's pick up where we left off.", "voice": "Hey, we got cut off. Let's pick up where we left off."}
ACK = "Got it."
# Natural per-slot acknowledgements (live test 2026-09-27: "Got it: Juno." read like a form).
ACK_SLOT = {
    "agent_name": "{v}. I like it.",
    "user_name": "Nice to meet you, {v}.",
    "need": "Good one, I can help with that.",
    "gmail": "Gmail's connected.",
}
CHANGED_SLOT = {
    "agent_name": "Okay, {v} it is.",
    "user_name": "Thanks, {v} it is.",
    "need": "Okay, noted: {v}.",
    "gmail": "Updated.",
}


def ack_for(slot: str, value: Optional[str], changed: bool = False) -> str:
    table = CHANGED_SLOT if changed else ACK_SLOT
    line = table.get(slot)
    if not line or ("{v}" in line and not value):
        return CHANGED if changed else ACK
    return line.format(v=(value or "").strip().rstrip("."))
CHANGED = "Updated."
SKIPPED = "No problem, we can come back to that later."
SUGGEST = "A few ideas: getting your inbox under control, drafting replies, or keeping your calendar in check."
REJECTED = {
    "abusive": "Let's pick a different one.",
    "too_long": "That's a bit long. Something shorter?",
    "not_an_email": "That didn't look like an email address.",
}
RESPOND = {
    "privacy_question": (
        "Gmail connects through Google sign-in, so your assistant never sees your password, "
        "and nothing is sent without your OK."
    ),
    "prompt_injection": "I can't do that, but I'm happy to keep going with setup.",
    "abuse": "Let's keep it friendly.",
    "off_topic": "Good question. Let's finish getting you set up first.",
    "other_language": "Sorry, I can only do English for now.",
}
REASK = "Sorry, I missed that."

NATO = {
    "a": "Alpha", "b": "Bravo", "c": "Charlie", "d": "Delta", "e": "Echo", "f": "Foxtrot",
    "g": "Golf", "h": "Hotel", "i": "India", "j": "Juliett", "k": "Kilo", "l": "Lima",
    "m": "Mike", "n": "November", "o": "Oscar", "p": "Papa", "q": "Quebec", "r": "Romeo",
    "s": "Sierra", "t": "Tango", "u": "Uniform", "v": "Victor", "w": "Whiskey", "x": "X-ray",
    "y": "Yankee", "z": "Zulu",
}
SYMBOLS = {".": "dot", "_": "underscore", "-": "dash", "+": "plus"}
EMAIL_CHUNK = 4


def ask_line(slot: str, channel: Channel) -> str:
    lines = ASK[slot]
    return lines.get(channel) or lines["text"]


def agent_name_nudge() -> str:
    return AGENT_NAME_NUDGE.format(*AGENT_NAME_SUGGESTIONS[:2])


def spell(value: str) -> str:
    """'Sam' -> 'S, A, M' (spoken spell-back)."""
    return ", ".join(c.upper() for c in value if not c.isspace())


def _say_char(c: str) -> str:
    low = c.lower()
    if low in NATO:
        return f"{low} as in {NATO[low]}"
    return SYMBOLS.get(c, c)


def email_chunks(email: str, size: int = EMAIL_CHUNK) -> list[str]:
    """Chunked NATO readback of the local part; the domain is read as words."""
    local, _, domain = email.partition("@")
    chunks = [", ".join(_say_char(c) for c in local[i:i + size]) for i in range(0, len(local), size)]
    if domain:
        chunks.append("at " + " dot ".join(domain.split(".")))
    return chunks


def email_readback(email: str) -> str:
    return "I have " + "; ".join(email_chunks(email)) + ". Is that right?"


def confirm_line(slot: str, value: Optional[str], channel: Channel) -> str:
    value = value or ""
    if slot == "agent_name":
        return f"Just checking, you'd like to call your assistant {value}?"
    if slot == "user_name":
        return f"I want to get your name right. I heard {spell(value)}. Is that right?"
    if slot == "gmail":
        return email_readback(value)
    return f"Just checking: {value}. Is that right?"


def why_line(spec: FlowSpec, slot: str) -> str:
    return spec.slots[slot]["why"]


DEFERRED = {
    "agent_name": "name your assistant",
    "user_name": "tell your assistant your name",
    "need": "share what you'd like help with",
    "gmail": "connect Gmail",
}


def graduation_summary(state: SessionState, deferred: list[str]) -> str:
    """value_demo + graduated: one templated summary (no fabricated inbox facts)."""
    agent = state.slots.get("agent_name")
    user = state.slots.get("user_name")
    need = state.slots.get("need")
    who = agent.value if agent and agent.status == "filled" and agent.value else "Your assistant"
    hi = f"You're all set, {user.value}." if user and user.status == "filled" and user.value else "You're all set."
    parts = [hi]
    if need and need.status == "filled" and need.value:
        parts.append(f"{who}'s first job: {_lower_first(need.value.rstrip('.'))}.")
    else:
        parts.append(f"{who} is ready when you are.")
    todo = [DEFERRED[s] for s in deferred if s in DEFERRED]
    if todo:
        items = todo[0] if len(todo) == 1 else ", ".join(todo[:-1]) + " and " + todo[-1]
        parts.append(f"You can {items} from the main screen whenever you like.")
    return " ".join(parts)


def _lower_first(v: str) -> str:
    return v[:1].lower() + v[1:] if v[:2] != v[:2].upper() else v


# Post-graduation home conversation (GRAD-001). Policy text: templated, never paraphrased.
# Claims stay inside docs/product-facts.md; this trial never executes tasks.
HOME_REPLY = {
    "prompt_injection": "I can't do that, and nothing's changed.",
    "privacy_question": RESPOND["privacy_question"],
    "home_capability": (
        "I'm here for your email, calendar and everyday tasks, and I ask for your OK before acting "
        "for you. This trial can't carry out tasks yet."
    ),
    "home_task": "I can't do that in this trial yet, so nothing's been read, sent or changed.",
    "home_offer_gmail": "Connect Gmail on this screen whenever you're ready.",
    "home_need_added": "I've added that to what you'd like help with.",
    "home_chat": "You can rename me, change your name or what you'd like help with right here.",
}
HOME_REJECTED = {
    "charset": "That doesn't look like a name. Try letters only.",
    "too_short": "Could you say a bit more?",
    "unsure_need": "No rush. Tell me when something comes to mind.",
    "joke_or_profane": "Let's pick a different one.",
    "very_long": "That's a bit long. Something shorter?",
    "needs_confirm": "Let's pick a different one.",
    "empty": "That's empty. Try again.",
    **REJECTED,
}
HOME_GMAIL_CONNECTED = "Gmail's connected. Nothing is sent or changed without your OK."
