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
              "home_need_added", "home_chat")
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


def home_extract(utterance: str) -> tuple[dict[str, str], bool]:
    """Regex floor for edit phrasings: (slots, is_edit)."""
    text = (utterance or "").strip()
    if not text:
        return {}, False
    for slot, rx in (("user_name", _USER_EDIT), ("agent_name", _AGENT_EDIT), ("need", _NEED_EDIT)):
        m = rx.match(text)
        if m:
            return {slot: m.groups()[-1].strip()}, True
    return {}, False


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

    slots = {k: v for k, v in x.slots.items() if v}
    floor, floor_edit = home_extract(text)
    for k, v in floor.items():
        slots.setdefault(k, v)
    editing = "change_answer" in intents or floor_edit
    also = bool(_ALSO.search(text))

    for name in EDITABLE:
        raw = slots.get(name)
        if raw and turn.channel in spec.slots[name]["channels"]:
            _edit(spec, st, name, raw, editing=editing, also=also, turn=turn, plan=plan, ev=ev)
    if slots.get("gmail"):
        _gmail(spec, st, slots["gmail"], turn, plan, ev)

    if not (plan.changed or plan.acknowledge or plan.rejected or plan.respond_to):
        plan.respond_to.extend(_kinds(st, text, intents))

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
    elif _TASK.search(text) or "off_topic" in intents:
        out = ["home_task"]
    else:
        out = ["home_chat"]
    if not st.filled("gmail") and (_GMAIL.search(text) or out == ["home_task"]):
        out.append("home_offer_gmail")
    return out


def editable(slot: Optional[str]) -> bool:
    return slot in EDITABLE
