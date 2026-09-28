"""LAT-001: voice turn latency — per-turn timing line, direct speech (no phrasing LLM run),
and the store's round-trip budget on the voice turn path.
"""
import asyncio

import pytest

from agent.brain.engine import Turn, apply
from agent.brain.state import SessionState
from agent.voice.timing import TurnTimer, format_line

from test_voice_flows import SPEC, _args, _Ctx


def _run(coro):
    return asyncio.run(coro)


# --- timing line ------------------------------------------------------------------


def test_turn_timer_emits_one_line_per_caller_turn():
    out: list[dict] = []
    t = TurnTimer(emit=out.append)
    t.first_audio(at=0.5)                      # opening line: no caller turn open, nothing logged
    t.user_stopped(at=10.0)
    t.stt_final(at=10.05)
    t.llm_started(at=10.3)
    t.tool_call(at=11.0)
    t.handler_done(handler_ms=120.0, node="need", direct=True, db_ms=110.0, db_calls=2, at=11.12)
    t.llm_text(at=11.2)                        # ignored: the line was spoken directly
    rec = t.first_audio(at=11.35)
    t.first_audio(at=11.4)                     # the same turn closes once
    assert out == [rec] and rec["node"] == "need" and rec["direct"] is True
    assert rec["stt_final"] == pytest.approx(50) and rec["turn_wait"] == pytest.approx(300)
    assert rec["llm_tool"] == pytest.approx(700) and rec["handler"] == 120.0 and rec["db_calls"] == 2
    assert rec["llm_speech_ttfb"] is None and rec["tts_ttfb"] == pytest.approx(230)
    assert rec["total"] == pytest.approx(1350)
    line = format_line(rec)
    assert line.startswith("voice_turn_timing node=need direct=1 ")
    for k in ("stt_final_ms=50", "llm_tool_ms=700", "handler_ms=120", "db_ms=110", "db_calls=2",
              "llm_speech_ttfb_ms=-", "tts_ttfb_ms=230", "total_ms=1350"):
        assert k in line


def test_turn_timer_measures_llm_phrasing_when_not_direct():
    out: list[dict] = []
    t = TurnTimer(emit=out.append)
    t.user_stopped(at=0.0)
    t.llm_started(at=0.2)
    t.tool_call(at=1.0)
    t.llm_started(at=1.1)                      # LLM #2 start does not move the extraction span
    t.handler_done(handler_ms=100.0, node="need", direct=False, at=1.1)
    t.llm_text(at=1.6)
    rec = t.first_audio(at=1.9)
    assert rec["llm_tool"] == pytest.approx(800) and rec["llm_speech_ttfb"] == pytest.approx(500)
    assert rec["tts_ttfb"] == pytest.approx(300) and rec["total"] == pytest.approx(1900)


# --- direct speech ----------------------------------------------------------------


def _flow(**kw):
    pytest.importorskip("pipecat_flows")
    from agent.voice.flows import LocalBrain, VoiceFlow

    st = apply(SPEC, SessionState(session_id="lat"), Turn(channel="text", event="open")).state
    ctx = _Ctx()
    flow = VoiceFlow(SPEC, LocalBrain(SPEC, st), context=ctx, **kw)
    _run(flow.opening())
    return flow, ctx


def test_record_slots_speaks_the_brains_line_directly():
    timer = TurnTimer(emit=lambda r: None)
    flow, ctx = _flow(timer=timer)
    ctx.messages.append({"role": "user", "content": "I'm Sam, S-A-M"})
    result, node = _run(flow.handle_record_slots(_args("I'm Sam, S-A-M")))
    assert result["say"] and node["pre_actions"] == [{"type": "tts_say", "text": result["say"]}]
    assert node["respond_immediately"] is False            # no phrasing LLM run
    assert "Say this line" not in node["task_messages"][0]["content"]
    assert timer._t.handler_ms is not None and timer._t.direct is True


def test_absorbed_turn_stays_quiet_and_llm_mode_still_available():
    flow, ctx = _flow()
    ctx.messages.append({"role": "user", "content": "uh"})
    result, node = _run(flow.handle_record_slots(_args("uh")))
    assert result["absorbed"] and "pre_actions" not in node and node["respond_immediately"] is False

    flow, ctx = _flow(direct_speech=False)
    ctx.messages.append({"role": "user", "content": "I'm Sam, S-A-M"})
    result, node = _run(flow.handle_record_slots(_args("I'm Sam, S-A-M")))
    assert "pre_actions" not in node and node["respond_immediately"] is True
    assert result["say"] in node["task_messages"][0]["content"]


def test_direct_speech_is_on_by_default_and_can_be_turned_off():
    from agent.voice.config import VoiceConfig

    assert VoiceConfig.from_env({}).direct_speech is True
    assert VoiceConfig.from_env({"PERSONA_VOICE_DIRECT_SPEECH": "0"}).direct_speech is False


# --- store round trips ----------------------------------------------------------


@pytest.fixture(scope="module")
def dsn():
    pytest.importorskip("psycopg")
    pytest.importorskip("psycopg_pool")
    from agent.store.testing import ephemeral_dsn

    d = ephemeral_dsn()
    if d is None:
        pytest.skip("no ephemeral Postgres (set PERSONA_TEST_DATABASE_URL or install initdb/pg_ctl)")
    return d


