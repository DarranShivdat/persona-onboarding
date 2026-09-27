"""OBS-001: tracer backends. Offline — the Langfuse client is a recording stub, never the SDK."""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.brain.spec import load_spec
from agent.brain.state import SessionState
from agent.llm import Extractor, Phraser, run_turn
from agent.llm.client import ReplayClient
from agent.llm.testing import text_response, tool_response
from agent.obs import JsonlTracer, NoopTracer, get_tracer, reset_tracer
from agent.obs.langfuse_adapter import (
    LangfuseConfigError,
    LangfuseTracer,
    configure_pipecat_otel,
    otlp_settings,
)
from agent.obs.meta import prompts_hash, turn_metadata

SPEC = load_spec()
AGENT_ROOT = Path(__file__).resolve().parents[1]
KEYS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test"}


class _Obs:
    def __init__(self, rec, kind, kw):
        self.rec, self.kind, self.kw = rec, kind, kw

    def update_trace(self, **kw):
        self.rec.calls.append(("update_trace", kw))

    def end(self):
        self.rec.calls.append(("end", {"kind": self.kind, "name": self.kw.get("name")}))


class StubLangfuse:
    """Records the Langfuse v3 client surface the adapter uses."""

    def __init__(self, fail=False):
        self.calls, self.fail, self._n = [], fail, 0

    def _call(self, name, kw):
        if self.fail:
            raise RuntimeError("langfuse down")
        self.calls.append((name, kw))

    def create_trace_id(self):
        self._call("create_trace_id", {})
        self._n += 1
        return f"{self._n:032x}"

    def start_span(self, **kw):
        self._call("start_span", kw)
        return _Obs(self, "span", kw)

    def start_generation(self, **kw):
        self._call("start_generation", kw)
        return _Obs(self, "generation", kw)

    def create_score(self, **kw):
        self._call("create_score", kw)

    def flush(self):
        self._call("flush", {})


@pytest.fixture(autouse=True)
def _fresh_tracer(monkeypatch):
    reset_tracer()
    yield
    reset_tracer()


# --- backend selection -------------------------------------------------------------------


def test_default_is_noop(monkeypatch):
    monkeypatch.delenv("PERSONA_TRACING", raising=False)
    assert isinstance(get_tracer(), NoopTracer)
    assert get_tracer() is get_tracer()


def test_jsonl_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONA_TRACING", "jsonl")
    monkeypatch.setenv("PERSONA_TRACE_FILE", str(tmp_path / "t.jsonl"))
    tr = get_tracer()
    assert isinstance(tr, JsonlTracer)
    tid = tr.start_trace(session_id="s", channel="text", name="turn", metadata={"node": "agent_name"})
    tr.span(tid, name="brain.apply", output={"node": "user_name"})
    tr.generation(tid, name="extract", model="m", input="x", output={}, usage={"input_tokens": 3})
    tr.score(tid, name="on_track", value=1.0)
    rows = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["trace", "span", "generation", "score"]
    assert rows[0]["session_id"] == "s" and rows[0]["metadata"] == {"node": "agent_name"}
    assert all(r["trace_id"] == tid for r in rows)


def test_langfuse_mode_without_keys_falls_back_to_noop(monkeypatch):
    monkeypatch.setenv("PERSONA_TRACING", "langfuse")
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    assert isinstance(get_tracer(), NoopTracer)


def test_langfuse_mode_without_sdk_falls_back_to_noop(monkeypatch):
    monkeypatch.setenv("PERSONA_TRACING", "langfuse")
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setitem(sys.modules, "langfuse", None)  # import fails even if installed
    assert isinstance(get_tracer(), NoopTracer)


def test_langfuse_mode_constructs_client_from_env_with_stub(monkeypatch):
    seen = {}

    class FakeLangfuse(StubLangfuse):
        def __init__(self, **kw):
            super().__init__()
            seen.update(kw)

    fake_mod = type(sys)("langfuse")
    fake_mod.Langfuse = FakeLangfuse
    monkeypatch.setitem(sys.modules, "langfuse", fake_mod)
    monkeypatch.setenv("PERSONA_TRACING", "langfuse")
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    tr = get_tracer()
    assert isinstance(tr, LangfuseTracer) and isinstance(tr.client, FakeLangfuse)
    assert seen == {"public_key": "pk-lf-test", "secret_key": "sk-lf-test", "host": "https://cloud.langfuse.com"}


