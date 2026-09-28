"""Post-graduation turns (GRAD-001): the home screen's conversation.

Called from `engine.apply` only when the session has already graduated and the turn is an
utterance (or the OAuth gmail fill), never an event. Pure, like the engine:

    apply_home(spec, state, turn) -> TurnResult   (input state never mutated)

What it may change — and only through the same validators as onboarding:
  - agent_name / user_name / need: an explicit edit (`change_answer`, from chat or from the
    home screen's tap-to-edit route) overwrites; an empty/skipped slot is filled by any valid
    value; "I *also* want help with X" appends to `need` instead of replacing it.
  - gmail: only a turn with `oauth_verified=True` (the OAuth callback) fills it.
Anything else is answered, never acted on: this trial doesn't execute tasks, so a task
request gets an honest "can't do that yet" (no fabricated inbox facts, nothing "sent").

The plan it returns uses the engine's `ResponsePlan` with `say=["home"]`; `respond_to`
carries the home reply kinds (HOME_KINDS) for the phrasing layer's HOME_* templates.
A prompt-injection turn changes nothing (state returned as given).

Live test 2026-09-28 (HOME-002):
  - "change my name" (no value) asks "What should I call you?" and waits; the next message is
    the new value. The wait is kept in `state.explained` as a `home:` entry (no schema change).
  - "my name is Darrran not darren": the "not ..." part is dropped, and a name with a tripled
    letter is read back for a spelling check ("did you mean Darran?") before it is saved.
  - Model-extracted values must appear in what the user typed: a bare "change my name" can no
    longer re-save every slot from context (the "Thanks, Darran it is. Okay, jarvis it is. ..."
    burst).
  - Simple arithmetic gets a short answer; other off-topic asks get a natural scoped redirect.
"""
from __future__ import annotations

import copy
import re
from typing import Optional

from .spec import FlowSpec
from .state import SessionState
from .validators import VALIDATORS

HOME = "home"
EDITABLE = ("agent_name", "user_name", "need")
# Reply kinds (plan.respond_to) the phrasing layer turns into HOME_* templates.
HOME_KINDS = ("prompt_injection", "privacy_question", "home_capability", "home_task", "home_offer_gmail",
              "home_need_added", "home_chat", "home_ask_user_name", "home_ask_agent_name", "home_ask_need",
              "home_confirm_spelling", "home_math", "home_off_topic", "home_cancelled", "home_kept")
# Validator outcomes that need a yes/no during onboarding are refused on home (no pending
# confirm state after graduation): the user just picks another value.
_CONFIRM_REASON = "needs_confirm"

# Code-side extraction floor for the common edit phrasings, so edits work offline (FakeLlm)
# and when the model misses them. Conservative: whole-utterance patterns only.
_NAME_TAIL = r"(\S[^\n!?,;]{0,48}?)"
_USER_EDIT = re.compile(rf"^(?:please\s+)?(?:actually\s*,?\s*)?(?:call me|my name is|my name's|i'm called|i go by)\s+{_NAME_TAIL}\s*(?:instead|please|now)?[.!]*$", re.I)
_AGENT_EDIT = re.compile(
    rf"^(?:please\s+)?(?:actually\s*,?\s*)?(?:rename yourself(?: to)?|call yourself|change your name to|your name is( now)?|"
    rf"i'?ll call you|i want to call you|let'?s call you|be called)\s+{_NAME_TAIL}\s*(?:instead|please|now)?[.!]*$", re.I)
_NEED_EDIT = re.compile(
    r"^(?:(?:and\s+)?i\s+(?:also\s+)?(?:want|need|would like|'d like)\s+(?:some\s+)?help\s+with|"
    r"(?:also\s+)?help me with|i also need help with|change my need to|actually,?\s+i\s+(?:want|need)\s+help\s+with)\s+(.{3,200}?)[.!]*$",
    re.I)
_ALSO = re.compile(r"\b(also|too|as well|another thing|in addition)\b", re.I)
_INJECTION = re.compile(
    r"ignore (all |any |the |your )?(previous|prior|above|earlier)? ?(instructions|rules|prompts?)|system prompt|"
    r"developer mode|you are now|disregard (your|all|the) ", re.I)
_PRIVACY = re.compile(r"\b(privacy|private|password|secure|security|safe|data|store[sd]?|retention|who can see|access)\b", re.I)
_CAPABILITY = re.compile(r"\b(what (can|will|do|would) you|how (do|does|will) (you|this|it) work|what are you|what('s| is) (this|persona)|"
                         r"what happens (next|now)|what do you do)\b", re.I)