@pytest.fixture
def service(dsn):
    from agent.api.llm import FakeLlm
    from agent.api.service import Notifier, SessionService
    from agent.obs.tracing import NoopTracer
    from agent.store import PgStore
    from agent.store.testing import reset

    reset(dsn)
    store = PgStore(dsn, max_size=4)
    yield SessionService(store=store, llm=FakeLlm(), spec=SPEC, tracer=NoopTracer(), notifier=Notifier())
    store.close()


def test_voice_turn_makes_two_db_round_trips_and_no_reload(service):
    pytest.importorskip("pipecat_flows")
    from agent.voice.flows import ServiceBrain, VoiceFlow

    sid, _, _ = service.create()
    brain = ServiceBrain(service, sid)
    ctx = _Ctx()
    flow = VoiceFlow(SPEC, brain, context=ctx)
    _run(flow.opening())
    for u in ["I'm Sam, S-A-M", "help me triage my inbox every morning"]:
        ctx.messages.append({"role": "user", "content": u})
        result, _ = _run(flow.handle_record_slots(_args(u)))
        assert brain.last_db.calls == 2             # load state+lease, one-statement commit
        assert flow.last.state == service.store.load(sid)   # the committed state, not a re-read
    kinds = [e.kind for e in service.store.events_after(sid, 0)]
    assert kinds.count("user_utterance") == 2 and "transition" in kinds


def test_one_statement_commit_keeps_version_check_and_event_order(service):
    from agent.store import NotFoundError, VersionConflictError
    from agent.store import perf

    sid, _, _ = service.create()
    store = service.store
    st, lease = store.load_for_turn(sid)
    assert lease is None and st.version == store.load(sid).version
    evs = [{"kind": "brain", "channel": "voice", "trace_id": "t", "payload": {"i": i}} for i in range(5)]
    with perf.collect() as db:
        v = store.commit(st, expected_version=st.version, events=evs)
    assert v == st.version + 1 and db.calls == 1
    got = [e.payload.get("i") for e in store.events_after(sid, 0, kinds=["brain"]) if "i" in e.payload]
    assert got == [0, 1, 2, 3, 4]
    before = len(store.events_after(sid, 0))
    with pytest.raises(VersionConflictError):
        store.commit(st, expected_version=st.version, events=evs)     # stale: nothing written
    assert len(store.events_after(sid, 0)) == before
    ghost = SessionState(session_id="00000000-0000-0000-0000-000000000000")
    with pytest.raises(NotFoundError):
        store.commit(ghost, expected_version=0, events=evs)


def test_load_for_turn_reports_a_live_lease(service):
    sid, _, _ = service.create()
    lease = service.acquire_call(sid, ttl_s=30)
    _, got = service.store.load_for_turn(sid)
    assert got is not None and got.call_id == lease.call_id


# --- LAT-001 continuation: ICE cred cache, turn-detection tuning ---------------------------

def test_minted_ice_is_reused_until_it_nears_expiry(monkeypatch):
    import asyncio as _asyncio

    from agent.voice import ice

    ice.reset_ice_cache()
    monkeypatch.setenv("CLOUDFLARE_TURN_KEY_ID", "k")
    monkeypatch.setenv("CLOUDFLARE_TURN_API_TOKEN", "t")
    mints = []

    async def fake_mint(ttl):
        mints.append(ttl)
        return [{"urls": ["turn:turn.example.test:3478?transport=udp"], "username": f"u{len(mints)}", "credential": "c"}]

    now = [1000.0]
    monkeypatch.setattr(ice, "cloudflare_ice_servers", fake_mint)
    monkeypatch.setattr(ice.time, "monotonic", lambda: now[0])
    try:
        s1, ttl1 = _asyncio.run(ice.resolve_ice())
        now[0] += 60
        s2, ttl2 = _asyncio.run(ice.resolve_ice())
        assert len(mints) == 1 and s2[0]["username"] == "u1" and ttl1 == 3600 and ttl2 == 3540
        s2[0]["username"] = "mutated"                    # callers get copies
        now[0] += ice.ICE_TTL_S - ice.ICE_MIN_REMAINING_S  # < 15 min left: mint fresh creds
        s3, ttl3 = _asyncio.run(ice.resolve_ice())
        assert len(mints) == 2 and s3[0]["username"] == "u2" and ttl3 == 3600
    finally:
        ice.reset_ice_cache()


def test_turn_detection_is_tuned_for_latency():
    from agent.voice import session as vs

    assert vs.VAD_STOP_S == 0.2 and vs.TURN_MAX_SILENCE_S <= 2.0
    params = vs.build_user_params(vs.VoiceConfig())
    assert params.vad_analyzer.params.stop_secs == 0.2
    assert params.vad_analyzer.params.start_secs == 0.2 and params.vad_analyzer.params.confidence == 0.7  # barge-in unchanged
    stop = params.user_turn_strategies.stop[0]
    assert stop._turn_analyzer.params.stop_secs == vs.TURN_MAX_SILENCE_S