def test_noop_default_imports_no_langfuse():
    """Process start on the default path (API app + turn helpers) never imports langfuse/OTel."""
    code = (
        "import sys, importlib\n"
        "import agent.obs, agent.llm, agent.llm.turn, agent.api.service\n"
        "try:\n    importlib.import_module('agent.api.app')\nexcept ImportError:\n    pass\n"
        "from agent.obs import get_tracer; get_tracer()\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('langfuse', 'opentelemetry')"
        " or m == 'agent.obs.langfuse_adapter']\n"
        "print(','.join(bad))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PERSONA_TRACING" and not k.startswith("LANGFUSE_")}
    env["PERSONA_TRACING"] = "noop"
    out = subprocess.run([sys.executable, "-c", code], cwd=AGENT_ROOT, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""


# --- LangfuseTracer -----------------------------------------------------------------------


def test_langfuse_tracer_maps_protocol_to_client():
    lf = StubLangfuse()
    tr = LangfuseTracer(client=lf)
    tid = tr.start_trace(session_id="sess-1", channel="voice", name="turn", metadata={"node": "need"})
    tr.span(tid, name="brain.apply", input={"node": "need"}, output={"node": "gmail"}, metadata={"e": 1})
    tr.generation(tid, name="extract", model="claude-haiku-4-5", input="hi", output={"x": 1},
                  usage={"input_tokens": 10, "output_tokens": 2, "note": "x"}, latency_ms=12.5)
    tr.score(tid, name="on_track", value=0.5, comment="ok")
    tr.flush()

    names = [c[0] for c in lf.calls]
    assert names == ["create_trace_id", "start_span", "update_trace", "end", "start_span", "end",
                     "start_generation", "end", "create_score", "flush"]
    root, upd = lf.calls[1][1], lf.calls[2][1]
    assert root["trace_context"] == {"trace_id": tid}
    assert upd["session_id"] == "sess-1" and upd["tags"] == ["channel:voice"]
    assert upd["metadata"] == {"channel": "voice", "node": "need"}
    span = lf.calls[4][1]
    assert span["trace_context"] == {"trace_id": tid} and span["output"] == {"node": "gmail"}
    gen = lf.calls[6][1]
    assert gen["model"] == "claude-haiku-4-5" and gen["usage_details"] == {"input_tokens": 10, "output_tokens": 2}
    assert gen["metadata"]["latency_ms"] == 12.5
    assert lf.calls[8][1] == {"name": "on_track", "value": 0.5, "trace_id": tid, "comment": "ok"}


def test_langfuse_tracer_never_raises():
    tr = LangfuseTracer(client=StubLangfuse(fail=True))
    tid = tr.start_trace(session_id="s", channel="text", name="turn")
    assert isinstance(tid, str) and len(tid) == 32
    tr.span(tid, name="x")
    tr.generation(tid, name="g", model="m", input=None, output=None)
    tr.score(tid, name="s", value=1.0)
    tr.flush()


def test_langfuse_tracer_requires_keys_when_building_real_client():
    with pytest.raises(LangfuseConfigError):
        LangfuseTracer(env={})


def test_run_turn_trace_carries_turn_metadata():
    lf = StubLangfuse()
    tr = LangfuseTracer(client=lf)
    st = SessionState(session_id="sess-9", node="agent_name")
    ex = Extractor(ReplayClient([tool_response(SPEC, {"agent_name": "Nova"})]), SPEC, model="claude-haiku-4-5", tracer=tr)
    ph = Phraser(ReplayClient([text_response("Nova it is! Want to hop on a quick call?")]), SPEC,
                 model="claude-haiku-4-5", tracer=tr)
    out = run_turn(SPEC, st, channel="text", utterance="Nova", extractor=ex, phraser=ph, tracer=tr)
    assert out.result.state.filled("agent_name")
    upd = next(kw for n, kw in lf.calls if n == "update_trace")
    assert upd["session_id"] == "sess-9"
    assert upd["metadata"]["node"] == "agent_name"
    assert upd["metadata"]["flow_version"] == SPEC.raw["version"]
    assert upd["metadata"]["prompt_hash"] == prompts_hash(SPEC)
    gens = [kw["name"] for n, kw in lf.calls if n == "start_generation"]
    assert gens == ["extract", "phrase"]


def test_prompts_hash_matches_harness_run_name(monkeypatch):
    monkeypatch.syspath_prepend(str(AGENT_ROOT.parents[1]))  # repo root: `harness` namespace package
    runner = pytest.importorskip("harness.convo.runner")
    assert runner.prompts_hash(SPEC) == prompts_hash(SPEC)


def test_turn_metadata_shape():
    meta = turn_metadata(SPEC, "need", event=None, version=3)
    assert meta == {"node": "need", "flow_version": SPEC.raw["version"], "prompt_hash": prompts_hash(SPEC), "version": 3}


# --- Pipecat OTel -> Langfuse OTLP ---------------------------------------------------------


def test_otlp_settings_endpoint_and_auth():
    endpoint, headers = otlp_settings({**KEYS, "LANGFUSE_HOST": "https://us.cloud.langfuse.com/"})
    assert endpoint == "https://us.cloud.langfuse.com/api/public/otel/v1/traces"
    assert base64.b64decode(headers["Authorization"].split()[1]).decode() == "pk-lf-test:sk-lf-test"


def test_configure_pipecat_otel_uses_setup_seam():
    seen = {}

    def setup(**kw):
        seen.update(kw)
        return True

    exporter = object()
    assert configure_pipecat_otel(exporter=exporter, setup=setup) is True
    assert seen == {"service_name": "persona-voice", "exporter": exporter}


def test_configure_pipecat_otel_best_effort_without_keys():
    import agent.obs.langfuse_adapter as la

    la._otel_configured = None
    try:
        assert configure_pipecat_otel(env={}) is False
    finally:
        la._otel_configured = None


def test_voice_tracing_kwargs_off_by_default_and_on_with_langfuse(monkeypatch):
    pytest.importorskip("pipecat")
    from agent.voice.config import VoiceConfig
    from agent.voice import session as vs
    import agent.obs.langfuse_adapter as la

    assert VoiceConfig.from_env({}).otel_langfuse is False
    assert vs.tracing_kwargs(VoiceConfig.from_env({}), call_id="c1") == {}
    cfg = VoiceConfig.from_env({"PERSONA_TRACING": "langfuse"})
    assert cfg.otel_langfuse is True
    monkeypatch.setattr(la, "configure_pipecat_otel", lambda: False)
    assert vs.tracing_kwargs(cfg, call_id="c1") == {}
    monkeypatch.setattr(la, "configure_pipecat_otel", lambda: True)
    kw = vs.tracing_kwargs(cfg, call_id="c1", session_id="s1")
    assert kw["enable_tracing"] is True and kw["conversation_id"] == "c1"
    assert kw["additional_span_attributes"]["langfuse.session.id"] == "s1"
