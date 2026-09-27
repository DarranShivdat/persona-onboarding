"""One browser call: SmallWebRTC in -> Deepgram STT -> user aggregator (Silero VAD +
Smart Turn) -> Claude (or stub turn) -> Cartesia TTS ⇄ failover Deepgram TTS ->
SmallWebRTC out -> assistant aggregator (ARCHITECTURE §7).

Barge-in: Pipecat interruptions (default user-turn start strategies broadcast an
interruption; the assistant aggregator truncates the interrupted bot text). Browser
WebRTC has AEC, so no PSTN echo mute.

Teardown is idempotent and fires on client disconnect, hangup, max duration, pipeline
end, or goodbye-after-playout — whichever comes first; `on_ended(reason)` (the lease
release hook) runs exactly once. Patterns adapted from Penciled voice-agent `bot.py`
(Darran's IP): per-call teardown, graceful end after playout.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from loguru import logger
from pipecat.frames.frames import EndFrame, Frame, InputAudioRawFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from .config import SAMPLE_RATE, VoiceConfig
from .lifecycle import CallTeardown, Step, end_after_playout
from .playout import PlayoutObserver
from .services import build_llm, build_stt, build_tts

OnEnded = Callable[[str], Awaitable[None]]


def tracing_kwargs(cfg: VoiceConfig, *, call_id: str, session_id: Optional[str] = None) -> dict:
    """PipelineWorker OTel kwargs. Off unless PERSONA_TRACING=langfuse AND the OTLP exporter
    could be installed (agent/obs/langfuse_adapter.py); otherwise the call runs untraced."""
    if not cfg.otel_langfuse:
        return {}
    from ..obs.langfuse_adapter import configure_pipecat_otel  # lazy: no OTel imports by default

    if not configure_pipecat_otel():
        return {}
    attrs = {"persona.channel": "voice", "persona.call_id": call_id}
    if session_id:
        attrs["langfuse.session.id"] = session_id
    return {"enable_tracing": True, "conversation_id": call_id, "additional_span_attributes": attrs}

GREETING = "Hi, it's Persona! Thanks for calling. What should I call you?"
GOODBYE_MAX_DURATION = "We're just about out of time on this call. Everything so far is saved, so you can pick up in the chat. Bye for now!"
GOODBYE_STT_DOWN = "Sorry, I'm having trouble hearing you. Everything so far is saved, so let's keep going in the chat. Bye for now!"


@dataclass
class CallStats:
    call_id: str
    pc_id: str = ""
    started: float = field(default_factory=time.time)
    in_frames: int = 0
    connected: bool = False
    tts_active: Optional[str] = None
    ended_reason: Optional[str] = None
    local_candidates: list[str] = field(default_factory=list)


def _candidate_types(connection) -> list[str]:
    answer = connection.get_answer() or {}
    return sorted({line.split(" typ ", 1)[1].split()[0] for line in answer.get("sdp", "").splitlines()
                   if line.startswith("a=candidate:") and " typ " in line})


def build_user_params(cfg: VoiceConfig):
    """Silero VAD + Smart Turn v3 stop strategy (best-effort: VAD-only if the turn
    model can't load, e.g. a slim CI image)."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    # Penciled-tuned VAD (confidence 0.7, start 0.2s, stop 0.4s); keep in sync with warmup.py.
    vad = SileroVADAnalyzer(params=VADParams(confidence=0.7, start_secs=0.2, stop_secs=0.4))
    try:
        from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
        from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy

        strategies = UserTurnStrategies(stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Smart Turn unavailable ({e}); using default VAD stop strategy")
        strategies = UserTurnStrategies()
    return LLMUserAggregatorParams(vad_analyzer=vad, user_turn_strategies=strategies)


class CallSession:
    def __init__(self, connection, cfg: VoiceConfig, *, call_id: str,
                 on_ended: Optional[OnEnded] = None, heartbeat: Optional[Step] = None,
                 heartbeat_s: float = 30.0, greeting: str = GREETING):
        self.connection, self.cfg = connection, cfg
        self.stats = CallStats(call_id=call_id, pc_id=getattr(connection, "pc_id", "") or "")
        self._on_ended, self._heartbeat, self._heartbeat_s = on_ended, heartbeat, heartbeat_s
        self._greeting = greeting
        self._worker = None
        self._playout = None
        self._bg: list[asyncio.Task] = []
        self._ending = False
        self._pending_reason: Optional[str] = None
        self.teardown = CallTeardown()

    # --- control -----------------------------------------------------------------

    async def hangup(self, reason: str = "server_hangup") -> None:
        await self.teardown(reason)

    async def say_goodbye(self, text: str, reason: str) -> None:
        """Speak `text`, then end once it has played out (never cut the goodbye)."""
        if self._ending or self.teardown.started or self._worker is None:
            return
        self._ending = True
        await self._worker.queue_frame(TTSSpeakFrame(text))

        async def finish() -> None:
            ended = await end_after_playout(self._playout, queue_end=lambda: self._worker.queue_frame(EndFrame()),
                                            torn_down=lambda: self.teardown.started)
            if ended:
                await asyncio.sleep(3.0)  # EndFrame drains the pipeline; cancel if it stalls
                await self.teardown(reason)

        self._pending_reason = reason
        self._spawn(finish())

    def _spawn(self, coro) -> None:
        self._bg.append(asyncio.ensure_future(coro))

    # --- pipeline ------------------------------------------------------------------

    def _build(self):
        transport = SmallWebRTCTransport(
            webrtc_connection=self.connection,
            params=TransportParams(audio_in_enabled=True, audio_out_enabled=True,
                                   audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=SAMPLE_RATE),
        )

        async def on_tts_switched(service) -> None:
            self.stats.tts_active = service.name

        stt = build_stt(self.cfg)
        llm = build_llm(self.cfg)
        tts = build_tts(self.cfg, on_switched=on_tts_switched)
        self.stats.tts_active = getattr(getattr(tts, "strategy", None), "active_service", tts).name
        aggregators = LLMContextAggregatorPair(LLMContext(), user_params=build_user_params(self.cfg))
        self._playout = PlayoutObserver()
        stages = [transport.input(), _AudioInCounter(self.stats), stt, aggregators.user(), llm, tts,
                  transport.output(), aggregators.assistant()]
        pipeline = Pipeline([p for p in stages if p is not None])
        self._worker = PipelineWorker(
            pipeline,
            params=PipelineParams(audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=SAMPLE_RATE,
                                  enable_metrics=True),
            observers=[self._playout],
            idle_timeout_secs=None,  # silence floors are spoken per node (VOICE-002); max duration caps calls
            **tracing_kwargs(self.cfg, call_id=self.stats.call_id),
        )

        @transport.event_handler("on_client_connected")
        async def _connected(_t, _c):  # noqa: ANN001
            self.stats.connected = True
            if self._greeting:
                await self._worker.queue_frame(TTSSpeakFrame(self._greeting))

        @transport.event_handler("on_client_disconnected")
        async def _disconnected(_t, _c):  # noqa: ANN001
            await self.teardown("client_disconnected")

        if stt is not None and hasattr(stt, "add_event_handler"):
            async def _stt_usable(_svc, usable: bool) -> None:
                # Deepgram already reconnected with backoff and gave up: say so, then end politely.
                if not usable:
                    logger.warning("STT unusable after reconnect attempts; ending call politely")
                    await self.say_goodbye(GOODBYE_STT_DOWN, "stt_unavailable")
            try:
                stt.add_event_handler("on_usable_changed", _stt_usable)
            except Exception:  # noqa: BLE001 - older Pipecat without the event
                pass
        return transport

    async def run(self) -> None:
        self.stats.local_candidates = _candidate_types(self.connection)
        transport = self._build()
        worker = self._worker
        self.teardown.add("cancel_background", self._cancel_background)
        self.teardown.add("worker_cancel", lambda: worker.cancel())
        self.teardown.add("transport_cleanup", transport.cleanup)
        self.teardown.add("connection_disconnect", self.connection.disconnect)
        self.teardown.add("on_ended", self._ended)

        self._spawn(self._max_duration())
        if self._heartbeat:
            self._spawn(self._heartbeat_loop())
        try:
            runner = WorkerRunner(handle_sigint=False)
            await runner.add_workers(worker)
            await runner.run()
        finally:
            await self.teardown(self._pending_reason or "pipeline_ended")

    async def _ended(self) -> None:
        self.stats.ended_reason = self.teardown.reason
        if self._on_ended:
            await self._on_ended(self.teardown.reason or "unknown")

    async def _cancel_background(self) -> None:
        me = asyncio.current_task()
        for t in self._bg:
            if t is not me and not t.done():
                t.cancel()

    async def _max_duration(self) -> None:
        await asyncio.sleep(self.cfg.max_call_secs)
        await self.say_goodbye(GOODBYE_MAX_DURATION, "max_duration")
        await asyncio.sleep(30)
        await self.teardown("max_duration")

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_s)
            try:
                await self._heartbeat()
            except Exception as e:  # noqa: BLE001 - a missed renewal must not drop the call
                logger.warning(f"call heartbeat failed: {e}")


class _AudioInCounter(FrameProcessor):
    """Counts caller audio frames (health/proof signal); passes everything through."""

    def __init__(self, stats: CallStats):
        super().__init__()
        self._stats = stats

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self._stats.in_frames += 1
        await self.push_frame(frame, direction)
