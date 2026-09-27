"""Langfuse backend for agent.obs.Tracer (implementation: packet OBS-001).

The ONLY module allowed to import `langfuse`. Reads LANGFUSE_PUBLIC_KEY,
LANGFUSE_SECRET_KEY, LANGFUSE_HOST (Cloud or self-hosted) from the environment.
Voice pipeline note: Pipecat emits OpenTelemetry spans; Langfuse ingests OTLP,
so OBS-001 may route Pipecat's OTel exporter to Langfuse instead of wrapping
every frame processor by hand.
"""
from __future__ import annotations


class LangfuseTracer:  # pragma: no cover - scaffold
    def __init__(self) -> None:
        raise NotImplementedError("OBS-001: implement Langfuse adapter behind agent.obs.Tracer")
