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

Call hand-off (VOICE-003): `opening(reconnect=True)` resumes a dropped call with the
brain's "we got cut off" line (a fresh call that continues a chat says it is picking up
instead); `typed_turn` acknowledges, by voice, text the user typed in the chat during the
call — that turn already ran once through the text path (same serialized session), so
the voice side only speaks the brain's result and moves to its node, never re-extracts.
Lease/grace live in `handoff.py`; silence nudges in `silence.py`.

Gmail on the call (VOICE-004-lite): the brain already pushes `gmail_connect_card` on every
gmail-node turn (same card, same OAuth, same `SessionService.gmail_connected` fill as text),
so the call only speaks around it: the ask adds the "type your email in the chat" escape,
a typed email is acknowledged without spelling it back (spoken NATO capture was cut; a
typed/spoken email stays a `candidate` until OAuth), and out-of-band results reach the call
through the session's turn listeners — `gmail_oauth` (connected; a partial grant is stated
plainly) and `gmail_failed` (cancelled / closed consent: offer retry, type-it, or skip).

Tool-call guard (GUARD-001): every Flows handler is wrapped so a function the brain's
current node does not expose on the call (or any unknown function, via the LLM's
catch-all `handle_unknown_function`) is rejected: no brain turn, no state change, a
`rejected_tool_call` record + warning, and the node's line is re-spoken. The voice LLM
is told to say the brain's line (shortened at most), and `speech_context` feeds the
output guard (`speech_guard.py`) that screens what it actually says.

Latency (LAT-001): with `direct_speech` (default) the brain's templated line is final, so
after `record_slots` it is spoken straight through TTS (`tts_say` pre-action, same path as
the opening) and the node does not re-run the LLM: the LLM is used only for extraction.
Barge-in is unchanged (a TTS say is interruptible like LLM speech). `timer` (timing.py)
receives the handler span and the store's round trips for the per-turn timing line.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, Protocol

from ..brain import engine
from ..brain.engine import Extraction, ResponsePlan, Turn
from ..brain.spec import TERMINAL_NODE, TOOL_REGISTRY, FlowSpec
from ..brain.state import SessionState
from ..llm import templates as T
from ..llm.extract import parse
from ..llm.phrase import answer_line, build_brief, critical_lines, template_reply
from ..llm.prompts import approved_facts
from ..llm.schema import TOOL_NAME, record_slots_tool
from ..obs.tracing import NoopTracer, Tracer
from .timing import TurnTimer

from loguru import logger

if TYPE_CHECKING:  # pipecat is a runtime extra; the brain-level pieces import without it
    from pipecat_flows import FlowsFunctionSchema, NodeConfig

CHANNEL = "voice"
# T.GREET pitches the call itself; on the call the greeting just opens the questions.
VOICE_GREET = "Hi, it's Persona! Let's get you set up. It's just a few quick questions."
VOICE_CONTINUE = "Hi, it's Persona! Let's pick up where we left off in the chat."
TYPED_ACK = "I see you typed that in the chat."
GMAIL_TYPE_IT = "Or, if it's easier, type your email in the chat."
GMAIL_TYPED = ("Thanks, I see the email you typed. To actually connect it, tap Continue with Google "
               "on your screen and sign in there.")
GMAIL_FAILED = ("Looks like Google didn't finish signing you in. You can tap Try again on your screen, "
                "or just say skip and we'll do it later.")
GMAIL_CONNECTED = "Gmail's connected."
# Scope -> what the assistant can't do without it (ARCHITECTURE §11 partial grants).
GMAIL_CAPABILITY = {
    "https://www.googleapis.com/auth/gmail.readonly": "read your email",
    "https://www.googleapis.com/auth/gmail.modify": "organize your inbox",
    "https://www.googleapis.com/auth/gmail.send": "send email",
}
OnGraduated = Callable[[str], Awaitable[None]]
# GUARD-001: the voice LLM delivers the brain's line; it may shorten it, never extend it.
SAY_LINE = ('Say this line; you may shorten it, but never add facts, questions or offers: "{line}"')

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
    gm = state.slots.get("gmail")
    spelled = T.email_readback(gm.value) if gm and gm.value and gm.status == "candidate" else None
    # Never spell an email out on the call (NATO capture was cut): the card + chat carry it.
    crit = [c for c in critical_lines(plan, state, CHANNEL) if c != spelled]
    name_line = _name_line(plan, state)
    if name_line:
        # NAME-001: the name read-back / re-ask replaces the generic confirm or ask line.
        generic = T.confirm_line(NAME, state.slots[NAME].value, CHANNEL) if plan.confirm == NAME else None
        crit = [c for c in crit if c != generic] + [name_line]
    rest = _name_ack(plan, state, template_reply(spec, plan, state, CHANNEL, critical=bool(crit)))
    gmail = ""
    if plan.ask == "gmail" and not crit:
        # Live test 2026-09-27: "type your email in the chat" misled (typing never connects).
        gmail = GMAIL_TYPED if spelled else ""
    return " ".join(p for p in [VOICE_GREET if greet else "", rest, gmail, *crit] if p)


