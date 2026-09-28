"""One browser call: SmallWebRTC in -> Deepgram STT -> user aggregator (Silero VAD +
Smart Turn) -> Claude under Pipecat Flows (or stub turn) -> Cartesia TTS ⇄ failover Deepgram TTS ->
SmallWebRTC out -> assistant aggregator (ARCHITECTURE §7).

Barge-in: Pipecat interruptions (default user-turn start strategies broadcast an
interruption; the assistant aggregator truncates the interrupted bot text). Browser
WebRTC has AEC, so no PSTN echo mute.

Teardown is idempotent and fires on client disconnect, hangup, max duration, pipeline
end, or goodbye-after-playout — whichever comes first; `on_ended(reason)` (the lease
release hook) runs exactly once. Patterns adapted from Penciled voice-agent `bot.py`
(Darran's IP): per-call teardown, graceful end after playout.

Hand-off (VOICE-003): `attach(control, session_id)` wires the call to `handoff.CallControl`
— lease heartbeat (a lost lease ends this pipeline: taken over elsewhere), and
`on_ended(reason)` routes a media drop into the reconnect grace window and anything else
to a hangup + chat resume. `reconnect=True` opens with "we got cut off". A per-node
silence floor (`silence.py`) nudges at ≈7s/≈15s, then offers the chat and ends
(`silence_timeout`). Text typed in the chat during the call is acknowledged by voice, and
so are the Gmail card's out-of-band results (OAuth connected / consent failed, VOICE-004).

LLM slot (`VoiceConfig.llm_mode`): `flows` = Claude under a Pipecat Flows `FlowManager`
whose nodes and `record_slots` handler come from `flows.VoiceFlow` over a `BrainPort`
(the shared brain; pass `brain=ServiceBrain(...)` to share the text session's state);
`stub` = `StubTurnLLM`, which never touches state.
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

from ..brain.spec import FlowSpec, load_spec
from ..brain.state import SessionState
from .config import SAMPLE_RATE, VoiceConfig
from .flows import BrainPort, LocalBrain, VoiceFlow
from .lifecycle import CallTeardown, end_after_playout
from .playout import PlayoutObserver
from .services import build_llm, build_stt, build_tts
from .speech_guard import build_speech_guard
from .silence import SilenceFloor, SilenceObserver, SilencePolicy, run_silence_floor, silence_line

OnEnded = Callable[[str], Awaitable[None]]
Heartbeat = Callable[[], Awaitable[Optional[bool]]]


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
                 on_ended: Optional[OnEnded] = None, heartbeat: Optional[Heartbeat] = None,
                 heartbeat_s: float = 30.0, greeting: str = GREETING,
                 spec: Optional[FlowSpec] = None, brain: Optional[BrainPort] = None,
                 reconnect: bool = False, silence: Optional[SilencePolicy] = SilencePolicy()):
        self.connection, self.cfg = connection, cfg
        self._spec, self._brain = spec, brain
        self._reconnect = reconnect
        self.silence = SilenceFloor(silence) if silence else None
        self._unwatch: Optional[Callable[[], None]] = None
        self.flow: Optional[VoiceFlow] = None
        self._flow_manager = None
        self.stats = CallStats(call_id=call_id, pc_id=getattr(connection, "pc_id", "") or "")
        self._on_ended, self._heartbeat, self._heartbeat_s = on_ended, heartbeat, heartbeat_s
        self._on_connected: Optional[Callable[[], Awaitable[None]]] = None
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

    def attach(self, control, session_id: str) -> None:
        """Wire lease heartbeat / grace / hangup-resume to a `handoff.CallControl`."""
        self._on_ended, self._heartbeat = control.hooks(session_id, self.stats.call_id)
        self._heartbeat_s = control.settings.heartbeat_s
        call_id = self.stats.call_id
        self._on_connected = lambda: control.connected(session_id, call_id)
        control.attach(call_id, self.hangup)

    def release_hooks(self) -> None:
        """Stop reporting to the lease (this pipeline is being replaced by a resume of the
        same call): a later teardown must not end or drop the call."""
        self._on_ended = self._heartbeat = self._on_connected = None

    async def speak(self, text: str) -> None:
        if self._worker is not None and not self._ending and not self.teardown.started:
            await self._worker.queue_frame(TTSSpeakFrame(text))

    def current_node(self) -> Optional[str]:
        return self.flow.last.plan.node if self.flow is not None and self.flow.last is not None else None

    async def on_typed(self, plan, source: str = "text", data: Optional[dict] = None) -> None:
        """An out-of-band turn committed during the call: typed chat text (EC-28) or the
        Gmail card's OAuth result (VOICE-004). Say the brain's result."""
        if self.flow is None or self._flow_manager is None or self._ending or self.teardown.started:
            return
        if self.silence:
            self.silence.user_activity()
        node = await (self.flow.typed_turn(plan) if source == "text"
                      else self.flow.typed_turn(plan, source=source, data=data))
        if node is not None and not self.teardown.started:
            await self._flow_manager.set_node_from_config(node)

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
        context = LLMContext()
        aggregators = LLMContextAggregatorPair(context, user_params=build_user_params(self.cfg))
        self._playout = PlayoutObserver()
        # GUARD-001: on a Flows call, every sentence the LLM says passes the output guard.
        speech_guard = (build_speech_guard(lambda: self.flow.speech_context() if self.flow else None)
                        if self.cfg.llm_mode == "flows" else None)
        stages = [transport.input(), _AudioInCounter(self.stats), stt, aggregators.user(), llm, speech_guard,
                  tts, transport.output(), aggregators.assistant()]
        pipeline = Pipeline([p for p in stages if p is not None])
        self._worker = PipelineWorker(
            pipeline,
            params=PipelineParams(audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=SAMPLE_RATE,
                                  enable_metrics=True),
            observers=[self._playout] + ([SilenceObserver(self.silence)] if self.silence else []),
            idle_timeout_secs=None,  # per-node spoken silence floors (VOICE-003) + max duration cap calls
            **tracing_kwargs(self.cfg, call_id=self.stats.call_id),
        )

        if self.cfg.llm_mode == "flows":
            self._build_flow(llm, aggregators, context)

        @transport.event_handler("on_client_connected")
        async def _connected(_t, _c):  # noqa: ANN001
            self.stats.connected = True
            if self._on_connected is not None:
                try:
                    await self._on_connected()
                except Exception as e:  # noqa: BLE001 - a UI push must not drop the call
                    logger.warning(f"call connected push failed: {e}")
            if self._flow_manager is not None:
                # The brain's call_started turn picks the node and the opening line.
                await self._flow_manager.initialize(await self.flow.opening(reconnect=self._reconnect))
            elif self._brain is not None:
                # Stub turn over the shared brain (fake vendors / no Anthropic key): the brain's
                # call_started still moves the session onto voice and picks the opening line
                # (so hangup resumes in chat); the stub LLM never touches state after that.
                self.flow = VoiceFlow(self._spec or load_spec(), self._brain)
                await self.flow.opening(reconnect=self._reconnect)
                await self._worker.queue_frame(TTSSpeakFrame(self.flow.last.line))
            elif self._greeting:
                await self._worker.queue_frame(TTSSpeakFrame(self._greeting))
            if self.silence and not self.silence.armed:
                self.silence.start()
                self._spawn(self._silence_floor())

        @transport.event_handler("on_client_disconnected")
        async def _disconnected(_t, _c):  # noqa: ANN001
            # Start teardown, don't await it: its transport.cleanup step waits for this very
            # handler to finish, so awaiting here deadlocks and on_ended (grace/resume) never runs.
            self._spawn(self.teardown("client_disconnected"))

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

    def _build_flow(self, llm, aggregators, context) -> None:
        from pipecat_flows import FlowManager

        spec = self._spec or load_spec()
        brain = self._brain or LocalBrain(spec, SessionState(session_id=self.stats.call_id))

        async def graduated(line: str) -> None:
            await self.say_goodbye(line, "graduated")

        self.flow = VoiceFlow(spec, brain, context=context, on_graduated=graduated)
        # GUARD-001: any function call with no handler is rejected by the flow (logged, line re-spoken).
        llm.register_function(None, self.flow.handle_unknown_function)
        self._flow_manager = FlowManager(llm=llm, context_aggregator=aggregators, worker=self._worker)
        watch = getattr(brain, "watch_text", None)
        if watch is not None:
            loop = asyncio.get_running_loop()
            # Called from the text turn's thread after commit: hop onto the call's loop.
            self._unwatch = watch(lambda out: loop.call_soon_threadsafe(
                self._spawn, self.on_typed(out.plan, getattr(out, "source", "text"), getattr(out, "data", None))))

    async def run(self) -> None:
        self.stats.local_candidates = _candidate_types(self.connection)
        transport = self._build()
        worker = self._worker
        self.teardown.add("unwatch_text", self._stop_watching)
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

    async def _stop_watching(self) -> None:
        if self.silence:
            self.silence.stop()
        if self._unwatch:
            self._unwatch()
            self._unwatch = None

    async def _silence_floor(self) -> None:
        spec = self._spec or load_spec()
        await run_silence_floor(self.silence, speak=self.speak,
                                park=lambda line: self.say_goodbye(line, "silence_timeout"),
                                line=lambda action: silence_line(spec, self.current_node(), action),
                                sleep=asyncio.sleep)

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
                held = await self._heartbeat()
            except Exception as e:  # noqa: BLE001 - a missed renewal must not drop the call
                logger.warning(f"call heartbeat failed: {e}")
                continue
            if held is False:
                # Another tab/device took the call over (or the lease was released):
                # never two bots on one session.
                logger.info("call lease lost; ending this pipeline")
                await self.teardown("lease_lost")
                return


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
