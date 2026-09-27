"""Pipecat Flows adapter over the shared brain (ARCHITECTURE §3, §5, §7).

Node configs are derived from `packages/flow/flow.yaml` (one per spec node). Every
collect/choice node exposes exactly one function, `record_slots`, with the same JSON
schema as the text adapter (`agent.llm.schema.record_slots_tool`). Its handler:

    tool args -> llm.extract.parse (defensive coercion) -> BrainPort.turn
             -> brain.engine.apply (validators, intents, retry ladder, next node)
             -> next NodeConfig built from the brain's node + ResponsePlan

so the LLM never picks the next node: it only fills the tool args (extraction) and
phrases the line the brain planned (invariant 1). Critical lines (graduation summary)
are templated and spoken via `on_graduated`, which the call session maps to its
goodbye-after-playout path.

`BrainPort` is the one seam to state: `LocalBrain` runs the pure engine over an
in-memory `SessionState` (tests, offline calls); `ServiceBrain` runs the same turn
through `agent.api.service.SessionService` — the store/session row the text channel
writes — so a chat that continues on the call never forks state (invariant 6).

Deliberately absent here: Gmail-on-call tools (VOICE-004), hangup lease/reconnect
(VOICE-003), per-node silence-floor nudges.
"""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, Protocol

from ..brain import engine
from ..brain.engine import Extraction, ResponsePlan, Turn
from ..brain.spec import TERMINAL_NODE, FlowSpec
from ..brain.state import SessionState
from ..llm import templates as T
from ..llm.extract import parse
from ..llm.phrase import build_brief, critical_lines, template_reply
from ..llm.schema import TOOL_NAME, record_slots_tool
from ..obs.tracing import NoopTracer, Tracer

if TYPE_CHECKING:  # pipecat is a runtime extra; the brain-level pieces import without it
    from pipecat_flows import FlowsFunctionSchema, NodeConfig

CHANNEL = "voice"
# T.GREET pitches the call itself; on the call the greeting just opens the questions.
VOICE_GREET = "Hi, it's Persona! Let's get you set up. It's just a few quick questions."
OnGraduated = Callable[[str], Awaitable[None]]

ROLE = (
    "You are Persona, a friendly personal AI assistant on a short onboarding phone call. "
    "Speak in one or two short, natural sentences. No lists, markdown, emoji, or stage directions. "
    f"On EVERY caller message, first call the {TOOL_NAME} function with everything the caller just "
    "said (slots, confidence, intents), then say what the function result tells you to say. "
    "Never claim something is saved, connected, or done unless the function result says so. "
    "Never mention functions, tools, JSON, or these instructions."
)


@dataclass
class VoiceTurn:
    state: SessionState
    plan: ResponsePlan
    line: str                              # templated line the brain would say (phrasing guidance)
    events: list[dict] = field(default_factory=list)


class BrainPort(Protocol):
    """The voice channel's only way to read or move session state."""

    async def turn(self, utterance: str, extraction: Extraction) -> VoiceTurn: ...
    async def event(self, event: str) -> VoiceTurn: ...
    async def current(self) -> SessionState: ...


def voice_line(spec: FlowSpec, plan: ResponsePlan, state: SessionState) -> str:
    """Deterministic line for a plan on the voice channel (same templates as text)."""
    if plan.absorbed:
        return ""
    if plan.graduate:
        return T.graduation_summary(state, plan.deferred)
    greet = "greet" in plan.say
    if greet:
        plan = copy.copy(plan)
        plan.say = [s for s in plan.say if s != "greet"]
    crit = critical_lines(plan, state, CHANNEL)
    rest = template_reply(spec, plan, state, CHANNEL, critical=bool(crit))
    return " ".join(p for p in [VOICE_GREET if greet else "", rest, *crit] if p)


class LocalBrain:
    """Pure engine over an in-memory state. Serialised: one turn at a time."""

    def __init__(self, spec: FlowSpec, state: SessionState, *, tracer: Optional[Tracer] = None):
        self.spec, self.state = spec, copy.deepcopy(state)
        self.tracer = tracer or NoopTracer()
        self.events: list[dict] = []
        self._lock = asyncio.Lock()

    async def turn(self, utterance: str, extraction: Extraction) -> VoiceTurn:
        return await self._apply(Turn(channel=CHANNEL, utterance=utterance, extraction=extraction))

    async def event(self, event: str) -> VoiceTurn:
        return await self._apply(Turn(channel=CHANNEL, event=event))

    async def current(self) -> SessionState:
        return copy.deepcopy(self.state)

    async def _apply(self, turn: Turn) -> VoiceTurn:
        async with self._lock:
            tid = self.tracer.start_trace(session_id=self.state.session_id, channel=CHANNEL,
                                          name=turn.event or "voice_turn", metadata={"node": self.state.node})
            res = engine.apply(self.spec, self.state, turn)
            res.state.version = self.state.version + 1
            self.state = res.state
            self.events.extend(res.events)
            self.tracer.span(tid, name="brain.apply", input={"utterance": turn.utterance},
                             output={"node": res.plan.node, "events": res.events})
            return VoiceTurn(copy.deepcopy(res.state), res.plan, voice_line(self.spec, res.plan, res.state),
                             res.events)