NAME = "user_name"


def _name_line(plan: ResponsePlan, state: SessionState) -> str:
    """NAME-001 read-back lines: the templated name confirm (spelled), or after a "no" the
    re-ask / spell-it-for-me ask. Empty when the plan has no name business."""
    sv = state.slots.get(NAME)
    if plan.confirm == NAME and sv and sv.value:
        # A repeated read-back (no yes/no yet) says "So that's ...?", not "Nice to meet you" again.
        attempt = max(sv.confirm_attempts, 2) if plan.reconfirm else sv.confirm_attempts
        return T.name_confirm_line(sv.value, CHANNEL, attempt)
    if NAME in plan.denied and plan.ask == NAME:
        return T.NAME_SPELL_ASK if plan.spell == NAME else T.NAME_REASK
    return ""


def _name_ack(plan: ResponsePlan, state: SessionState, rest: str) -> str:
    """After a read-back the name ack says the (confirmed or kept) name, not "nice to meet you" twice."""
    sv = state.slots.get(NAME)
    if NAME not in plan.acknowledge or not sv or not sv.value or not sv.confirm_attempts:
        return rest
    line = T.NAME_KEEP if sv.low_confidence else T.NAME_CONFIRMED
    return rest.replace(T.ack_for(NAME, sv.value), line.format(v=sv.value.strip().rstrip(".")))


def gmail_oauth_line(data: Optional[dict]) -> str:
    """Spoken right after the OAuth callback filled gmail; a partial grant is stated plainly."""
    granted = set((data or {}).get("scopes") or [])
    missing = [can for scope, can in GMAIL_CAPABILITY.items() if granted and scope not in granted]
    if not missing:
        return GMAIL_CONNECTED
    cant = missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + " or " + missing[-1]
    return f"{GMAIL_CONNECTED} Google left out permission to {cant}, so I can do the rest, and you can allow it anytime."


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
        self.last_db: Optional[Any] = None    # store.perf.DbTiming of the last turn (LAT-001)

    async def turn(self, utterance: str, extraction: Extraction) -> VoiceTurn:
        return await self._run("voice_turn", lambda _s: Turn(channel=CHANNEL, utterance=utterance,
                                                              extraction=extraction), user_text=utterance)

    async def event(self, event: str) -> VoiceTurn:
        return await self._run(f"voice_{event}", lambda _s: Turn(channel=CHANNEL, event=event))

    async def current(self) -> SessionState:
        return await asyncio.to_thread(self.service.store.load, self.session_id)

    def watch_text(self, cb: Callable[[Any], None]) -> Callable[[], None]:
        """`cb(outcome)` after each out-of-band turn committed on this session: typed text
        (EC-28), the OAuth fill, or a failed consent (`outcome.source`, VOICE-004)."""
        return self.service.add_text_listener(self.session_id, lambda _sid, out: cb(out))

    async def _run(self, name: str, build: Callable[[SessionState], Turn], **kw: Any) -> VoiceTurn:
        from ..store import VersionConflictError, perf  # lazy: psycopg only where a store exists

        def run() -> tuple[Any, Any]:
            with perf.collect() as db:
                try:
                    return self.service._run(self.session_id, name, CHANNEL, build, **kw), db
                except VersionConflictError:
                    # Another process committed first (in-process turns queue on the service's
                    # session lock). Nothing of ours was written: re-apply on the fresh state.
                    return self.service._run(self.session_id, name, CHANNEL, build, **kw), db

        async with self._lock:
            out, self.last_db = await asyncio.to_thread(run)
            # The service hands back the state it committed: no re-load round trip (LAT-001).
            state = out.state if out.state is not None else await self.current()
            return VoiceTurn(state, out.plan, voice_line(self.service.spec, out.plan, state))


