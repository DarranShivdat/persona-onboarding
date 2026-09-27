"""Thin in-house tracing interface. The core imports ONLY this module.

Backends:
  - NoopTracer   (default)
  - JsonlTracer  (local file; used by harness tiers so they run offline)
  - LangfuseTracer (agent/obs/langfuse_adapter.py; the only file allowed to
    `import langfuse`). Selected with PERSONA_TRACING=langfuse.

Keep this surface small: traces per session turn, spans for pipeline steps,
generations for LLM calls, scores for evals.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Protocol


class Tracer(Protocol):
    def start_trace(self, *, session_id: str, channel: str, name: str, metadata: dict | None = None) -> str: ...
    def span(self, trace_id: str, *, name: str, input: Any = None, output: Any = None, metadata: dict | None = None) -> None: ...
    def generation(self, trace_id: str, *, name: str, model: str, input: Any, output: Any,
                   usage: dict | None = None, latency_ms: float | None = None, metadata: dict | None = None) -> None: ...
    def score(self, trace_id: str, *, name: str, value: float, comment: str | None = None) -> None: ...
    def flush(self) -> None: ...


class NoopTracer:
    def start_trace(self, **_: Any) -> str:
        return uuid.uuid4().hex

    def span(self, *_: Any, **__: Any) -> None: ...
    def generation(self, *_: Any, **__: Any) -> None: ...
    def score(self, *_: Any, **__: Any) -> None: ...
    def flush(self) -> None: ...


class JsonlTracer:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, kind: str, **fields: Any) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "kind": kind, **fields}, default=str) + "\n")

    def start_trace(self, *, session_id: str, channel: str, name: str, metadata: dict | None = None) -> str:
        tid = uuid.uuid4().hex
        self._write("trace", trace_id=tid, session_id=session_id, channel=channel, name=name, metadata=metadata or {})
        return tid

    def span(self, trace_id: str, **fields: Any) -> None:
        self._write("span", trace_id=trace_id, **fields)

    def generation(self, trace_id: str, **fields: Any) -> None:
        self._write("generation", trace_id=trace_id, **fields)

    def score(self, trace_id: str, **fields: Any) -> None:
        self._write("score", trace_id=trace_id, **fields)

    def flush(self) -> None: ...


_tracer: Optional[Tracer] = None


def tracing_mode() -> str:
    return os.environ.get("PERSONA_TRACING", "noop").lower()


def get_tracer() -> Tracer:
    """Resolve the backend from PERSONA_TRACING = noop | jsonl | langfuse."""
    global _tracer
    if _tracer is not None:
        return _tracer
    mode = tracing_mode()
    if mode == "jsonl":
        _tracer = JsonlTracer(os.environ.get("PERSONA_TRACE_FILE", ".persona-qa/traces.jsonl"))
    elif mode == "langfuse":
        try:
            from .langfuse_adapter import LangfuseTracer  # optional dependency, imported lazily
            _tracer = LangfuseTracer()
        except Exception as e:  # noqa: BLE001 - missing keys/SDK must not take turns down
            logging.getLogger(__name__).warning("PERSONA_TRACING=langfuse unavailable (%s); using noop", e)
            _tracer = NoopTracer()
    else:
        _tracer = NoopTracer()
    return _tracer


def reset_tracer() -> None:
    """Forget the resolved backend (tests; env changes)."""
    global _tracer
    _tracer = None