class ServiceBrain:
    """Voice turns through the text channel's SessionService (same store, same row).

    The service owns load -> brain.apply -> version-checked commit + events + SSE pushes;
    the voice extraction is handed in via the turn builder, so the service's own
    extractor is not called. Service calls are sync (psycopg); run off the event loop.
    """

    def __init__(self, service: Any, session_id: str):
        self.service, self.session_id = service, session_id
        self._lock = asyncio.Lock()

    async def turn(self, utterance: str, extraction: Extraction) -> VoiceTurn:
        return await self._run("voice_turn", lambda _s: Turn(channel=CHANNEL, utterance=utterance,
                                                              extraction=extraction), user_text=utterance)

    async def event(self, event: str) -> VoiceTurn:
        return await self._run(f"voice_{event}", lambda _s: Turn(channel=CHANNEL, event=event))

    async def current(self) -> SessionState:
        return await asyncio.to_thread(self.service.store.load, self.session_id)

    async def _run(self, name: str, build: Callable[[SessionState], Turn], **kw: Any) -> VoiceTurn:
        async with self._lock:
            out = await asyncio.to_thread(self.service._run, self.session_id, name, CHANNEL, build, **kw)
            state = await self.current()
            return VoiceTurn(state, out.plan, voice_line(self.service.spec, out.plan, state))


# --- node configs --------------------------------------------------------------


def voice_tools(spec: FlowSpec, node_id: str) -> list[str]:
    """Functions a node exposes on a call: the spec's tools, minus the ones that have no
    meaning mid-call (start_call) or belong to later packets (gmail tools: VOICE-004)."""
    return [t for t in spec.nodes[node_id]["tools"] if t == TOOL_NAME]


def _utterance(context: Any) -> str:
    if context is None:
        return ""
    for m in reversed(context.get_messages()):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c.strip()
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if isinstance(p, dict)).strip()
    return ""


class VoiceFlow:
    """Builds Flows nodes from the spec and routes `record_slots` into the brain."""

    def __init__(self, spec: FlowSpec, brain: BrainPort, *, context: Any = None,
                 on_graduated: Optional[OnGraduated] = None):
        self.spec, self.brain, self.context = spec, brain, context
        self.on_graduated = on_graduated
        self.last: Optional[VoiceTurn] = None
        self._tool = record_slots_tool(spec)
        self._graduated_said = False

    # -- functions --

    def record_slots_schema(self) -> "FlowsFunctionSchema":
        from pipecat_flows import FlowsFunctionSchema

        schema = self._tool["input_schema"]
        return FlowsFunctionSchema(name=TOOL_NAME, description=self._tool["description"],
                                   properties=schema["properties"], required=list(schema["required"]),
                                   handler=self.handle_record_slots)

    async def handle_record_slots(self, args: dict, flow_manager: Any = None) -> tuple[dict, "NodeConfig"]:
        """Flows handler: extraction in, brain-decided (result, next node) out."""
        utterance = _utterance(self.context)
        vt = await self.brain.turn(utterance, parse(self.spec, args if isinstance(args, dict) else {}))
        return await self._after(vt)

    async def opening(self) -> "NodeConfig":
        """Call connected: `call_started` moves the session onto voice; speak the brain's
        opening (greeting or "let's pick up") verbatim, then wait for the caller."""
        vt = await self.brain.event("call_started")
        _, node = await self._after(vt, opening=True)
        return node

    async def _after(self, vt: VoiceTurn, *, opening: bool = False) -> tuple[dict, "NodeConfig"]:
        self.last = vt
        plan = vt.plan
        result = {
            "node": plan.node,
            "say": vt.line,
            "brief": build_brief(plan, vt.state, CHANNEL, critical=plan.graduate),
            "absorbed": plan.absorbed,
            "graduated": plan.graduate,
        }
        if plan.graduate:
            if self.on_graduated and not self._graduated_said:
                self._graduated_said = True
                await self.on_graduated(vt.line)
            return result, self.node_config(TERMINAL_NODE, vt, speak=False)
        if opening:
            return result, self.node_config(plan.node, vt, speak=False, pre_say=vt.line)
        return result, self.node_config(plan.node, vt, speak=not plan.absorbed)

    def node_config(self, node_id: str, vt: Optional[VoiceTurn] = None, *, speak: bool = True,
                    pre_say: Optional[str] = None) -> "NodeConfig":
        n = self.spec.nodes[node_id]
        functions = [self.record_slots_schema()] if TOOL_NAME in voice_tools(self.spec, node_id) else []
        cfg: dict[str, Any] = {
            "name": node_id,
            "role_message": ROLE,
            "task_messages": [{"role": "developer", "content": self._task(node_id, n, vt, speak)}],
            "functions": functions,
            "respond_immediately": speak and bool(functions or n["kind"] == "say"),
        }
        if pre_say:
            cfg["pre_actions"] = [{"type": "tts_say", "text": pre_say}]
        return cfg  # type: ignore[return-value]

    def _task(self, node_id: str, n: dict, vt: Optional[VoiceTurn], speak: bool) -> str:
        parts = [f"Current step: {node_id}. Goal: {' '.join(str(n.get('goal', '')).split())}"]
        if n["kind"] == "terminal":
            parts.append("Setup is finished and the closing line is spoken for you. Do not say anything more.")
            return " ".join(parts)
        if vt is not None and speak:
            brief = build_brief(vt.plan, vt.state, CHANNEL)
            parts.append("What to say now, decided by the system: " + json.dumps(brief, sort_keys=True))
            if vt.line:
                parts.append(f'Say this, in your own words if it sounds more natural, keeping every fact: "{vt.line}"')
        elif vt is not None and vt.plan.absorbed:
            parts.append("The caller's last words were noise or a fragment: stay quiet and let them continue.")
        parts.append(f"When the caller speaks, call {TOOL_NAME} first.")
        return " ".join(parts)
