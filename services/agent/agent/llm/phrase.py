"""Phrasing: `ResponsePlan` -> 1-2 short sentences, then the output guard.

The LLM only phrases what the plan says (acknowledge, respond, ask). Critical lines
(confirm readbacks, NATO email chunks, graduation summary) are templates appended
after the LLM's lead-in. Any failure (API error, empty/guarded-out output, missing
question) degrades to templates, so a turn always has something to say.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field as dc_field
from typing import Any, Optional

from ..brain.engine import ResponsePlan
from ..brain.spec import FlowSpec
from ..brain.state import Channel, SessionState
from ..brain.validators import AGENT_NAME_SUGGESTIONS
from ..obs.tracing import NoopTracer, Tracer
from . import templates as T
from .client import LLMClient, blocks, field, usage_dict
from .guard import GuardResult, guard
from .models import phrase_model, policy_for
from .prompts import approved_facts, phrasing_system

PHRASE_MAX_TOKENS = 120  # 1-2 short sentences; hard cap on rambling


@dataclass
class PhraseResult:
    text: str
    source: str                                   # llm | template | mixed | silent
    dropped: list[tuple[str, str]] = dc_field(default_factory=list)
    brief: Optional[dict] = None
    usage: dict = dc_field(default_factory=dict)
    latency_ms: float = 0.0
    error: Optional[str] = None


def critical_lines(plan: ResponsePlan, state: SessionState, channel: Channel) -> list[str]:
    out: list[str] = []
    if plan.confirm:
        out.append(T.confirm_line(plan.confirm, state.slot(plan.confirm).value, channel))
    gm = state.slots.get("gmail")
    if plan.ask == "gmail" and channel == "voice" and gm and gm.status == "candidate" and gm.value:
        out.append(T.email_readback(gm.value))
    return out


def build_brief(plan: ResponsePlan, state: SessionState, channel: Channel,
                utterance: str = "", critical: bool = False) -> dict[str, Any]:
    val = lambda s: state.slots.get(s).value if s in state.slots else None  # noqa: E731
    agent = state.slots.get("agent_name")
    brief = {
        "channel": channel,
        "assistant_name": agent.value if agent and agent.status == "filled" else None,
        "user_name": val("user_name") if state.filled("user_name") else None,
        "greet": "greet" in plan.say,
        "resume": plan.resume,
        "acknowledge": {s: val(s) for s in plan.acknowledge},
        "changed": {s: val(s) for s in plan.changed},
        "rejected": dict(plan.rejected),
        "skipped_for_later": list(plan.skipped),
        "respond_to": list(plan.respond_to),
        "user_said": utterance if plan.respond_to else None,
        "ask": None if critical else plan.ask,
        "offer_call": plan.offer_call and not critical,
        "explain_why": plan.explain_why and not critical,
        "suggest_examples": plan.suggest_examples,
        "suggest_names": list(AGENT_NAME_SUGGESTIONS[:2]) if _hesitated_on_agent_name(plan, state) else None,
        "gmail_connected": state.filled("gmail"),
        "gmail_button_on_screen": "gmail_connect_card" in plan.push_ui,
        "end_without_question": critical,
    }
    return {k: v for k, v in brief.items() if v not in (None, False, {}, [], "")} | {"channel": channel}


def _has_content(brief: dict) -> bool:
    return any(k not in ("channel", "assistant_name", "user_name", "gmail_connected",
                         "gmail_button_on_screen", "end_without_question") for k in brief)


def template_reply(spec: FlowSpec, plan: ResponsePlan, state: SessionState,
                   channel: Channel, critical: bool) -> str:
    parts: list[str] = []
    if "greet" in plan.say:
        parts.append(T.GREET)
    elif plan.resume:
        parts.append(T.RESUME[channel])
    if plan.acknowledge:
        parts += [T.ack_for(s, state.slots[s].value if s in state.slots else None) for s in plan.acknowledge]
    elif plan.changed:
        parts += [T.ack_for(s, state.slots[s].value if s in state.slots else None, changed=True)
                  for s in plan.changed]
    parts += [T.RESPOND[i] for i in plan.respond_to if i in T.RESPOND]
    parts += [T.REJECTED[r] for r in plan.rejected.values() if r in T.REJECTED]
    if plan.skipped:
        parts.append(T.SKIPPED)
    if not critical:
        parts += _ask_parts(spec, plan, channel, state)
    return " ".join(dict.fromkeys(parts))


def _hesitated_on_agent_name(plan: ResponsePlan, state: SessionState) -> bool:
    return plan.ask == "agent_name" and state.node_attempts.get("agent_name", 0) > 0


def _ask_parts(spec: FlowSpec, plan: ResponsePlan, channel: Channel, state: SessionState) -> list[str]:
    parts: list[str] = []
    if plan.ask and plan.explain_why:
        parts.append(T.why_line(spec, plan.ask))
    if plan.suggest_examples:
        parts.append(T.SUGGEST)
    if plan.ask == "agent_name" and "greet" in plan.say:
        parts.append(T.FIRST_ASK_AGENT_NAME)
    elif _hesitated_on_agent_name(plan, state) and not plan.explain_why:
        parts.append(T.agent_name_nudge())
    elif plan.ask:
        parts.append(T.ask_line(plan.ask, channel, spec))
    elif plan.offer_call:
        parts.append(T.OFFER_CALL)
    return parts


class Phraser:
    def __init__(self, client: Optional[LLMClient], spec: FlowSpec, *, model: Optional[str] = None,
                 tracer: Optional[Tracer] = None):
        self.client = client
        self.spec = spec
        self.model = model or phrase_model()
        self.tracer = tracer or NoopTracer()
        self.policy = policy_for(self.model)
        self._system = [{"type": "text", "text": phrasing_system(spec),
                         "cache_control": {"type": "ephemeral"}}]
        self._facts = approved_facts() + "\n" + "\n".join(d["why"] for d in spec.slots.values())

    def request(self, brief: dict) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "model": self.model,
            "max_tokens": PHRASE_MAX_TOKENS,
            "system": self._system,
            "messages": [{"role": "user", "content": "<brief>" + json.dumps(brief, sort_keys=True) + "</brief>"}],
        }
        if self.policy.thinking is not None:
            kw["thinking"] = self.policy.thinking
        if (self.policy.thinking or {}).get("type") != "enabled":
            kw["temperature"] = 0.4
        return kw

    def allowed_corpus(self, state: SessionState) -> str:
        return self._facts + "\n" + "\n".join(s.value for s in state.slots.values() if s.value)

    def phrase(self, plan: ResponsePlan, state: SessionState, channel: Channel, *,
               utterance: str = "", trace_id: Optional[str] = None) -> PhraseResult:
        if plan.absorbed:
            return PhraseResult("", "silent")
        if plan.graduate:
            return PhraseResult(T.graduation_summary(state, plan.deferred), "template")
        crit = critical_lines(plan, state, channel)
        brief = build_brief(plan, state, channel, utterance, critical=bool(crit))
        fallback = template_reply(self.spec, plan, state, channel, critical=bool(crit))
        if not _has_content(brief) or self.client is None:
            return PhraseResult(_join(fallback, crit), "template", brief=brief)

        t0 = time.perf_counter()
        usage: dict = {}
        err: Optional[str] = None
        raw = ""
        try:
            resp = self.client.messages.create(**self.request(brief))
            usage = usage_dict(resp)
            raw = "".join(field(b, "text", "") for b in blocks(resp) if field(b, "type") == "text")
        except Exception as e:  # never let an API error become dead air
            err = f"{type(e).__name__}: {e}"
        latency = round((time.perf_counter() - t0) * 1000, 1)
        g: GuardResult = guard(raw, allowed=self.allowed_corpus(state), gmail_connected=state.filled("gmail"))
        text, source = g.text, "llm"
        if not text:
            text, source = fallback, "template"
        elif not crit and (plan.ask or plan.offer_call) and "?" not in text and not _mentions_button(text, plan):
            # The model (or the guard) lost the question: append the templated ask.
            text, source = text + " " + " ".join(_ask_parts(self.spec, plan, channel, state)[-1:]), "mixed"
        if crit:
            source = "mixed" if source == "llm" else source
        res = PhraseResult(_join(text, crit), source, g.dropped, brief, usage, latency, err)
        if trace_id is not None:
            self.tracer.generation(trace_id, name="phrase", model=self.model, input=brief, output=res.text,
                                   usage=usage, latency_ms=latency,
                                   metadata={"source": source, "dropped": g.dropped, "error": err})
        return res


def _mentions_button(text: str, plan: ResponsePlan) -> bool:
    # The gmail ask is an instruction to press the on-screen button, not a question.
    return plan.ask == "gmail" and "button" in text.lower()


def _join(text: str, crit: list[str]) -> str:
    return " ".join(p for p in [text, *crit] if p).strip()
