"""Session service: the one turn path (load -> extract -> brain.apply -> phrase ->
version-checked persist + events), shared by text turns, session open, and the
OAuth gmail fill. HTTP-free so voice (VOICE-001) can call it too.

UI pushes are persisted as `session_events(kind='ui_push', payload={type, data})`
in the same transaction as the state change; the SSE stream replays them by id.
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import threading
from dataclasses import asdict, dataclass
from typing import Any, Optional

from ..brain import engine
from ..brain.engine import Extraction, ResponsePlan, Turn
from ..brain.spec import FlowSpec
from ..brain.state import Channel, SessionState
from ..obs.meta import turn_metadata
from ..obs.tracing import Tracer
from ..store import CallLease, PgStore, VersionConflictError
from .llm import TurnLlm


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Notifier:
    """Wakes SSE streams in this process after a commit (cross-thread safe).
    Streams also poll the DB, so multi-process deployments still converge."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiters: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}

    def subscribe(self, session_id: str) -> tuple[asyncio.AbstractEventLoop, asyncio.Event]:
        w = (asyncio.get_running_loop(), asyncio.Event())
        with self._lock:
            self._waiters.setdefault(session_id, set()).add(w)
        return w

    def unsubscribe(self, session_id: str, w) -> None:
        with self._lock:
            self._waiters.get(session_id, set()).discard(w)

    def notify(self, session_id: str) -> None:
        with self._lock:
            waiters = list(self._waiters.get(session_id, ()))
        for loop, ev in waiters:
            loop.call_soon_threadsafe(ev.set)


@dataclass
class TurnOutcome:
    reply: str
    plan: ResponsePlan
    snapshot: dict
    trace_id: str


def snapshot(spec: FlowSpec, state: SessionState, lease: Optional[CallLease]) -> dict[str, Any]:
    slots = {}
    for name in spec.slots:
        sv = state.slots.get(name)
        slots[name] = {
            "status": sv.status if sv else "empty",
            "value": sv.value if sv else None,
            "source": sv.source if sv else None,
            "needs_confirm": bool(sv and sv.needs_confirm),
        }
    return {
        "id": state.session_id,
        "version": state.version,
        "status": "graduated" if state.graduated else "active",
        "node": state.node,
        "active_channel": state.active_channel,
        "slots": slots,
        "deferred_prompts": list(state.deferred_prompts),
        "graduated": state.graduated,
        "call": {
            "live": lease is not None,
            "call_id": lease.call_id if lease else None,
            "expires_at": lease.expires_at.isoformat() if lease else None,
        },
    }


def ui_push(type_: str, data: dict, *, channel: Optional[str], trace_id: Optional[str]) -> dict:
    return {"kind": "ui_push", "channel": channel, "trace_id": trace_id, "payload": {"type": type_, "data": data}}


