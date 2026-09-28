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
        "text": "Use the Connect Gmail button whenever you're ready.",
        "voice": "I've put a Connect Gmail button on your screen. Tap it whenever you're ready.",
    },
}
# copy.md A-01 / A-03 / A-05 (DESIGN-002 tone pass).
OFFER_CALL = "The rest goes faster out loud. Quick call? Texting works just as well."
GREET = "Hi! I'm your new assistant. I'll help with email, your calendar, and the everyday stuff."
FIRST_ASK_AGENT_NAME = "First things first: what would you like to call me?"
AGENT_NAME_NUDGE = "No pressure. How about {0}, or {1}? Anything you like works."
# CUTOFF-001 (live 2026-09-28): the brain cannot tell a fresh call from a dropped one, so its
# voice resume line is the fresh-call one; only a real reconnect (lease resumed inside the
# grace window, VoiceFlow.opening(reconnect=True)) swaps in RESUME_CUT_OFF.
RESUME = {"text": "Welcome back, let's pick up where we left off.",
          "voice": "Hi, it's Persona! Let's pick up where we left off in the chat."}
RESUME_CUT_OFF = "Hey, we got cut off. Let's pick up where we left off."
ACK = "Got it."
# Natural per-slot acknowledgements (live test 2026-09-27: "Got it: Juno." read like a form).
ACK_SLOT = {
    "agent_name": "{v}. I like it.",
    "user_name": "Nice to meet you, {v}.",
    # HONEST-001 (live 2026-09-28): ONE fixed need ack for every need, no capability claim either
    # way (never "I can help with that"). The LLM only extracts the need; code says this.
    "need": "Noted: {v}.",
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
    v = (value or "").strip().rstrip(".")
    return line.format(v=_lower_first(v) if slot == "need" else v)


def slot_ack(state: SessionState, slot: str, changed: bool = False) -> str:
    """ack_for plus the name kept at the read-back cap ("I'll go with ... for now")."""
    sv = state.slots.get(slot)
    value = sv.value if sv else None
    if slot == "user_name" and sv is not None and sv.low_confidence and value:
        return NAME_KEEP.format(v=value.strip().rstrip("."))
    return ack_for(slot, value, changed)


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
    "prompt_injection": "I'll leave that one. Let's keep going with setup.",
    "abuse": "Let's keep it friendly.",
    "off_topic": "Good question. Let's finish getting you set up first.",
    "other_language": "Sorry, I can only do English for now.",
}
REASK = "Sorry, I missed that."

# VQA-001: a user's question, on the call AND in the text chat (at every collecting node), is
# answered from THIS table only. The extraction call picks an id (enum, like a Penciled
# categorize bucket) or none; code says the line verbatim and then the node's ask. Every
# line restates docs/product-facts.md (the only approved facts), makes no capability
# promise, and passes the output guard (tests/test_voice_polish.py, test_text_answers.py).
# No id -> the fixed deflection RESPOND["off_topic"]. Never model-written.
APPROVED_ANSWERS: dict[str, tuple[str, str]] = {  # id: (what it answers, line said verbatim)
    "what_is_persona": (
        "who or what you are, what Persona is, or what you / it can do or help with",
        "Persona's a personal AI assistant that helps with your email, calendar, and everyday "
        "tasks. This trial covers setup only, so it doesn't carry out tasks yet."),
    "setup_length": (
        "how long this / setup will take, how many questions are left, or what setup involves",
        "Setup is a few quick questions: what to call your assistant, your name, one thing you'd "
        "like help with, and connecting Gmail."),
    "can_skip": (
        "whether they can skip a question or step, or do it later",
        "You can skip any question and finish it later from the main screen."),
    "need_to_call": (
        "whether they need to call / talk, or can just type",
        "You don't need to call: typing works just as well, and a quick call is optional."),
    "asks_before_acting": (
        "whether it acts on its own or asks first",
        "Persona asks for your OK before it acts on your behalf."),
    "persona_band": (
        "the Persona Band or any hardware",
        "Persona Band is Persona's screenless wearable for talking to your assistant by voice."),
    "gmail_password": (
        "whether it sees their password, or how Gmail connects",
        "Gmail connects through Google sign-in, so your assistant never sees your password."),
    "gmail_access": (
        "why it wants Gmail or read and write access, or what it does with email",
        "It asks for read and write access so it can read your email, organize it, and send "
        "email for you, only after you say OK."),
    "nothing_without_ok": (
        "whether their email is safe, or whether it will send or change email without asking",
        "Nothing is sent or changed in your inbox without your explicit OK."),
    "gmail_testing": (
        "the unverified app notice, testing mode, or who can connect",
        "This trial's Google app is in testing mode, so only invited test accounts can connect, "
        "and Google shows an unverified app notice you can continue past."),
    "gmail_disconnect": (
        "disconnecting Gmail or removing access",
        "You can disconnect Gmail anytime in Settings, which revokes access and deletes the "
        "stored tokens."),
    "call_audio": (
        "whether the call is recorded or what happens to their voice",
        "Speech providers process the call to run the conversation, and call audio is not saved."),
    "data_retention": (
        "privacy, or how long their data is kept",
        "Setup conversations are kept no longer than 30 days after your last activity."),
    "contact": (
        "who to contact with questions or deletion requests",
        "For questions or deletion requests, email darranshivdat1 at gmail dot com."),
}
VOICE_ANSWERS = APPROVED_ANSWERS  # the call's name for the same table (VQA-001)


