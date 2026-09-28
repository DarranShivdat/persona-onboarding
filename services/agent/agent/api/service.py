"""Session service: the one turn path (load -> extract -> brain.apply -> phrase ->
version-checked persist + events), shared by text turns, session open, and the
OAuth gmail fill. HTTP-free so voice (VOICE-001) can call it too.

UI pushes are persisted as `session_events(kind='ui_push', payload={type, data})`
in the same transaction as the state change; the SSE stream replays them by id.

Turns are serialized per session (in-process lock + the DB version check), so text typed
during a live call and the call's own turns form one stream (EC-28). Call lifecycle
(VOICE-003): acquire / take over / heartbeat / drop (grace) / reconnect / end; ending a
call outside the grace window runs the brain's `call_ended` event and pushes
`call_resume` naming what's left, so the chat picks up (EC-01).
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import threading
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional

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


SLOT_LABELS = {"agent_name": "naming your assistant", "user_name": "your name",
               "need": "what you'd like help with", "gmail": "connecting Gmail"}


def remaining_slots(spec: FlowSpec, state: SessionState) -> list[str]:
    return [s for s, d in spec.slots.items() if d.get("required") and not state.filled(s)]


def resume_copy(remaining: list[str]) -> str:
    """Chat copy after a call ends (templated, never LLM output)."""
    if not remaining:
        return "The call ended, but everything's saved and you're all set."
    labels = [SLOT_LABELS.get(s, s.replace("_", " ")) for s in remaining]
    left = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
    return f"The call ended, but everything so far is saved. Still left: {left}. You can call back or keep going here."


TurnListener = Callable[[str, "TurnOutcome"], None]


def ui_push(type_: str, data: dict, *, channel: Optional[str], trace_id: Optional[str]) -> dict:
    return {"kind": "ui_push", "channel": channel, "trace_id": trace_id, "payload": {"type": type_, "data": data}}


class SessionService:
    def __init__(self, *, store: PgStore, llm: TurnLlm, spec: FlowSpec, tracer: Tracer, notifier: Notifier):
        self.store, self.llm, self.spec, self.tracer, self.notifier = store, llm, spec, tracer, notifier
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._listeners: dict[str, list[TurnListener]] = {}

    def session_lock(self, session_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.RLock())

    def add_text_listener(self, session_id: str, cb: TurnListener) -> Callable[[], None]:
        """`cb(session_id, outcome)` after each committed text turn (a live call uses this
        to acknowledge typed input by voice, EC-28). Called from the committing thread."""
        with self._locks_guard:
            self._listeners.setdefault(session_id, []).append(cb)

        def remove() -> None:
            with self._locks_guard:
                if cb in self._listeners.get(session_id, []):
                    self._listeners[session_id].remove(cb)
        return remove

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

        with self.session_lock(session_id):
            self.reconcile_call(session_id)
            out = self._run(session_id, "text_turn", "text", build, user_text=text,
                            expected_version=expected_version)
        with self._locks_guard:
            listeners = list(self._listeners.get(session_id, ()))
        for cb in listeners:
            try:
                cb(session_id, out)
            except Exception:  # noqa: BLE001 - a listener must never fail the committed turn
                pass
        return out

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

    def _run(self, session_id, name, channel, build, **kw) -> TurnOutcome:
        with self.session_lock(session_id):
            return self._run_locked(session_id, name, channel, build, **kw)

    def _run_locked(self, session_id, name, channel, build, *, user_text=None, expected_version=None,
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

    def _call_push(self, session_id: str, data: dict) -> None:
        self.store.append_events(session_id, [ui_push("call_state", data, channel="voice", trace_id=None)])
        self.notifier.notify(session_id)

    def acquire_call(self, session_id: str, *, ttl_s: float, take_over: bool = False) -> CallLease:
        lease = self.store.acquire_call_lease(session_id, ttl_s=ttl_s, take_over=take_over)
        if lease.replaced:
            self._call_push(session_id, {"state": "ended", "call_id": lease.replaced, "reason": "taken_over"})
        self._call_push(session_id, {"state": "ringing", "call_id": lease.call_id})
        return lease

    def heartbeat_call(self, session_id: str, call_id: str, *, ttl_s: float) -> Optional[CallLease]:
        return self.store.renew_call_lease(session_id, call_id, ttl_s=ttl_s)

    def call_dropped(self, session_id: str, call_id: str, *, grace_s: float) -> bool:
        """Media dropped without a hangup: the lease now lives only for the grace window."""
        lease = self.store.renew_call_lease(session_id, call_id, ttl_s=grace_s)
        if lease:
            self._call_push(session_id, {"state": "reconnecting", "call_id": call_id, "grace_s": grace_s})
        return lease is not None

    def call_reconnected(self, session_id: str, call_id: str, *, ttl_s: float) -> Optional[CallLease]:
        lease = self.store.renew_call_lease(session_id, call_id, ttl_s=ttl_s, reconnect=True)
        if lease:
            self._call_push(session_id, {"state": "live", "call_id": call_id, "resumed": True})
        return lease

    def release_call(self, session_id: str, call_id: str, *, reason: str) -> bool:
        return self.end_call(session_id, call_id, reason=reason)[0]

    def end_call(self, session_id: str, call_id: str, *, reason: str) -> tuple[bool, Optional[TurnOutcome]]:
        """Hangup outside the grace window: release the lease and, if the call moved the
        session onto voice, hand it back to the chat with a resume message (EC-01).
        Idempotent: only the holder's first end releases (and resumes)."""
        with self.session_lock(session_id):
            released = self.store.release_call_lease(session_id, call_id, reason=reason)
            if not released:
                return False, None
            self._call_push(session_id, {"state": "ended", "call_id": call_id, "reason": reason})
            if reason == "taken_over":
                return True, None
            return True, self._resume_in_chat(session_id, call_id, reason)

    def reconcile_call(self, session_id: str) -> Optional[TurnOutcome]:
        """A session still on voice with no live lease lost its call without a clean end
        (process restart, grace timer lost): finish it now so chat is never stuck."""
        with self.session_lock(session_id):
            if self.store.load(session_id).active_channel != "voice" or self.store.lease(session_id):
                return None
            holder = self.store.lease_holder(session_id)
            if holder:
                return self.end_call(session_id, holder, reason="network_timeout")[1]
            return self._resume_in_chat(session_id, None, "network_timeout")

    def _resume_in_chat(self, session_id: str, call_id: Optional[str], reason: str) -> Optional[TurnOutcome]:
        state = self.store.load(session_id)
        if state.active_channel != "voice":
            return None  # the call never connected (or already handed back): nothing to resume
        left = remaining_slots(self.spec, state)
        data = {"call_id": call_id, "reason": reason, "remaining": left, "message": resume_copy(left),
                "graduated": state.graduated, "can_call_back": not state.graduated}
        return self._run_locked(session_id, "call_ended", "text", lambda s: Turn(channel="text", event="call_ended"),
                                extra_pushes=[("call_resume", data)])