class SessionService:
    def __init__(self, *, store: PgStore, llm: TurnLlm, spec: FlowSpec, tracer: Tracer, notifier: Notifier):
        self.store, self.llm, self.spec, self.tracer, self.notifier = store, llm, spec, tracer, notifier

    # --- lifecycle -----------------------------------------------------------

    def create(self) -> tuple[str, str, TurnOutcome]:
        token = secrets.token_urlsafe(32)
        st = self.store.create_session(flow_version=int(self.spec.raw["version"]), token_hash=hash_token(token))
        outcome = self._run(st.session_id, "session_open", "text", lambda s: Turn(channel="text", event="open"))
        return st.session_id, token, outcome

    def check_token(self, session_id: str, token: Optional[str]) -> bool:
        stored = self.store.token_hash(session_id)
        return bool(token and stored) and secrets.compare_digest(stored, hash_token(token))

    def get_snapshot(self, session_id: str) -> dict:
        return snapshot(self.spec, self.store.load(session_id), self.store.lease(session_id))

    # --- turns ---------------------------------------------------------------

    def text_turn(self, session_id: str, text: str, *, expected_version: Optional[int] = None) -> TurnOutcome:
        def build(state: SessionState) -> Turn:
            x = self.llm.extract(spec=self.spec, state=state, utterance=text, channel="text")
            return Turn(channel="text", utterance=text, extraction=x)

        return self._run(session_id, "text_turn", "text", build, user_text=text, expected_version=expected_version)

    def gmail_connected(self, session_id: str, *, email: str, google_sub: str, scopes: list[str],
                        sealed: Optional[dict] = None) -> TurnOutcome:
        """Server-to-server from the OAuth callback: the only path that fills `gmail`.
        `sealed` = GmailService.seal(...) output (ciphertext + token_status; never plaintext)."""
        state0 = self.store.load(session_id)
        channel: Channel = state0.active_channel or "text"

        def build(state: SessionState) -> Turn:
            return Turn(channel=channel, extraction=Extraction(slots={"gmail": email}), oauth_verified=True)

        gmail = {"email": email, "google_sub": google_sub, "scopes": scopes, **(sealed or {})}
        return self._run(session_id, "gmail_oauth", channel, build, gmail=gmail,
                         extra_pushes=[("gmail_connected", {"email": email})])

    def _run(self, session_id, name, channel, build, *, user_text=None, expected_version=None,
             gmail=None, extra_pushes=()) -> TurnOutcome:
        state = self.store.load(session_id)
        if expected_version is not None and expected_version != state.version:
            raise VersionConflictError(session_id, expected_version)
        lease = self.store.lease(session_id)
        tid = self.tracer.start_trace(session_id=session_id, channel=channel, name=name,
                                      metadata=turn_metadata(self.spec, state.node, version=state.version))
        turn = build(state)
        self.tracer.span(tid, name="extract", input=user_text, output=asdict(turn.extraction))
        result = engine.apply(self.spec, state, turn)
        self.tracer.span(tid, name="brain.apply", output=asdict(result.plan), metadata={"events": result.events})
        reply = "" if result.plan.absorbed else self.llm.phrase(
            spec=self.spec, state=result.state, plan=result.plan, channel=channel)
        self.tracer.span(tid, name="phrase", output=reply)

        new = result.state
        new.version = state.version + 1
        ev: list[dict] = []

        def add(kind: str, payload: dict) -> None:
            ev.append({"kind": kind, "channel": channel, "trace_id": tid, "payload": payload})

        if user_text is not None:
            add("user_utterance", {"text": user_text})
            add("extraction", asdict(turn.extraction))
        for e in result.events:
            add("transition" if e["type"] == "transition" else "brain", e)
        if reply:
            add("bot_utterance", {"text": reply, "node": result.plan.node})

        def push(type_: str, data: dict) -> None:
            ev.append(ui_push(type_, data, channel=channel, trace_id=tid))

        if user_text is not None:
            push("transcript", {"role": "user", "text": user_text, "channel": channel})
        for t, data in extra_pushes:
            push(t, data)
        if reply:
            push("transcript", {"role": "assistant", "text": reply, "channel": channel})
        snap = snapshot(self.spec, new, lease)
        push("state", snap)
        for p in result.plan.push_ui:
            push(p, {"node": result.plan.node})
        if result.plan.graduate and not state.graduated:
            push("graduate", {"deferred": list(result.plan.deferred)})

        self.store.commit(new, expected_version=state.version, events=ev, gmail=gmail)
        self.notifier.notify(session_id)
        self.tracer.flush()
        return TurnOutcome(reply=reply, plan=result.plan, snapshot=snap, trace_id=tid)

    # --- call lease ----------------------------------------------------------

    def acquire_call(self, session_id: str, *, ttl_s: float) -> CallLease:
        lease = self.store.acquire_call_lease(session_id, ttl_s=ttl_s)
        self.store.append_events(session_id, [
            ui_push("call_state", {"state": "ringing", "call_id": lease.call_id}, channel="voice", trace_id=None)])
        self.notifier.notify(session_id)
        return lease

    def release_call(self, session_id: str, call_id: str, *, reason: str) -> bool:
        released = self.store.release_call_lease(session_id, call_id, reason=reason)
        if released:
            self.store.append_events(session_id, [
                ui_push("call_state", {"state": "ended", "call_id": call_id, "reason": reason},
                        channel="voice", trace_id=None)])
            self.notifier.notify(session_id)
        return released
