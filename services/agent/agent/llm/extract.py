"""Extraction: one `record_slots` tool call -> candidate `Extraction` for `brain.apply`.

This module never touches SessionState: it reads a little context (node, what was
asked, pending confirm, filled values) to disambiguate, and returns candidates. Only
the brain's validators decide what becomes `filled`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field as dc_field
from typing import Any, Optional

from ..brain.engine import Extraction
from ..brain.spec import FlowSpec
from ..brain.state import Channel, SessionState
from ..obs.tracing import NoopTracer, Tracer
from .client import LLMClient, blocks, field, usage_dict
from .models import extract_model, policy_for
from .prompts import extraction_system
from .schema import TOOL_NAME, record_slots_tool

MAX_SLOT_CHARS = 200
EXTRACT_MAX_TOKENS = 512
AUTO_INSTRUCTION = f"Call the {TOOL_NAME} tool now. Do not reply with text."


@dataclass
class ExtractionResult:
    extraction: Extraction
    ok: bool                                  # False: no usable tool call (caller re-asks)
    model: str
    raw: Optional[dict] = None                # the tool input as returned
    usage: dict = dc_field(default_factory=dict)
    latency_ms: float = 0.0
    attempts: int = 1
    error: Optional[str] = None


def turn_context(spec: FlowSpec, state: SessionState, channel: Channel,
                 last_assistant: Optional[str] = None) -> dict[str, Any]:
    node = spec.nodes.get(state.node, {})
    pending = next((n for n, s in state.slots.items() if s.needs_confirm), None)
    return {
        "channel": channel,
        "node": state.node,
        "asking_for": node.get("slot") or ("call_offer" if node.get("kind") == "choice" else None),
        "pending_confirm": pending and {"slot": pending, "value": state.slots[pending].value},
        "known": {n: s.value for n, s in state.slots.items() if s.status in ("filled", "candidate")},
        "last_assistant": last_assistant,
    }


class Extractor:
    def __init__(self, client: LLMClient, spec: FlowSpec, *, model: Optional[str] = None,
                 tracer: Optional[Tracer] = None):
        self.client = client
        self.spec = spec
        self.model = model or extract_model()
        self.tracer = tracer or NoopTracer()
        self.policy = policy_for(self.model)
        # Stable prefix: tools render before system, so one breakpoint on the system
        # block caches tools + system together.
        self._tools = [record_slots_tool(spec)]
        self._system = [{"type": "text", "text": extraction_system(spec),
                         "cache_control": {"type": "ephemeral"}}]

    def request(self, utterance: str, context: dict[str, Any]) -> dict[str, Any]:
        # The utterance is data; it must not be able to close its own wrapper.
        utterance = utterance.replace("</user_message>", "").replace("<user_message>", "")
        user = (
            f"<context>{json.dumps(context, sort_keys=True)}</context>\n"
            f"<user_message>{utterance}</user_message>"
        )
        kw: dict[str, Any] = {
            "model": self.model,
            "max_tokens": EXTRACT_MAX_TOKENS,
            "system": self._system,
            "tools": self._tools,
            "messages": [{"role": "user", "content": user}],
        }
        if self.policy.forced_tool_choice:
            kw["tool_choice"] = {"type": "tool", "name": TOOL_NAME}
        else:
            kw["tool_choice"] = {"type": "auto"}
            kw["messages"][0]["content"] = user + "\n" + AUTO_INSTRUCTION
        if self.policy.thinking is not None:
            kw["thinking"] = self.policy.thinking
        if (self.policy.thinking or {}).get("type") != "enabled":
            kw["temperature"] = 0  # extraction is classification, not creativity
        return kw

    def extract(self, utterance: str, context: dict[str, Any], *,
                trace_id: Optional[str] = None) -> ExtractionResult:
        kw = self.request(utterance, context)
        # Auto tool_choice may answer in text; one retry, then give up (caller re-asks).
        tries = 1 if self.policy.forced_tool_choice else 2
        t0 = time.perf_counter()
        usage: dict = {}
        err: Optional[str] = None
        for attempt in range(1, tries + 1):
            try:
                resp = self.client.messages.create(**kw)
            except Exception as e:  # network/API errors never reach the brain
                err = f"{type(e).__name__}: {e}"
                break
            usage = usage_dict(resp)
            raw = _tool_input(resp)
            if raw is not None:
                res = ExtractionResult(parse(self.spec, raw), True, self.model, raw, usage,
                                       _ms(t0), attempt)
                self._trace(trace_id, utterance, context, res)
                return res
            err = "no record_slots tool call"
        res = ExtractionResult(Extraction(), False, self.model, None, usage, _ms(t0), attempt, err)
        self._trace(trace_id, utterance, context, res)
        return res

    def _trace(self, trace_id, utterance, context, res: ExtractionResult) -> None:
        if trace_id is None:
            return
        self.tracer.generation(trace_id, name="extract", model=res.model,
                               input={"utterance": utterance, "context": context},
                               output=res.raw, usage=res.usage, latency_ms=res.latency_ms,
                               metadata={"ok": res.ok, "attempts": res.attempts, "error": res.error})


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _tool_input(resp: Any) -> Optional[dict]:
    for b in blocks(resp):
        if field(b, "type") == "tool_use" and field(b, "name") == TOOL_NAME:
            inp = field(b, "input")
            if isinstance(inp, str):
                try:
                    inp = json.loads(inp)
                except ValueError:
                    return None
            return inp if isinstance(inp, dict) else None
    return None


def parse(spec: FlowSpec, raw: dict) -> Extraction:
    """Coerce a tool input into an Extraction, defensively (schema drift, older models)."""
    slots_in = raw.get("slots") if isinstance(raw.get("slots"), dict) else {}
    conf_in = raw.get("confidence") if isinstance(raw.get("confidence"), dict) else {}
    slots: dict[str, Optional[str]] = {}
    conf: dict[str, float] = {}
    for name in spec.slots:
        v = slots_in.get(name)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        if not isinstance(v, str) or not v.strip() or v.strip().lower() in ("null", "none"):
            continue
        slots[name] = v.strip()[:MAX_SLOT_CHARS]
        c = conf_in.get(name)
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            conf[name] = min(1.0, max(0.0, float(c)))
    intents: list[str] = []
    for i in raw.get("intents") or []:
        if isinstance(i, str) and i in spec.intents and i not in intents:
            intents.append(i)
    return Extraction(slots=slots, confidences=conf, intents=intents)
