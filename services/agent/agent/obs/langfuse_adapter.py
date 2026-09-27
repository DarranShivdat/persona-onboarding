"""Langfuse backend for agent.obs.Tracer, plus Pipecat OTel -> Langfuse OTLP routing.

The ONLY module allowed to import `langfuse` (and the OTLP exporter used for the
Pipecat path). Both imports are lazy: importing this module is free, and nothing here
runs unless PERSONA_TRACING=langfuse.

Env (names only; values never live in the repo):
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY   project keys
    LANGFUSE_HOST                              default https://cloud.langfuse.com

Written against the Langfuse Python SDK v3 (OTel-based):
    client.create_trace_id()
    client.start_span(trace_context={"trace_id": ...}, name=..., input=..., output=..., metadata=...)
      -> span.update_trace(name=..., session_id=..., tags=..., metadata=...); span.end()
    client.start_generation(trace_context=..., name=..., model=..., input=..., output=...,
                            usage_details=..., metadata=...) -> gen.end()
    client.create_score(name=..., value=..., trace_id=..., comment=...)
    client.flush()
Tests inject a stub with that surface, so no network and no `langfuse` install needed.

Tracing must never break a turn: every call is guarded and failures are logged once.

Voice: Pipecat (1.4) emits OpenTelemetry spans when `PipelineWorker(enable_tracing=True)`
and a global TracerProvider exists. `configure_pipecat_otel()` installs one whose OTLP/HTTP
exporter points at Langfuse's OTLP endpoint:
    endpoint  {LANGFUSE_HOST}/api/public/otel/v1/traces
    headers   Authorization: Basic base64(LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY)
Needs `opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http` (pulled in by
`langfuse>=3`). If they are missing it returns False and the call runs untraced.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any, Mapping, Optional

log = logging.getLogger(__name__)

DEFAULT_HOST = "https://cloud.langfuse.com"
OTLP_PATH = "/api/public/otel/v1/traces"


class LangfuseConfigError(RuntimeError):
    """Keys missing: get_tracer() falls back to noop rather than failing turns."""


def _settings(env: Optional[Mapping[str, str]] = None) -> tuple[str, str, str]:
    env = os.environ if env is None else env
    pk, sk = env.get("LANGFUSE_PUBLIC_KEY") or "", env.get("LANGFUSE_SECRET_KEY") or ""
    host = (env.get("LANGFUSE_HOST") or DEFAULT_HOST).rstrip("/")
    if not pk or not sk:
        raise LangfuseConfigError("PERSONA_TRACING=langfuse needs LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY")
    return pk, sk, host


class LangfuseTracer:
    """One Langfuse trace per turn; spans/generations/scores attach to it by trace id."""

    def __init__(self, client: Any = None, *, env: Optional[Mapping[str, str]] = None) -> None:
        if client is None:
            pk, sk, host = _settings(env)
            from langfuse import Langfuse  # optional dependency (extra: observability)

            client = Langfuse(public_key=pk, secret_key=sk, host=host)
        self.client = client
        self._warned = False

    def _guard(self, what: str, fn, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - observability never breaks a turn
            if not self._warned:
                log.warning("langfuse %s failed (further errors suppressed): %s", what, e)
                self._warned = True
            return None

    def _ctx(self, trace_id: str) -> dict:
        return {"trace_id": trace_id}

    # --- Tracer protocol -------------------------------------------------------------

    def start_trace(self, *, session_id: str, channel: str, name: str, metadata: dict | None = None) -> str:
        tid = self._guard("create_trace_id", self.client.create_trace_id)
        if not tid:
            import uuid

            tid = uuid.uuid4().hex  # 32 hex chars: a valid W3C/Langfuse trace id

        def _root() -> None:
            meta = {"channel": channel, **(metadata or {})}
            root = self.client.start_span(trace_context=self._ctx(tid), name=name, metadata=meta)
            root.update_trace(name=name, session_id=session_id, tags=[f"channel:{channel}"], metadata=meta)
            root.end()

        self._guard("start_trace", _root)
        return tid

    def span(self, trace_id: str, *, name: str, input: Any = None, output: Any = None,
             metadata: dict | None = None) -> None:
        def _span() -> None:
            self.client.start_span(trace_context=self._ctx(trace_id), name=name, input=input,
                                   output=output, metadata=metadata).end()

        self._guard("span", _span)

    def generation(self, trace_id: str, *, name: str, model: str, input: Any, output: Any,
                   usage: dict | None = None, latency_ms: float | None = None,
                   metadata: dict | None = None) -> None:
        meta = dict(metadata or {})
        if latency_ms is not None:
            meta["latency_ms"] = latency_ms
        usage_details = {k: int(v) for k, v in (usage or {}).items() if isinstance(v, (int, float))}

        def _gen() -> None:
            self.client.start_generation(trace_context=self._ctx(trace_id), name=name, model=model,
                                         input=input, output=output, usage_details=usage_details or None,
                                         metadata=meta).end()

        self._guard("generation", _gen)

    def score(self, trace_id: str, *, name: str, value: float, comment: str | None = None) -> None:
        self._guard("score", self.client.create_score, name=name, value=value, trace_id=trace_id, comment=comment)

    def flush(self) -> None:
        self._guard("flush", self.client.flush)


# --- Pipecat OpenTelemetry -> Langfuse OTLP ---------------------------------------------

def otlp_settings(env: Optional[Mapping[str, str]] = None) -> tuple[str, dict[str, str]]:
    """(endpoint, headers) for Langfuse's OTLP/HTTP trace ingestion. Pure; no imports."""
    pk, sk, host = _settings(env)
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    return host + OTLP_PATH, {"Authorization": f"Basic {auth}"}


_otel_configured: Optional[bool] = None


def configure_pipecat_otel(env: Optional[Mapping[str, str]] = None, *, exporter: Any = None,
                           setup: Any = None) -> bool:
    """Install a global OTel TracerProvider exporting to Langfuse (once per process).

    Best effort: returns False (and the call runs untraced) when keys, OpenTelemetry or
    Pipecat's tracing utilities are unavailable. `exporter`/`setup` are test seams.
    """
    global _otel_configured
    if _otel_configured is not None and exporter is None and setup is None:
        return _otel_configured
    ok = False
    try:
        if exporter is None:
            endpoint, headers = otlp_settings(env)
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
        if setup is None:
            from pipecat.utils.tracing.setup import setup_tracing as setup
        ok = bool(setup(service_name="persona-voice", exporter=exporter))
    except Exception as e:  # noqa: BLE001 - never break voice imports / call setup
        log.warning("Pipecat OTel -> Langfuse not configured: %s", e)
        ok = False
    _otel_configured = ok
    return ok