def with_answer(line: str, answer: Optional[str]) -> str:
    """Put an approved answer where the generic question reply sits (or first); the node's
    ask that follows is kept, so setup keeps moving. Idempotent."""
    if not answer or not line or answer in line:
        return line or (answer or "")
    for generic in (RESPOND["off_topic"], RESPOND["privacy_question"]):
        if generic in line:
            return line.replace(generic, answer, 1)
    return f"{answer} {line}"


NATO = {
    "a": "Alpha", "b": "Bravo", "c": "Charlie", "d": "Delta", "e": "Echo", "f": "Foxtrot",
    "g": "Golf", "h": "Hotel", "i": "India", "j": "Juliett", "k": "Kilo", "l": "Lima",
    "m": "Mike", "n": "November", "o": "Oscar", "p": "Papa", "q": "Quebec", "r": "Romeo",
    "s": "Sierra", "t": "Tango", "u": "Uniform", "v": "Victor", "w": "Whiskey", "x": "X-ray",
    "y": "Yankee", "z": "Zulu",
}
SYMBOLS = {".": "dot", "_": "underscore", "-": "dash", "+": "plus"}
EMAIL_CHUNK = 4


def ask_line(slot: str, channel: Channel, spec: Optional[FlowSpec] = None) -> str:
    lines = ASK.get(slot)
    if lines is None and spec is not None:
        # A slot added to flow.yaml without a hand-written line asks with its spec `ask`
        # (or its description): new nodes need no code change (GUARD-001 modularity).
        sdef = spec.slots[slot]
        return sdef.get("ask") or sdef["description"]
    if lines is None:
        raise KeyError(slot)
    return lines.get(channel) or lines["text"]


def agent_name_nudge() -> str:
    return AGENT_NAME_NUDGE.format(*AGENT_NAME_SUGGESTIONS[:2])


def spell(value: str, sep: str = ", ") -> str:
    """'Sam' -> 'S, A, M' (spoken spell-back); sep="-" -> 'S-A-M'."""
    return sep.join(c.upper() for c in value if not c.isspace())


# user_name read-back (NAME-001). Only the NAME is spelled on the call, never an email.
NAME_CONFIRM_VOICE = "Nice to meet you. Did I get that right: {v}, {s}?"
NAME_RECONFIRM_VOICE = "Thanks. So that's {v}, {s}?"
NAME_CONFIRM_TEXT = "Just checking, should I call you {v}?"
NAME_REASK = "Sorry about that. What's your name again?"
NAME_SPELL_ASK = "Sorry, could you spell it for me, letter by letter?"
NAME_CONFIRMED = "Perfect, thanks {v}."
NAME_KEEP = "I'll go with {v} for now. You can edit it anytime."


def name_confirm_line(value: str, channel: Channel, attempt: int = 1) -> str:
    if channel != "voice":
        return NAME_CONFIRM_TEXT.format(v=value)
    line = NAME_CONFIRM_VOICE if attempt <= 1 else NAME_RECONFIRM_VOICE
    return line.format(v=value, s=spell(value, "-"))


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
        return name_confirm_line(value, channel)
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
        # HONEST-001: restate the need, no claim that the assistant will (or can) do it.
        parts.append(f"{who} has noted what you'd like help with: {_lower_first(need.value.rstrip('.'))}.")
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
    "prompt_injection": "I'll leave that one, and nothing's changed.",
    "privacy_question": RESPOND["privacy_question"],
    "home_capability": (
        "I'm here for your email, calendar and everyday tasks, and I ask for your OK before acting "
        "for you. This trial doesn't carry out tasks yet."
    ),
    "home_task": "This trial doesn't carry out tasks yet, so nothing's been read, sent or changed.",
    "home_offer_gmail": "Connect Gmail on this screen whenever you're ready.",
    "home_need_added": "I've added that to what you'd like help with.",
    "home_chat": "You can rename me, change your name or what you'd like help with right here.",
    "home_ask_user_name": "Sure. What should I call you?",
    "home_ask_agent_name": "Sure. What would you like to call me?",
    "home_ask_need": "Sure. What would you like help with?",
    "home_off_topic": ("That's outside this trial's setup. You can rename me, change your name, "
                       "or update what you'd like help with."),
    "home_math_tail": "Otherwise, I'm here if you want to rename me, change your name, or update what you'd like help with.",
    "home_cancelled": "No problem, nothing's changed.",
}


def home_spelling_line(typed: str, suggestion: str) -> str:
    """HOME-002: an ambiguous typed name ("Darrran") is checked before it's saved."""
    return (f"Just checking the spelling: did you mean {suggestion} ({spell(suggestion, '-')})? "
            f"Say yes, or type it the way you'd like it.")
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
