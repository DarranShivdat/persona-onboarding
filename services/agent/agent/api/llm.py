"""The LLM port the turn path depends on (extract + phrase), and an offline FakeLlm.

The real adapter (Anthropic SDK, FLOW-002) implements `TurnLlm`; the API never
imports it directly — it is injected via `create_app(llm=...)`. `FakeLlm` needs no
network: tests script its extractions, and local dev gets a naive "the utterance
answers the current node" extractor plus templated phrasing.
"""
from __future__ import annotations

from collections import deque
from typing import Callable, Iterable, Optional, Protocol, Union

from ..brain.engine import Extraction, ResponsePlan
from ..brain.spec import FlowSpec
from ..brain.state import Channel, SessionState
from ..llm import templates as T


class TurnLlm(Protocol):
    def extract(self, *, spec: FlowSpec, state: SessionState, utterance: str, channel: Channel) -> Extraction: ...
    def phrase(self, *, spec: FlowSpec, state: SessionState, plan: ResponsePlan, channel: Channel) -> str: ...


Script = Union[Extraction, Callable[[SessionState, str], Extraction]]

_ASK = {
    "agent_name": T.ask_line("agent_name", "text"),
    "user_name": "And what should I call you?",
    "need": "What's one thing you'd love a hand with this week?",
    "gmail": "Last step: connect your Gmail with the button below.",
}
_YES = {"yes", "y", "sure", "ok", "okay", "yeah", "yep", "call me"}
_NO = {"no", "n", "nope", "no thanks", "type", "typing"}


class FakeLlm:
    """Deterministic, offline. `script` items are consumed one per `extract` call;
    when exhausted, falls back to the naive extractor."""

    def __init__(self, script: Iterable[Script] = ()):
        self.script: deque[Script] = deque(script)
        self.extract_calls = 0

    def push(self, *items: Script) -> None:
        self.script.extend(items)

    def extract(self, *, spec: FlowSpec, state: SessionState, utterance: str, channel: Channel) -> Extraction:
        self.extract_calls += 1
        if self.script:
            item = self.script.popleft()
            return item(state, utterance) if callable(item) else item
        return naive_extract(spec, state, utterance)

    def phrase(self, *, spec: FlowSpec, state: SessionState, plan: ResponsePlan, channel: Channel) -> str:
        return template_phrase(spec, state, plan)


def naive_extract(spec: FlowSpec, state: SessionState, utterance: str) -> Extraction:
    text = utterance.strip()
    node = spec.nodes[state.node]
    if node["kind"] == "choice":
        low = text.lower().rstrip(".!")
        return Extraction(intents=["accept_call"] if low in _YES else ["decline_call"] if low in _NO else [])
    slot: Optional[str] = node.get("slot")
    if node["kind"] == "collect" and slot and slot != "gmail" and text:
        return Extraction(slots={slot: text})
    return Extraction()


def template_phrase(spec: FlowSpec, state: SessionState, plan: ResponsePlan) -> str:
    if plan.absorbed:
        return ""
    out: list[str] = []
    if is_home(plan):
        return home_phrase(state, plan)
    if plan.graduate:
        # One templated summary (never "let's get to work" next to a "still left" list).
        return T.graduation_summary(state, plan.deferred)
    if "greet" in plan.say:
        out.append(T.GREET)  # copy.md A-01
    elif plan.resume:
        out.append("Let's pick up where we left off.")
    if plan.respond_to:
        out.append("Good question — happy to get into that once we're set up.")
    for s in plan.acknowledge:
        out.append(T.ack_for(s, state.slots[s].value))
    for s in plan.changed:
        out.append(T.ack_for(s, state.slots[s].value, changed=True))
    for s, reason in plan.rejected.items():
        out.append(T.REJECTED.get(reason, f"Hmm, that didn't work for your {s.replace('_', ' ')}."))
    if plan.confirm:
        out.append(f"Just to confirm, {state.slots[plan.confirm].value}? (yes/no)")
    elif plan.offer_call:
        out.append(T.OFFER_CALL)
    elif plan.ask:
        if plan.explain_why:
            out.append(spec.slots[plan.ask].get("why", ""))
        if plan.ask == "agent_name" and "greet" in plan.say:
            out.append(T.FIRST_ASK_AGENT_NAME)
        elif plan.ask == "agent_name" and state.node_attempts.get("agent_name", 0) and not plan.explain_why:
            out.append(T.agent_name_nudge())  # copy.md A-03: hesitated, so offer names lightly
        else:
            out.append(_ASK.get(plan.ask, f"What's your {plan.ask}?"))
    return " ".join(p for p in out if p)


def is_home(plan: ResponsePlan) -> bool:
    """A post-graduation conversation turn (brain/home.py), not the graduation itself."""
    return "home" in plan.say


def home_ack(state: SessionState, plan: ResponsePlan, skip: Iterable[str] = ()) -> list[str]:
    """Templated acknowledgements / rejections for a home turn (edits and the gmail fill);
    `skip`: slots a model reaction already acknowledged."""
    out: list[str] = []
    for s in plan.acknowledge:
        if s in skip:
            continue
        out.append(T.HOME_GMAIL_CONNECTED if s == "gmail" else T.ack_for(s, state.slots[s].value))
    for s in plan.changed:
        if s in skip or (s == "need" and "home_need_added" in plan.respond_to):
            continue
        out.append(T.HOME_GMAIL_CONNECTED if s == "gmail" else T.ack_for(s, state.slots[s].value, changed=True))
    for s, reason in plan.rejected.items():
        out.append(T.HOME_REJECTED.get(reason, f"That didn't work for your {s.replace('_', ' ')}."))
    return out


def home_tail(plan: ResponsePlan, state: Optional[SessionState] = None) -> list[str]:
    out: list[str] = []
    for k in plan.respond_to:
        if k == "home_math" and plan.note:
            out += [plan.note, T.HOME_REPLY["home_math_tail"]]
        elif k == "home_confirm_spelling" and state is not None:
            from ..brain.home import pending
            w = pending(state) or []
            if len(w) == 4 and w[0] == "spell":
                out.append(T.home_spelling_line(w[2], w[3]))
        elif k in T.HOME_REPLY:
            out.append(T.HOME_REPLY[k])
    return out


def home_phrase(state: SessionState, plan: ResponsePlan) -> str:
    return " ".join(p for p in [*home_ack(state, plan), *home_tail(plan, state)] if p)
