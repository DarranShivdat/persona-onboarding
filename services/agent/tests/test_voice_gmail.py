"""VOICE-004-lite: Gmail on the call — the same card + OAuth as text, a "type it" escape
through the shared text path, no spoken NATO. Offline (no vendor network).

EC-19 voice (privacy question at gmail) · EC-20 voice (refuse -> skipped + graduate) ·
EC-21 voice (consent failed -> the call offers retry / type-it / skip; gmail untouched) ·
card pushed on the call · OAuth fill heard by the call (partial grant stated) · hangup and
reconnect keep the Gmail step. The Postgres half skips without an ephemeral Postgres.
"""
import asyncio

import pytest

pytest.importorskip("pipecat_flows")

from agent.brain.engine import Extraction, ResponsePlan, Turn, apply  # noqa: E402
from agent.brain.state import SessionState, SlotValue  # noqa: E402
from agent.llm import templates as T  # noqa: E402
from agent.llm.extract import parse  # noqa: E402
from agent.voice.flows import (GMAIL_CONNECTED, GMAIL_FAILED, GMAIL_TYPE_IT, GMAIL_TYPED, TYPED_ACK,  # noqa: E402
                               LocalBrain, VoiceFlow, gmail_oauth_line, voice_line, voice_tools)

from test_voice_flows import SPEC, _args, _Ctx  # noqa: E402

G = "https://www.googleapis.com/auth/"
FULL = ["openid", "email", "profile", f"{G}gmail.readonly", f"{G}gmail.modify", f"{G}gmail.send"]
NO_SEND = FULL[:-1]
REFUSE_X = Extraction(intents=["refuse_slot"])


def _run(coro):
    return asyncio.run(coro)


def _at_gmail() -> SessionState:
    st = SessionState(session_id="g1", node="gmail", active_channel="voice")
    for k, v in {"agent_name": "Nova", "user_name": "Sam", "need": "triage my inbox"}.items():
        st.slots[k] = SlotValue(value=v, status="filled", source="voice")
    return st


def _spelled(line: str) -> bool:
    return " as in " in line or "Alpha" in line


async def _say(flow, ctx, utterance, args):
    ctx.messages.append({"role": "user", "content": utterance})
    return await flow.handle_record_slots(args, None)


# --- pure: the gmail ask on the call ------------------------------------------------------


def test_gmail_ask_on_call_pushes_card_and_offers_type_it():
    vt = _run(LocalBrain(SPEC, _at_gmail()).event("call_started"))
    assert "gmail_connect_card" in vt.plan.push_ui and vt.plan.ask == "gmail"
    assert "Connect Gmail button" in vt.line and GMAIL_TYPE_IT not in vt.line
    assert voice_tools(SPEC, "gmail") == ["record_slots"]   # card + type-it are code, not LLM tools


def test_ec19_voice_privacy_question_answers_and_stays_at_gmail():
    flow = VoiceFlow(SPEC, LocalBrain(SPEC, _at_gmail()), context=_Ctx())
    result, node = _run(_say(flow, flow.context, "what do you do with my email?",
                             _args("what do you do with my email?")))
    assert node["name"] == "gmail" and result["node"] == "gmail"
    assert T.RESPOND["privacy_question"] in result["say"]
    assert "Connect Gmail button" in result["say"] and "gmail_connect_card" in flow.last.plan.push_ui
    assert not flow.last.state.filled("gmail")


def test_ec20_voice_refusal_skips_gmail_and_graduates_with_deferred_prompt():
    said = []

    async def graduated(line):
        said.append(line)

    brain = LocalBrain(SPEC, _at_gmail())
    flow = VoiceFlow(SPEC, brain, context=_Ctx(), on_graduated=graduated)
    result, node = _run(_say(flow, flow.context, "no, I don't want to connect Gmail", _args_for(REFUSE_X)))
    st = _run(brain.current())
    assert st.slot("gmail").status == "skipped" and st.graduated and "gmail" in st.deferred_prompts
    assert result["graduated"] and node["name"] == "graduated"
    assert len(said) == 1 and "connect Gmail from the main screen" in said[0]


