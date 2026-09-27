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


class TurnLlm(Protocol):
    def extract(self, *, spec: FlowSpec, state: SessionState, utterance: str, channel: Channel) -> Extraction: ...
    def phrase(self, *, spec: FlowSpec, state: SessionState, plan: ResponsePlan, channel: Channel) -> str: ...


Script = Union[Extraction, Callable[[SessionState, str], Extraction]]

_ASK = {
    "agent_name": "What would you like to call your assistant?",
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
    if plan.resume:
        out.append("Welcome back!")
    if "greet" in plan.say:
        out.append("Hi! I'm your new Persona assistant.")
    if plan.respond_to:
        out.append("Good question — happy to get into that once we're set up.")
    for s in plan.acknowledge + plan.changed:
        out.append(f"Got it: {state.slots[s].value}.")
    for s, reason in plan.rejected.items():
        out.append(f"Hmm, that didn't work for your {s.replace('_', ' ')} ({reason}).")
    if plan.graduate:
        out.append("You're all set — let's get to work.")
        return " ".join(out)
    if plan.confirm:
        out.append(f"Just to confirm, {state.slots[plan.confirm].value}? (yes/no)")
    elif plan.offer_call:
        out.append("Want to hop on a quick call for the rest, or keep typing?")
    elif plan.ask:
        if plan.explain_why:
            out.append(spec.slots[plan.ask].get("why", ""))
        out.append(_ASK.get(plan.ask, f"What's your {plan.ask}?"))
    return " ".join(p for p in out if p)
