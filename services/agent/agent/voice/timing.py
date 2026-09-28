"""Per-turn voice latency spans, logged as one line per turn (LAT-001).

The budget is "user stops -> first bot audio" (< 1.5 s). Spans, all in ms:

    stt_final        user stopped (VAD) -> final transcript (may be negative: Deepgram
                     can finalize before the VAD stop fires)
    turn_wait        user stopped -> LLM #1 starts (Smart Turn / aggregation wait)
    llm_tool         LLM #1 start -> `record_slots` handler entered (extraction)
    handler          brain turn inside the handler (load -> apply -> commit)
    db               the handler's share spent in the store, with `db_calls` pooled round trips
    llm_speech_ttfb  handler done -> first LLM text (only when the LLM phrases the line;
                     `-` when the brain's line is spoken directly)
    tts_ttfb         line ready (handler done / first LLM text) -> first bot audio
    total            user stopped -> first bot audio

`TurnTimer` is pure (explicit timestamps, testable offline); `TimingObserver` feeds it
from pipeline frames and `VoiceFlow` reports the handler span.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from loguru import logger

SPANS = ("stt_final", "turn_wait", "llm_tool", "handler", "db", "llm_speech_ttfb", "tts_ttfb", "total")


@dataclass
class _Turn:
    user_stopped: Optional[float] = None
    stt_final: Optional[float] = None
    llm_start: Optional[float] = None
    tool_call: Optional[float] = None
    handler_done: Optional[float] = None
    handler_ms: Optional[float] = None
    db_ms: Optional[float] = None
    db_calls: Optional[int] = None
    direct: bool = False
    llm_text: Optional[float] = None
    node: Optional[str] = None


def _ms(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or b is None else (b - a) * 1000


class TurnTimer:
    """Collects one turn's marks (seconds, monotonic) and emits one dict per turn when the
    bot's first audio starts. `emit(record)` defaults to one loguru line."""

    def __init__(self, emit: Optional[Callable[[dict], None]] = None,
                 clock: Callable[[], float] = time.perf_counter):
        self.clock = clock
        self._emit = emit or (lambda r: logger.info(format_line(r)))
        self._t = _Turn()
        self.records: list[dict] = []

    def _now(self, at: Optional[float]) -> float:
        return self.clock() if at is None else at

    def user_stopped(self, at: Optional[float] = None) -> None:
        self._t = _Turn(user_stopped=self._now(at))   # a new utterance starts a new turn

    def stt_final(self, at: Optional[float] = None) -> None:
        self._t.stt_final = self._now(at)

    def llm_started(self, at: Optional[float] = None) -> None:
        # Only the first LLM run after the caller stopped is extraction (LLM #1); a phrasing
        # run (LLM #2) is measured at its first text instead.
        if self._t.llm_start is None:
            self._t.llm_start = self._now(at)

    def tool_call(self, at: Optional[float] = None) -> None:
        if self._t.tool_call is None:
            self._t.tool_call = self._now(at)

    def handler_done(self, *, handler_ms: float, node: Optional[str], direct: bool,
                     db_ms: Optional[float] = None, db_calls: Optional[int] = None,
                     at: Optional[float] = None) -> None:
        t = self._t
        t.handler_done, t.handler_ms, t.node, t.direct = self._now(at), handler_ms, node, direct
        t.db_ms, t.db_calls = db_ms, db_calls

    def llm_text(self, at: Optional[float] = None) -> None:
        t = self._t
        if t.handler_done is not None and t.llm_text is None and not t.direct:
            t.llm_text = self._now(at)

    def first_audio(self, at: Optional[float] = None) -> Optional[dict]:
        """Bot audio started: close the turn (once) if a caller turn is open."""
        t = self._t
        if t.user_stopped is None:
            return None   # opening line / nudge / typed ack: not a caller turn
        at = self._now(at)
        line_ready = t.llm_text if t.llm_text is not None else t.handler_done
        rec = {
            "node": t.node,
            "direct": t.direct,
            "stt_final": _ms(t.user_stopped, t.stt_final),
            "turn_wait": _ms(t.user_stopped, t.llm_start),
            "llm_tool": _ms(t.llm_start, t.tool_call),
            "handler": t.handler_ms,
            "db": t.db_ms,
            "db_calls": t.db_calls,
            "llm_speech_ttfb": None if t.direct else _ms(t.handler_done, t.llm_text),
            "tts_ttfb": _ms(line_ready, at),
            "total": _ms(t.user_stopped, at),
        }
        self._t = _Turn()
        self.records.append(rec)
        self._emit(rec)
        return rec


def format_line(rec: dict) -> str:
    def f(v) -> str:
        if v is None:
            return "-"
        if isinstance(v, bool):
            return "1" if v else "0"
        return f"{v:.0f}" if isinstance(v, float) else str(v)
    parts = [f"node={rec.get('node') or '-'}", f"direct={f(rec.get('direct'))}"]
    parts += [f"{k}_ms={f(rec.get(k))}" for k in SPANS]
    parts.insert(-1, f"db_calls={f(rec.get('db_calls'))}")
    return "voice_turn_timing " + " ".join(parts)


def build_timing_observer(timer: TurnTimer):
    """Pipeline observer feeding `timer` (frames are observed at every hop: dedupe by id)."""
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        TranscriptionFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.observers.base_observer import BaseObserver, FramePushed

    class TimingObserver(BaseObserver):
        def __init__(self) -> None:
            super().__init__()
            self._seen: set[int] = set()

        async def on_push_frame(self, data: FramePushed) -> None:
            f = data.frame
            kinds = (VADUserStoppedSpeakingFrame, TranscriptionFrame, LLMFullResponseStartFrame,
                     LLMTextFrame, BotStartedSpeakingFrame)
            if not isinstance(f, kinds) or f.id in self._seen:
                return
            self._seen.add(f.id)
            if len(self._seen) > 4096:
                self._seen.clear()
            if isinstance(f, VADUserStoppedSpeakingFrame):
                timer.user_stopped()
            elif isinstance(f, TranscriptionFrame):
                timer.stt_final()
            elif isinstance(f, LLMFullResponseStartFrame):
                timer.llm_started()
            elif isinstance(f, LLMTextFrame):
                timer.llm_text()
            elif isinstance(f, BotStartedSpeakingFrame):
                timer.first_audio()

    return TimingObserver()