def test_typed_email_on_call_is_never_spelled_back():
    st = _at_gmail()
    st.slots["gmail"] = SlotValue(value="priya.k@gmail.com", status="candidate", source="text")
    plan = apply(SPEC, st, Turn(channel="voice", event="call_started")).plan
    line = voice_line(SPEC, plan, st)
    assert not _spelled(line) and GMAIL_TYPED in line and GMAIL_TYPE_IT not in line
    assert T.email_readback("priya.k@gmail.com") not in line


def test_spoken_email_stays_candidate_and_is_not_spelled():
    brain = LocalBrain(SPEC, _at_gmail())
    flow = VoiceFlow(SPEC, brain, context=_Ctx())
    x = Extraction(slots={"gmail": "sam@example.com"})
    result, node = _run(_say(flow, flow.context, "it's sam at example dot com", _args_for(x)))
    st = _run(brain.current())
    assert st.slot("gmail").status == "candidate" and not st.graduated and node["name"] == "gmail"
    assert not _spelled(result["say"]) and GMAIL_TYPED in result["say"]


def _args_for(x: Extraction) -> dict:
    from agent.llm.testing import tool_response

    return tool_response(SPEC, x.slots, x.intents)["content"][0]["input"]


def test_gmail_oauth_line_states_a_partial_grant_plainly():
    assert gmail_oauth_line({"scopes": FULL}) == GMAIL_CONNECTED
    assert gmail_oauth_line(None) == GMAIL_CONNECTED
    no_send = gmail_oauth_line({"scopes": NO_SEND})
    assert no_send.startswith(GMAIL_CONNECTED) and "send email" in no_send and "read your email" not in no_send
    two = gmail_oauth_line({"scopes": FULL[:4]})
    assert "organize your inbox or send email" in two


def test_failed_consent_off_the_gmail_step_says_nothing():
    st = _at_gmail()
    st.node = "need"
    flow = VoiceFlow(SPEC, LocalBrain(SPEC, st))
    assert _run(flow.typed_turn(ResponsePlan(node="need"), source="gmail_failed")) is None


def test_call_session_routes_gmail_results_to_the_flow():
    from unittest.mock import MagicMock

    from agent.voice.config import VoiceConfig
    from agent.voice.session import CallSession

    calls = []

    class _FM:
        async def set_node_from_config(self, node):
            calls.append(("node", node["name"]))

    class _Flow:
        last = None

        async def typed_turn(self, plan, *, source="text", data=None):
            calls.append((source, data))
            return {"name": "gmail"}

    s = CallSession(MagicMock(pc_id="pc"), VoiceConfig(), call_id="c1")
    s.flow, s._flow_manager = _Flow(), _FM()
    _run(s.on_typed(object(), "gmail_failed", {"reason": "cancelled"}))
    assert calls == [("gmail_failed", {"reason": "cancelled"}), ("node", "gmail")]


# --- shared session (Postgres): card on call, type-it, OAuth, EC-21, hangup / reconnect -----

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")

from fastapi.testclient import TestClient  # noqa: E402

from agent.api.app import Settings, create_app  # noqa: E402
from agent.api.llm import FakeLlm  # noqa: E402
from agent.api.service import Notifier, SessionService  # noqa: E402
from agent.obs.tracing import NoopTracer  # noqa: E402
from agent.store import PgStore  # noqa: E402
from agent.store.testing import ephemeral_dsn, reset  # noqa: E402
from agent.voice.flows import ServiceBrain  # noqa: E402
from agent.voice.handoff import CallControl, HandoffSettings  # noqa: E402


@pytest.fixture(scope="module")
def dsn():
    d = ephemeral_dsn()
    if d is None:
        pytest.skip("no ephemeral Postgres (set PERSONA_TEST_DATABASE_URL or install initdb/pg_ctl)")
    return d


@pytest.fixture
def store(dsn):
    reset(dsn)
    s = PgStore(dsn, max_size=8)
    yield s
    s.close()


@pytest.fixture
def service(store):
    return SessionService(store=store, llm=FakeLlm(), spec=SPEC, tracer=NoopTracer(), notifier=Notifier())


def _x(u) -> Extraction:
    return parse(SPEC, _args(u))