# --- node configs --------------------------------------------------------------


def voice_tools(spec: FlowSpec, node_id: str) -> list[str]:
    """Functions a node exposes on a call: the spec's tools, minus the ones that have no
    meaning mid-call (start_call) or that code does instead of the LLM: the brain pushes
    the Gmail card on every gmail-node turn and the ask always offers "type it"
    (push_gmail_connect / request_typed_email); spoken NATO capture was cut."""
    return [t for t in spec.nodes[node_id]["tools"] if t == TOOL_NAME]


def _text_of(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict)).strip()
    return ""


def _is_spoken(m: Any) -> bool:
    """A caller transcript message (not assistant/developer text or a tool result)."""
    if not isinstance(m, dict) or m.get("role") != "user":
        return False
    c = m.get("content")
    return isinstance(c, str) or (isinstance(c, list) and all(
        isinstance(p, dict) and p.get("type", "text") == "text" for p in c))


def _utterance_since(context: Any, seen: int) -> Optional[str]:
    """Caller messages added since the last handled turn (message index `seen`), joined.
    None when the context shrank (a reset) or holds nothing new: use `_utterance`."""
    if context is None:
        return None
    msgs = context.get_messages()
    if seen > len(msgs):
        return None
    text = " ".join(t for t in (_text_of(m) for m in msgs[seen:] if _is_spoken(m)) if t)
    return text or None


def _utterance(context: Any) -> str:
    """The caller's latest turn. Smart Turn can end a turn at a short pause (a spelled name:
    "My name is Darran. D. A." ... "R. R. A. N."), the reply is interrupted by the rest, and
    the context then holds several consecutive user messages: join them (LAT-002 live probe:
    the last fragment alone made the name "An")."""
    if context is None:
        return ""
    parts: list[str] = []
    for m in reversed(context.get_messages()):
        if not _is_spoken(m):   # assistant / developer / tool result: the caller's turn starts after it
            if parts:
                break
            continue
        parts.append(_text_of(m))
    return " ".join(p for p in reversed(parts) if p)


