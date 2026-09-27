"""Call lease, reconnect grace and hangup hand-off (ARCHITECTURE §8; EC-01/02/04).

No Pipecat dependency: the API routes and the call pipeline both drive one `CallControl`
per process, which owns the per-call lifecycle on top of `SessionService`:

    start (lease; 409 unless take_over) -> heartbeat ... -> end        (hangup, EC-01)
                                                 \\-> dropped -> reconnect (EC-04)
                                                             \\-> grace expires -> end

- A media drop (client disconnected without hanging up) keeps the lease only for the
  grace window; a reconnect with the same call_id inside it resumes that call (the new
  pipeline opens with "we got cut off"). After the window it is a hangup.
- Take-over (EC-02) steals the lease atomically in the store and hangs up the old call's
  in-process pipeline; a pipeline in another process notices on its next heartbeat
  (lease lost -> it ends itself), so two bots never keep talking for one session.
- Ending outside grace releases the lease and runs the brain's `call_ended` event, which
  pushes `call_resume` (what's left) to the chat.

Timers use an injectable `sleep`, so tests drive them without wall-clock waits.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional

from loguru import logger

Hangup = Callable[[str], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]

# Teardown reasons that mean "the media went away", not "someone hung up".
GRACE_REASONS = frozenset({"client_disconnected", "network_drop", "ice_failed"})
# Reasons where another call now owns the session: never resume in chat.
LOST_REASONS = frozenset({"taken_over", "lease_lost"})


@dataclass(frozen=True)
class HandoffSettings:
    lease_ttl_s: float = 120.0
    heartbeat_s: float = 30.0
    grace_s: float = 20.0

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "HandoffSettings":
        env = os.environ if env is None else env
        return cls(
            lease_ttl_s=float(env.get("PERSONA_CALL_LEASE_TTL_S") or cls.lease_ttl_s),
            heartbeat_s=float(env.get("PERSONA_CALL_HEARTBEAT_S") or cls.heartbeat_s),
            grace_s=float(env.get("PERSONA_CALL_GRACE_S") or cls.grace_s),
        )


class CallControl:
    def __init__(self, service: Any, settings: Optional[HandoffSettings] = None, *,
                 sleep: Sleep = asyncio.sleep):
        self.service = service
        self.settings = settings or HandoffSettings()
        self._sleep = sleep
        self._pipelines: dict[str, Hangup] = {}         # call_id -> in-process pipeline hangup
        self._grace: dict[str, asyncio.Task] = {}       # call_id -> grace timer
        self.ended: dict[str, str] = {}                 # call_id -> reason (observability/tests)

    # --- lease ---------------------------------------------------------------------

    async def start(self, session_id: str, *, take_over: bool = False):
        """Acquire the call lease. Raises LeaseHeldError (API: 409 call_in_progress) if a
        live call holds it, unless `take_over` (explicit user confirm)."""
        lease = await asyncio.to_thread(self.service.acquire_call, session_id,
                                        ttl_s=self.settings.lease_ttl_s, take_over=take_over)
        if lease.replaced:
            self._cancel_grace(lease.replaced)
            self.ended[lease.replaced] = "taken_over"
            hangup = self._pipelines.pop(lease.replaced, None)
            if hangup is not None:
                await _quietly(hangup("taken_over"))
        return lease

    async def heartbeat(self, session_id: str, call_id: str) -> bool:
        """Renew the lease; False means another call owns the session now."""
        lease = await asyncio.to_thread(self.service.heartbeat_call, session_id, call_id,
                                        ttl_s=self.settings.lease_ttl_s)
        return lease is not None

    # --- pipeline wiring -------------------------------------------------------------

    def attach(self, call_id: str, hangup: Hangup) -> None:
        """A running pipeline for `call_id` (so take-over/end can stop it in-process)."""
        self._pipelines[call_id] = hangup

    def detach(self, call_id: str) -> None:
        self._pipelines.pop(call_id, None)

    def hooks(self, session_id: str, call_id: str) -> tuple[Callable[[str], Awaitable[None]],
                                                             Callable[[], Awaitable[bool]]]:
        """(`on_ended`, `heartbeat`) for `CallSession`."""

        async def on_ended(reason: str) -> None:
            self.detach(call_id)  # before anything that could hang this pipeline up again
            await self.disconnected(session_id, call_id, reason)

        async def heartbeat() -> bool:
            return await self.heartbeat(session_id, call_id)

        return on_ended, heartbeat

    # --- drop / reconnect / end ----------------------------------------------------

    async def disconnected(self, session_id: str, call_id: str, reason: str) -> None:
        """The pipeline for `call_id` is gone. A media drop opens the grace window;
        anything else (hangup, goodbye, silence park, max duration) ends the call."""
        if reason in GRACE_REASONS:
            await self.dropped(session_id, call_id)
        else:
            await self.end(session_id, call_id, reason)

    async def dropped(self, session_id: str, call_id: str) -> bool:
        grace = self.settings.grace_s
        ok = await asyncio.to_thread(self.service.call_dropped, session_id, call_id, grace_s=grace)
        if not ok:
            return False  # already ended / taken over
        self._cancel_grace(call_id)

        async def expire() -> None:
            await self._sleep(grace)
            await self.end(session_id, call_id, "network_timeout")

        self._grace[call_id] = asyncio.ensure_future(expire())
        return True

    def in_grace(self, call_id: str) -> bool:
        return call_id in self._grace

    async def reconnect(self, session_id: str, call_id: str):
        """Resume `call_id` inside its grace window (same call, same lease). None once the
        window has closed or the call was ended/taken over: start a new call instead."""
        lease = await asyncio.to_thread(self.service.call_reconnected, session_id, call_id,
                                        ttl_s=self.settings.lease_ttl_s)
        if lease is not None:
            self._cancel_grace(call_id)
        return lease

    async def end(self, session_id: str, call_id: str, reason: str):
        """End outside grace: release + resume in chat (idempotent). Returns (released,
        chat-resume TurnOutcome or None); only the first end of the holder releases."""
        self._cancel_grace(call_id)
        released, outcome = await asyncio.to_thread(self.service.end_call, session_id, call_id, reason=reason)
        if released:
            self.ended.setdefault(call_id, reason)
        hangup = self._pipelines.pop(call_id, None)
        if hangup is not None:
            await _quietly(hangup(reason))
        return released, outcome

    async def close(self) -> None:
        for call_id in list(self._grace):
            self._cancel_grace(call_id)

    def _cancel_grace(self, call_id: str) -> None:
        t = self._grace.pop(call_id, None)
        if t is not None and t is not asyncio.current_task() and not t.done():
            t.cancel()


async def _quietly(aw: Awaitable[Any]) -> None:
    try:
        await aw
    except Exception as e:  # noqa: BLE001 - stopping a pipeline must not fail the caller
        logger.warning(f"call hangup failed: {e}")