def _to_gmail_on_call(service) -> tuple[str, str]:
    """Agent name typed, call accepted and live, name + need said on the call -> gmail."""
    sid, _, _ = service.create()
    service.llm.push(_x("Call it Nova"), _x("sure, let's talk"))
    service.text_turn(sid, "Call it Nova")
    service.text_turn(sid, "sure, let's talk")
    call_id = service.acquire_call(sid, ttl_s=60).call_id
    return sid, call_id


async def _call(service, sid, utterances=("I'm Sam, S-A-M", "yes", "help me triage my inbox every morning"), **kw):
    ctx = _Ctx()
    flow = VoiceFlow(SPEC, ServiceBrain(service, sid), context=ctx, **kw)
    await flow.opening()
    for u in utterances:
        await _say(flow, ctx, u, _args(u))
    return flow


def _pushes(store, sid, type_):
    return [e for e in store.events_after(sid, kinds=["ui_push"], limit=1000) if e.payload["type"] == type_]


async def _watched(service, sid, flow, action):
    """Run `action` (a sync service call) and collect what the call's listener receives."""
    seen = []
    loop = asyncio.get_running_loop()
    unwatch = flow.brain.watch_text(lambda out: loop.call_soon_threadsafe(seen.append, out))
    try:
        ret = await asyncio.to_thread(action)
        await asyncio.sleep(0.05)
    finally:
        unwatch()
    return ret, seen


def test_card_is_pushed_on_the_call(service, store):
    sid, _ = _to_gmail_on_call(service)
    flow = _run(_call(service, sid))
    assert flow.last.plan.node == "gmail" and "Connect Gmail button" in flow.last.line
    cards = _pushes(store, sid, "gmail_connect_card")
    assert cards and cards[-1].channel == "voice" and cards[-1].payload["data"] == {"node": "gmail"}


def test_type_it_goes_through_the_text_path_and_is_acknowledged_without_spelling(service):
    sid, _ = _to_gmail_on_call(service)

    async def go():
        flow = await _call(service, sid)
        service.llm.push(Extraction(slots={"gmail": "priya.k@gmail.com"}))
        _, seen = await _watched(service, sid, flow, lambda: service.text_turn(sid, "priya.k@gmail.com"))
        return await flow.typed_turn(seen[0].plan, source=seen[0].source)

    node = _run(go())
    st = service.store.load(sid)
    assert st.slot("gmail").status == "candidate" and st.slot("gmail").source == "text"
    assert st.node == "gmail" and st.active_channel == "voice" and not st.graduated
    said = node["pre_actions"][0]["text"]
    assert said.startswith(TYPED_ACK) and GMAIL_TYPED in said and not _spelled(said)


def test_oauth_on_the_call_is_heard_and_graduates_with_partial_grant_stated(service, store):
    sid, _ = _to_gmail_on_call(service)
    said = []

    async def graduated(line):
        said.append(line)

    async def go():
        flow = await _call(service, sid, on_graduated=graduated)
        _, seen = await _watched(service, sid, flow, lambda: service.gmail_connected(
            sid, email="maya.r@gmail.com", google_sub="g-maya", scopes=NO_SEND))
        assert [o.source for o in seen] == ["gmail_oauth"]
        return await flow.typed_turn(seen[0].plan, source=seen[0].source, data=seen[0].data)

    node = _run(go())
    st = store.load(sid)
    assert st.filled("gmail") and st.slot("gmail").value == "maya.r@gmail.com" and st.graduated
    assert node["name"] == "graduated" and len(said) == 1
    assert said[0].startswith(GMAIL_CONNECTED) and "send email" in said[0] and "You're all set, Sam." in said[0]
    assert _pushes(store, sid, "gmail_connected")[-1].payload["data"] == {"email": "maya.r@gmail.com"}