_TASK = re.compile(r"\b(check|read|send|reply|draft|write|schedule|book|remind|summari[sz]e|sort|archive|delete|forward|"
                   r"clean up|unsubscribe|find|look up|email|inbox|calendar|meeting)\b", re.I)
_GMAIL = re.compile(r"\b(gmail|inbox|email|mail)\b", re.I)
# "change my name" with no value: ask for it, then wait for the next message.
_ASK_EDIT = (
    ("user_name", re.compile(
        r"^(?:(?:can|could|may) i\s+|i\s+(?:want|need|would like|'d like)\s+to\s+|i wanna\s+|let me\s+|let's\s+|please\s+)?"
        r"(?:change|update|fix|correct|edit|rename)\s+my\s+name(?:\s+please)?[.!?]*$|"
        r"^(?:that's|thats|that is)\s+not\s+my\s+name[.!?]*$|^you\s+(?:got|spelled|have)\s+my\s+name\s+wrong[.!?]*$", re.I)),
    ("agent_name", re.compile(
        r"^(?:(?:can|could|may) i\s+|i\s+(?:want|need|would like|'d like)\s+to\s+|i wanna\s+|let me\s+|please\s+)?"
        r"(?:change|update|edit)\s+your\s+name(?:\s+please)?[.!?]*$|^(?:can i\s+)?rename\s+you(?:rself)?[.!?]*$", re.I)),
    ("need", re.compile(
        r"^(?:(?:can|could|may) i\s+|i\s+(?:want|need|would like|'d like)\s+to\s+|let me\s+|please\s+)?"
        r"(?:change|update|edit)\s+(?:my need|what i (?:need|want)(?:\s+help with)?)[.!?]*$", re.I)),
)
_NOT_TAIL = re.compile(r"\s*,?\s+(?:not|instead of|rather than)\s+\S.*$", re.I)
_TRIPLED = re.compile(r"([a-z])\1\1+", re.I)
_YES = re.compile(r"^(?:y|yes|yeah|yep|yup|correct|right|that's right|thats right|exactly|sure|ok|okay|perfect)\b[.! ]*", re.I)
_NO = re.compile(r"^(?:n|no|nope|nah|not quite|wrong)\b[.!, ]*", re.I)
_CANCEL = re.compile(r"^(?:never ?mind|nvm|cancel|forget it|skip it|no thanks|leave it)[.! ]*$", re.I)
_VALUE_LEAD = re.compile(r"^(?:it's|it is|its|call me|my name is|my name's|i'm|im|i am|call yourself|you're|youre|"
                         r"i want help with|help with|i need help with)\s+", re.I)
_MATH = re.compile(r"^\s*(?:what'?s|whats|what is|calculate|how much is|compute)?\s*(-?\d{1,9}(?:\.\d{1,4})?)\s*"
                   r"([-+*/x×÷]|plus|minus|times|divided by)\s*(-?\d{1,9}(?:\.\d{1,4})?)\s*[?.!=]*\s*$", re.I)
PENDING = "home:"   # `state.explained` entry prefix for a home turn that is waiting on the user


def home_extract(utterance: str) -> tuple[dict[str, str], bool]:
    """Regex floor for edit phrasings: (slots, is_edit)."""
    text = (utterance or "").strip()
    if not text:
        return {}, False
    for slot, rx in (("user_name", _USER_EDIT), ("agent_name", _AGENT_EDIT), ("need", _NEED_EDIT)):
        m = rx.match(text)
        if m:
            value = m.groups()[-1].strip()
            if slot != "need":
                value = _NOT_TAIL.sub("", value).strip()   # "Darran not Darren" -> "Darran"
            return {slot: value}, True
    return {}, False


def pending(st: SessionState) -> Optional[list[str]]:
    """What a home turn is waiting on: ["ask", slot] or ["spell", slot, typed, suggestion]."""
    for e in st.explained:
        if e.startswith(PENDING):
            return e[len(PENDING):].split("|")
    return None


def _set_pending(st: SessionState, *parts: str) -> None:
    st.explained = [e for e in st.explained if not e.startswith(PENDING)]
    if parts:
        st.explained.append(PENDING + "|".join(p.replace("|", " ") for p in parts))


def spelling_suggestion(value: str) -> Optional[str]:
    """"Darrran" -> "Darran" (a tripled letter is almost always a typo); None when unambiguous."""
    fixed = _TRIPLED.sub(lambda m: m.group(1) * 2, value or "")
    return fixed if fixed != value else None