class VoiceFlow:
    """Builds Flows nodes from the spec and routes `record_slots` into the brain."""

    def __init__(self, spec: FlowSpec, brain: BrainPort, *, context: Any = None,
                 on_graduated: Optional[OnGraduated] = None, direct_speech: bool = True,
                 timer: Optional[TurnTimer] = None):
        self.spec, self.brain, self.context = spec, brain, context
        self.on_graduated = on_graduated
        self.direct_speech = direct_speech            # speak the brain's line via TTS, skip LLM #2
        self.timer = timer
        self.last: Optional[VoiceTurn] = None
        # LAT-001/003: compact (~1/3 the output tokens); VQA-001: + the approved-answer enum.
        self._tool = record_slots_tool(spec, compact=True,
                                       answers={k: v[0] for k, v in T.APPROVED_ANSWERS.items()})
        self._graduated_said = False
        self._ctx_seen = 0                             # context messages already handled as turns
        self.stt_confidence: Optional[float] = None   # last final transcript's STT confidence
        self.node: Optional[str] = None               # the brain's current node on this call
        self.rejections: list[dict] = []              # rejected_tool_call records (GUARD-001)
        self._facts: Optional[str] = None

    def note_stt_confidence(self, confidence: Optional[float]) -> None:
        """Called by the pipeline with each final transcript's STT confidence (Deepgram)."""
        self.stt_confidence = confidence

    # -- functions --

    def record_slots_schema(self) -> "FlowsFunctionSchema":
        from pipecat_flows import FlowsFunctionSchema

        schema = self._tool["input_schema"]
        return FlowsFunctionSchema(name=TOOL_NAME, description=self._tool["description"],
                                   properties=schema["properties"], required=list(schema["required"]),
                                   handler=self.guarded(TOOL_NAME, self.handle_record_slots))

    # -- tool-call guard (GUARD-001) --

    def call_rejection(self, name: str) -> Optional[str]:
        """Why a call to `name` may not run now, or None. Scoped to the brain's current node:
        Flows may keep earlier nodes' functions registered, so the advertised list alone is
        not proof."""
        if name not in TOOL_REGISTRY:
            return "unknown_function"
        if self.node is None:
            return "no_active_node"
        if name not in voice_tools(self.spec, self.node):
            return "not_on_node"
        return None

    def guarded(self, name: str, handler: Callable[..., Awaitable[tuple[dict, Any]]]):
        async def run(args: dict, flow_manager: Any = None) -> tuple[dict, Any]:
            reason = self.call_rejection(name)
            if reason is not None:
                return self.reject_call(name, reason)
            return await handler(args, flow_manager)
        return run

    def reject_call(self, name: str, reason: str) -> tuple[dict, Optional["NodeConfig"]]:
        """No brain turn, no state change: log it and re-speak the current node's line."""
        rec = {"type": "rejected_tool_call", "function": name, "node": self.node, "reason": reason}
        self._mark_seen()
        self.rejections.append(rec)
        logger.warning(f"voice LLM called {name!r} on node {self.node!r}: rejected ({reason})")
        line = self.last.line if self.last else ""
        result = {"rejected": True, "reason": reason, "node": self.node, "say": line}
        if self.node is None or self.node == TERMINAL_NODE or self.last is None:
            return result, None
        if self.direct_speech and line:
            return result, self.node_config(self.node, self.last, speak=False, pre_say=line)
        return result, self.node_config(self.node, self.last, speak=True)

    def _nothing_new(self) -> bool:
        """True when a turn was already handled on this call and the context holds no caller
        message after it (only possible with a live context)."""
        if self.context is None or self._ctx_seen == 0:
            return False
        msgs = self.context.get_messages()
        return self._ctx_seen <= len(msgs) and not any(_is_spoken(m) for m in msgs[self._ctx_seen:])

    def _mark_seen(self) -> None:
        self._ctx_seen = len(self.context.get_messages()) if self.context is not None else 0

    async def handle_unknown_function(self, params: Any) -> None:
        """Pipecat catch-all (`llm.register_function(None, ...)`): any function with no
        registered handler is rejected the same way instead of Pipecat's generic error."""
        name = getattr(params, "function_name", "?")
        result, _ = self.reject_call(name, self.call_rejection(name) or "no_handler")
        await params.result_callback(result)

    # -- output guard context --

    def speech_context(self) -> Optional[dict]:
        """What the voice LLM may say right now, for the output guard: the approved facts,
        slot whys and values, and the brain's line/brief. None before the call opens."""
        if self.last is None:
            return None
        if self._facts is None:
            self._facts = approved_facts() + "\n" + "\n".join(d["why"] for d in self.spec.slots.values())
        st = self.last.state
        values = "\n".join(v.value for v in st.slots.values() if v.value)
        brief = json.dumps(build_brief(self.last.plan, st, CHANNEL), sort_keys=True)
        return {"allowed": "\n".join([self._facts, values, self.last.line, brief]),
                "gmail_connected": st.filled("gmail"), "fallback": self.last.line}

    def ack_allowed(self) -> bool:
        """LAT-003: may a quick ack ("Got it.") be spoken while this turn's extraction runs?
        Never before the call has opened or after graduation, and never around the name
        read-back (the caller is saying or spelling their name: the ack would land in their
        pauses and the next line is the read-back itself)."""
        if self.last is None or self.node in (None, TERMINAL_NODE):
            return False
        plan = self.last.plan
        return not (plan.ask == NAME or plan.confirm == NAME or NAME in plan.denied or plan.spell)

    def answer_line(self, answer_id: Any) -> Optional[str]:
        """VQA-001: the approved spoken answer for an extraction's `answer` id, screened by the
        same output guard as every spoken sentence; None for no/unknown id or a guard miss."""
        line = answer_line(self.spec, answer_id)
        if line is None and isinstance(answer_id, str) and answer_id in T.APPROVED_ANSWERS:
            logger.warning(f"voice answer {answer_id!r} failed the output guard; using the default")
        return line

    def _with_answer(self, vt: VoiceTurn, answer_id: Any) -> VoiceTurn:
        """Put the approved answer where the brain's generic question reply sits (or first):
        the node's ask the brain planned still follows it, so setup keeps moving."""
        line = self.answer_line(answer_id)
        plan = vt.plan
        if not line or not vt.line or plan.absorbed or plan.graduate or "prompt_injection" in plan.respond_to:
            return vt
        vt.line = T.with_answer(vt.line, line)   # idempotent: the brain's line may already carry it
        return vt

    async def handle_record_slots(self, args: dict, flow_manager: Any = None) -> tuple[dict, "NodeConfig"]:
        """Flows handler: extraction in, brain-decided (result, next node) out."""
        if self.timer is not None:
            self.timer.tool_call()
        started = time.perf_counter()
        if self._nothing_new():
            # A second extraction call with no new caller words (a retried or repeated LLM
            # run): applying it would run the same turn twice. Stay on the node, say nothing.
            logger.warning(f"record_slots with no new caller message on node {self.node!r}: ignored")
            result = {"ignored": True, "reason": "no_new_utterance", "node": self.node}
            if self.node is None or self.node == TERMINAL_NODE or self.last is None:
                return result, None  # type: ignore[return-value]
            return result, self.node_config(self.node, self.last, speak=False)
        utterance = _utterance_since(self.context, self._ctx_seen) or _utterance(self.context)
        self._mark_seen()
        raw = args if isinstance(args, dict) else {}
        x = parse(self.spec, raw)
        if self.stt_confidence is not None and NAME in x.slots:
            # The brain sees the weaker of the STT and extraction confidence for the name.
            x.confidences[NAME] = min(self.stt_confidence, x.confidences.get(NAME, 1.0))
        vt = await self.brain.turn(utterance, x)
        if self.timer is not None:
            db = getattr(self.brain, "last_db", None)
            self.timer.handler_done(handler_ms=(time.perf_counter() - started) * 1000, node=vt.plan.node,
                                    direct=self.direct_speech and bool(vt.line) and not vt.plan.absorbed,
                                    db_ms=db.ms if db else None, db_calls=db.calls if db else None)
        return await self._after(self._with_answer(vt, raw.get("answer")))

    async def opening(self, *, reconnect: bool = False) -> "NodeConfig":
        """Call connected: `call_started` moves the session onto voice; speak the brain's
        opening verbatim, then wait for the caller. The brain's resume line ("we got cut
        off") is right for a reconnect inside the grace window; a fresh call that
        continues a chat says it is picking up from the chat instead."""
        self._mark_seen()   # anything already in the context (e.g. chat history) is not a call turn
        vt = await self.brain.event("call_started")
        cut_off = T.RESUME[CHANNEL]
        if not reconnect and vt.plan.resume and vt.line.startswith(cut_off):
            vt.line = VOICE_CONTINUE + vt.line[len(cut_off):]
        _, node = await self._after(vt, opening=True)
        return node

    async def typed_turn(self, plan: ResponsePlan, *, source: str = "text",
                         data: Optional[dict] = None) -> Optional["NodeConfig"]:
        """A turn landed out of band during the call (already committed by the text path or
        the OAuth callback): say it by voice and move to the brain's node. None if nothing
        to say. `source`: text (typed in the chat) | gmail_oauth | gmail_failed."""
        state = await self.brain.current()
        if source == "gmail_failed":
            # No state change (the card shows the error); only while the call is still at gmail.
            if state.node != "gmail" or state.filled("gmail"):
                return None
            vt = VoiceTurn(state, self.last.plan if self.last else ResponsePlan(node="gmail"), GMAIL_FAILED)
            _, node = await self._after(vt, opening=True)
            return node
        if plan.absorbed:
            return None
        line = voice_line(self.spec, plan, state)
        if source == "gmail_oauth":
            line = f"{gmail_oauth_line(data)} {line}".strip()
        elif not plan.graduate:
            line = f"{TYPED_ACK} {line}".strip()
        vt = VoiceTurn(state, plan, line)
        _, node = await self._after(vt, opening=True)
        return node

    async def _after(self, vt: VoiceTurn, *, opening: bool = False) -> tuple[dict, "NodeConfig"]:
        self.last = vt
        self.node = TERMINAL_NODE if vt.plan.graduate else vt.plan.node
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
        if opening or (self.direct_speech and vt.line and not plan.absorbed):
            # The brain's line is final: TTS says it verbatim, no phrasing LLM run (LAT-001).
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
                parts.append(SAY_LINE.format(line=vt.line))
        elif vt is not None and vt.plan.absorbed:
            parts.append("The caller's last words were noise or a fragment: stay quiet and let them continue.")
        parts.append(f"When the caller speaks, call {TOOL_NAME} first.")
        return " ".join(parts)
