"""Host call pipelines inside the agent API process (VOICE-005).

`POST /v1/sessions/{id}/call {sdp, type}` acquires the lease (`handoff.CallControl`), then
`CallHost.answer(...)` builds one SmallWebRTC peer (server leg on the same ICE list the
browser gets, TURN included, ADR 0001), starts a `CallSession` over the shared brain
(`flows.ServiceBrain`: same SessionService, same row as text turns), attaches it to
`CallControl` (heartbeat / grace / hangup-resume) and returns the SDP answer.

Invariants:
- At most one live pipeline per call_id here, and the lease is the per-session gate:
  take-over hangs up the replaced call inside `CallControl.start` before we build the new
  one; a resume (`reconnect`) first retires any pipeline still running for that call_id
  without ending the call.
- Teardown is idempotent (`CallSession.teardown`); `close()` (app lifespan shutdown) hangs
  up every live call and waits for their tasks, so no peer connection or task leaks.

Browser offers are non-trickle (ICE gathered before the POST, like the spike and the
Pipecat client), and every dial or reconnect uses a fresh RTCPeerConnection, so no
PATCH/candidate route and no pc_id renegotiation are needed.

Patterns adapted from Penciled voice-agent `bot.py` (Darran's IP): per-call task
registry, idempotent teardown, shutdown drain.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from .config import VoiceConfig

IceProvider = Callable[[], Awaitable[list[dict]]]
ConnectionFactory = Callable[[list[dict]], Any]
SessionFactory = Callable[..., Any]

SHUTDOWN_REASON = "server_shutdown"
RESUMED_ELSEWHERE = "resumed"


class CallHost:
    def __init__(self, calls: Any, cfg: VoiceConfig, *, service: Any = None, spec: Any = None,
                 ice_provider: Optional[IceProvider] = None,
                 connection_factory: Optional[ConnectionFactory] = None,
                 session_factory: Optional[SessionFactory] = None,
                 drain_s: float = 5.0):
        self.calls, self.cfg = calls, cfg
        self.service = service if service is not None else calls.service
        self.spec = spec if spec is not None else getattr(self.service, "spec", None)
        self._ice = ice_provider or _default_ice
        self._connection = connection_factory or _default_connection
        self._session = session_factory or _default_session
        self._drain_s = drain_s
        self._live: dict[str, Any] = {}              # call_id -> CallSession
        self._tasks: dict[str, asyncio.Task] = {}    # call_id -> session.run()
        self._closing = False

    @property
    def enabled(self) -> bool:
        return self.cfg.can_answer

    def live_calls(self) -> list[str]:
        return [cid for cid, t in self._tasks.items() if not t.done()]

    async def answer(self, session_id: str, call_id: str, sdp: str, type_: str, *,
                     reconnect: bool = False) -> dict:
        """SDP answer for the browser's offer; the pipeline runs in the background."""
        if self._closing:
            raise RuntimeError("call host is shutting down")
        t0 = time.perf_counter()
        await self._retire(call_id)
        connection = self._connection(await self._ice())
        t_ice = time.perf_counter()
        try:
            await connection.initialize(sdp=sdp, type=type_)
            answer = connection.get_answer()
            if not answer or not answer.get("sdp"):
                raise RuntimeError("SmallWebRTC produced no SDP answer")
        except BaseException:
            await _quietly(connection.disconnect())
            raise
        session = self._session(connection, self.cfg, call_id=call_id, session_id=session_id,
                                service=self.service, spec=self.spec, reconnect=reconnect)
        session.attach(self.calls, session_id)
        self._live[call_id] = session
        task = asyncio.ensure_future(self._run(call_id, session))
        self._tasks[call_id] = task
        t_end = time.perf_counter()
        # LAT-001: where the POST /call answer time goes (ICE lookup, aiortc offer/answer + gather).
        logger.info(f"call_answer_timing call={call_id} ice_ms={(t_ice - t0) * 1000:.0f} "
                    f"sdp_ms={(t_end - t_ice) * 1000:.0f} total_ms={(t_end - t0) * 1000:.0f}")
        return {"sdp": answer["sdp"], "type": answer.get("type", "answer"), "pc_id": answer.get("pc_id")}

    async def _run(self, call_id: str, session: Any) -> None:
        try:
            await session.run()
        except asyncio.CancelledError:
            await _quietly(session.hangup(SHUTDOWN_REASON))
            raise
        except Exception as e:  # noqa: BLE001 - one broken call must not take the process down
            logger.exception(f"call {call_id} pipeline failed: {e}")
            await _quietly(session.hangup("pipeline_error"))
        finally:
            if self._live.get(call_id) is session:
                self._live.pop(call_id, None)
                self._tasks.pop(call_id, None)

    async def _retire(self, call_id: str) -> None:
        """A resume for `call_id` while its old pipeline still runs (the server hadn't
        noticed the drop yet): stop the old one without ending the call."""
        old = self._live.pop(call_id, None)
        task = self._tasks.pop(call_id, None)
        if old is None:
            return
        self.calls.detach(call_id)
        old.release_hooks()
        await _quietly(old.hangup(RESUMED_ELSEWHERE))
        if task is not None:
            await _wait(task, self._drain_s)

    async def close(self) -> None:
        """Process shutdown: hang up every live call (the lease is released and the chat
        gets its resume message), then wait for the pipelines to finish."""
        self._closing = True
        live = list(self._live.values())
        await asyncio.gather(*(_quietly(s.hangup(SHUTDOWN_REASON)) for s in live))
        tasks = [t for t in self._tasks.values() if not t.done()]
        if tasks:
            await _wait(asyncio.gather(*tasks, return_exceptions=True), self._drain_s)
        self._live.clear()
        self._tasks.clear()


async def _default_ice() -> list[dict]:
    from .ice import resolve_ice_servers

    return await resolve_ice_servers()


def _default_connection(ice_servers: list[dict]):
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

    from .ice import limit_gather_timeout, server_ice_servers, to_aiortc

    limit_gather_timeout()   # ICE-002: relay-first server leg, shorter gather cap
    return SmallWebRTCConnection(ice_servers=to_aiortc(server_ice_servers(ice_servers)))


def _default_session(connection, cfg: VoiceConfig, *, call_id: str, session_id: str, service: Any,
                     spec: Any, reconnect: bool):
    from .flows import ServiceBrain
    from .session import CallSession

    return CallSession(connection, cfg, call_id=call_id, spec=spec,
                       brain=ServiceBrain(service, session_id), reconnect=reconnect)


async def _wait(aw, timeout: float) -> None:
    try:
        await asyncio.wait_for(asyncio.shield(aw) if isinstance(aw, asyncio.Future) else aw, timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if isinstance(aw, asyncio.Future) and not aw.done():
            aw.cancel()
    except Exception:  # noqa: BLE001
        pass


async def _quietly(aw) -> None:
    try:
        await aw
    except Exception as e:  # noqa: BLE001 - teardown must not fail the caller
        logger.warning(f"call teardown step failed: {e}")