def _grounded(slot: str, value: str, text: str) -> bool:
    """A model-extracted value must come from what the user said (not from context)."""
    v, t = (value or "").strip().lower(), text.lower()
    if not v:
        return False
    if slot == "need":
        words = re.findall(r"[a-z']{4,}", v)
        return not words or any(w in t for w in words)
    return v in t or v.replace(" ", "") in t.replace(" ", "")


def _math(text: str) -> Optional[str]:
    m = _MATH.match(text or "")
    if not m:
        return None
    a, op, b = float(m.group(1)), m.group(2).lower(), float(m.group(3))
    sym = {"x": "×", "*": "×", "times": "×", "plus": "+", "minus": "-", "divided by": "÷", "/": "÷"}.get(op, op)
    if sym == "÷" and b == 0:
        return None
    r = {"+": a + b, "-": a - b, "×": a * b, "÷": a / b if b else 0}[sym]
    fmt = lambda n: str(int(n)) if float(n).is_integer() else f"{n:.4g}"  # noqa: E731
    return f"{fmt(a)} {sym} {fmt(b)} is {fmt(r)}."


def _value_from(text: str) -> str:
    v = _VALUE_LEAD.sub("", text.strip()).strip().rstrip(".!?").strip()
    return v.strip("\"'“”")


def _deferred(spec: FlowSpec, st: SessionState) -> list[str]:
    return [s for s, d in spec.slots.items() if d["required"] and not st.filled(s)]


def apply_home(spec: FlowSpec, state: SessionState, turn):
    # Imported here: engine imports this module inside its graduated branch.
    from .engine import ResponsePlan, TurnResult

    st = copy.deepcopy(state)
    plan = ResponsePlan(node=st.node, graduate=True, say=[HOME])
    ev: list[dict] = []
    x = turn.extraction
    text = (turn.utterance or "").strip()
    intents = [i for i in x.intents if i in spec.intents]

    if "prompt_injection" in intents or _INJECTION.search(text):
        # Structural defense, as in onboarding: nothing in an injection turn can move state.
        ev.append({"type": "injection_ignored", "node": state.node})
        plan.respond_to.append("prompt_injection")
        plan.deferred = list(state.deferred_prompts)
        return TurnResult(copy.deepcopy(state), plan, ev)

    wait = pending(st)
    kept: Optional[str] = None   # slot the user re-confirmed (the value was already that)
    if text:
        _set_pending(st)   # any typed/spoken turn resolves (or abandons) what we were waiting on
    slots = {k: v for k, v in x.slots.items() if v and (not text or k == "gmail" or _grounded(k, v, text))}
    floor, floor_edit = home_extract(text)
    for k, v in floor.items():
        slots[k] = v if k != "need" or k not in slots else slots[k]
    editing = "change_answer" in intents or floor_edit
    also = bool(_ALSO.search(text))

    if wait and text:
        if _CANCEL.match(text):
            plan.respond_to.append("home_cancelled")
            return _done(spec, st, plan, ev)
        if wait[0] == "spell" and len(wait) == 4:
            slot, typed, suggestion = wait[1], wait[2], wait[3]
            if _YES.match(text) and not _grounded(slot, typed, text):
                slots, editing, kept = {slot: suggestion}, True, slot
            elif _NO.match(text) and not _value_from(_NO.sub("", text)):
                _set_pending(st, "ask", slot)
                plan.respond_to.append(f"home_ask_{slot}")
                return _done(spec, st, plan, ev)
            else:
                slots, editing = {slot: slots.get(slot) or _value_from(_NO.sub("", text)) or typed}, True
                plan.note = "spelled"   # they chose this spelling: don't ask again
        elif wait[0] == "ask" and len(wait) == 2 and wait[1] in EDITABLE:
            slot = wait[1]
            slots, editing, kept = {slot: slots.get(slot) or floor.get(slot) or _value_from(text)}, True, slot
    elif text and not slots:
        for slot, rx in _ASK_EDIT:
            if rx.match(text):
                _set_pending(st, "ask", slot)
                plan.respond_to.append(f"home_ask_{slot}")
                return _done(spec, st, plan, ev)

    for name in EDITABLE:
        raw = slots.get(name)
        if raw and turn.channel in spec.slots[name]["channels"]:
            _edit(spec, st, name, raw, editing=editing, also=also, turn=turn, plan=plan, ev=ev)
    if slots.get("gmail"):
        _gmail(spec, st, slots["gmail"], turn, plan, ev)

    if kept and not (plan.changed or plan.acknowledge or plan.rejected or plan.respond_to):
        # "yes" to "did you mean Darran?" when it already was Darran: confirm it, don't deflect.
        plan.respond_to.append("home_kept")
        plan.note = kept
    if not (plan.changed or plan.acknowledge or plan.rejected or plan.respond_to):
        answer = _math(text)
        if answer:
            plan.note = answer
            plan.respond_to.append("home_math")
        else:
            plan.respond_to.extend(_kinds(st, text, intents))
    return _done(spec, st, plan, ev)