def test_ec21_voice_failed_consent_is_noticed_without_touching_state(service, store):
    sid, _ = _to_gmail_on_call(service)

    async def go():
        flow = await _call(service, sid)
        before = store.load(sid).version
        noticed, seen = await _watched(service, sid, flow, lambda: service.gmail_failed(sid, reason="cancelled"))
        node = await flow.typed_turn(seen[0].plan, source=seen[0].source, data=seen[0].data)
        return before, noticed, seen, node

    before, noticed, seen, node = _run(go())
    st = store.load(sid)
    assert noticed is True and [o.source for o in seen] == ["gmail_failed"]
    assert st.version == before and st.slot("gmail").status == "empty" and st.node == "gmail"
    assert node["name"] == "gmail" and node["pre_actions"][0]["text"] == GMAIL_FAILED
    logged = [e for e in store.events_after(sid, limit=1000)
              if e.kind == "brain" and e.payload.get("type") == "gmail_oauth_failed"]
    assert len(logged) == 1 and logged[0].payload["reason"] == "cancelled"
    # then skipping by voice continues the flow (EC-20 path)
    async def skip():
        flow = VoiceFlow(SPEC, ServiceBrain(service, sid), context=_Ctx())
        return await _say(flow, flow.context, "not now", _args("not now"))
    result, _ = _run(skip())
    st = store.load(sid)
    assert result["graduated"] and st.slot("gmail").status == "skipped" and "gmail" in st.deferred_prompts


def test_failed_consent_without_a_live_call_is_only_logged(service, store):
    sid, _, _ = service.create()
    assert service.gmail_failed(sid, reason="window_closed") is False


def test_gmail_failed_route_requires_the_session_token(store):
    app = create_app(store=store, llm=FakeLlm(), spec=SPEC, settings=Settings())
    with TestClient(app) as c:
        body = c.post("/v1/sessions").json()
        sid, token = body["id"], body["token"]
        assert c.post(f"/v1/sessions/{sid}/gmail/failed", json={"reason": "cancelled"}).status_code == 401
        assert c.post(f"/v1/sessions/{sid}/gmail/failed", json={"reason": "DROP TABLE"},
                      headers={"Authorization": f"Bearer {token}"}).status_code == 422
        r = c.post(f"/v1/sessions/{sid}/gmail/failed", json={"reason": "cancelled"},
                   headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and r.json() == {"noticed": False}


def test_hangup_keeps_gmail_progress_and_oauth_after_the_call_still_fills(service, store):
    sid, call_id = _to_gmail_on_call(service)

    async def go():
        flow = await _call(service, sid)
        service.llm.push(Extraction(slots={"gmail": "priya.k@gmail.com"}))
        await asyncio.to_thread(service.text_turn, sid, "priya.k@gmail.com")
        return await CallControl(service).end(sid, call_id, "user_hangup")

    cards_before = len(_pushes(store, sid, "gmail_connect_card"))
    released, outcome = _run(go())
    st = store.load(sid)
    assert released and outcome is not None
    assert st.node == "gmail" and st.active_channel is None and not st.graduated
    assert st.slot("gmail").status == "candidate" and st.slot("gmail").value == "priya.k@gmail.com"
    for k in ("agent_name", "user_name", "need"):
        assert st.filled(k), k
    resume = _pushes(store, sid, "call_resume")[-1].payload["data"]
    assert resume["remaining"] == ["gmail"]
    assert len(_pushes(store, sid, "gmail_connect_card")) > cards_before   # card re-shown in chat

    service.gmail_connected(sid, email="priya.k@gmail.com", google_sub="g-priya", scopes=FULL)
    st = store.load(sid)
    assert st.filled("gmail") and st.graduated


def test_reconnect_inside_grace_resumes_at_gmail_with_card_and_type_it(service, store):
    sid, call_id = _to_gmail_on_call(service)
    control = CallControl(service, HandoffSettings(grace_s=30))

    async def go():
        await _call(service, sid)
        assert await control.dropped(sid, call_id)
        assert await control.reconnect(sid, call_id) is not None
        flow = VoiceFlow(SPEC, ServiceBrain(service, sid), context=_Ctx())
        node = await flow.opening(reconnect=True)
        await control.close()
        return node, flow

    node, flow = _run(go())
    said = node["pre_actions"][0]["text"]
    assert node["name"] == "gmail" and said.startswith(T.RESUME["voice"]) and "Connect Gmail button" in said
    assert "gmail_connect_card" in flow.last.plan.push_ui
    assert store.load(sid).slot("need").status == "filled"