def _done(spec, st, plan, ev):
    from .engine import TurnResult

    st.deferred_prompts = _deferred(spec, st)
    plan.deferred = list(st.deferred_prompts)
    if plan.changed or plan.acknowledge or plan.rejected or plan.respond_to:
        ev.append({"type": "home_turn", "changed": list(plan.changed), "filled": list(plan.acknowledge),
                   "rejected": dict(plan.rejected), "respond_to": list(plan.respond_to)})
    return TurnResult(st, plan, ev)


def _edit(spec, st, name, raw, *, editing, also, turn, plan, ev) -> None:
    sdef = spec.slots[name]
    vid = sdef["validator"]
    sv = st.slot(name)
    was_filled = sv.status == "filled"
    if was_filled and not editing and not (name == "need" and also):
        return  # a filled answer changes only on an explicit edit
    value = raw
    appended = False
    text = (turn.utterance or "").strip()
    if name == "user_name" and text and plan.note != "spelled":
        suggestion = spelling_suggestion(raw.strip())
        if suggestion:
            # Ambiguous spelling ("Darrran"): check before saving anything.
            _set_pending(st, "spell", name, raw.strip(), suggestion)
            plan.respond_to.append("home_confirm_spelling")
            ev.append({"type": "spelling_check", "slot": name, "typed": raw.strip(), "suggestion": suggestion})
            return
    if name == "need" and was_filled and also:
        add = re.sub(r"^\s*(also|too)\s+", "", raw.strip(), flags=re.I).strip().rstrip(".")
        if add.lower() in (sv.value or "").lower():
            return
        value, appended = f"{(sv.value or '').rstrip('.')}; {add}", True
    res = VALIDATORS[vid](value, channel=turn.channel, confidence=turn.extraction.confidences.get(name))
    if res.outcome != "ok":
        reason = res.reason or ("unsure_need" if res.outcome == "unsure" else _CONFIRM_REASON)
        plan.rejected[name] = reason
        ev.append({"type": "slot_rejected", "slot": name, "reason": reason})
        return
    if was_filled and res.value == sv.value:
        return
    if was_filled:
        ev.append({"type": "slot_changed", "slot": name, "old": sv.value, "new": res.value})
        plan.changed.append(name)
        if appended:
            plan.respond_to.append("home_need_added")
    else:
        ev.append({"type": "slot_filled", "slot": name, "value": res.value, "validated_by": vid})
        plan.acknowledge.append(name)
    sv.value, sv.source, sv.confidence = res.value, turn.channel, turn.extraction.confidences.get(name)
    sv.status, sv.validated_by, sv.needs_confirm = "filled", vid, False


def _gmail(spec, st, raw, turn, plan, ev) -> None:
    res = VALIDATORS["gmail_oauth"](raw, channel=turn.channel, oauth_verified=turn.oauth_verified)
    sv = st.slot("gmail")
    if res.outcome != "ok":
        # Typed email after graduation: never "connected"; point at the Connect Gmail button.
        if not st.filled("gmail"):
            plan.respond_to.append("home_offer_gmail")
        return
    if sv.status == "filled" and sv.value == res.value:
        return
    ev.append({"type": "slot_filled" if sv.status != "filled" else "slot_changed", "slot": "gmail",
               "value": res.value, "validated_by": "gmail_oauth"})
    (plan.changed if sv.status == "filled" else plan.acknowledge).append("gmail")
    sv.value, sv.source, sv.confidence = res.value, turn.channel, None
    sv.status, sv.validated_by, sv.needs_confirm = "filled", "gmail_oauth", False


def _kinds(st: SessionState, text: str, intents: list[str]) -> list[str]:
    """What the user asked, when nothing was edited (answered from approved facts only)."""
    if "privacy_question" in intents or _PRIVACY.search(text):
        return ["privacy_question"]
    if _CAPABILITY.search(text):
        out = ["home_capability"]
    elif _TASK.search(text):
        out = ["home_task"]
    elif "off_topic" in intents:
        out = ["home_off_topic"]
    else:
        out = ["home_chat"]
    if not st.filled("gmail") and (_GMAIL.search(text) or out == ["home_task"]):
        out.append("home_offer_gmail")
    return out


def editable(slot: Optional[str]) -> bool:
    return slot in EDITABLE
