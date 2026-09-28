"""VOICE-003: call lease, take-over, reconnect grace, hangup -> chat resume, typing during
a call. API + service + CallControl over an ephemeral Postgres; the call itself is the
VOICE-002 Flows handler over ServiceBrain (no WebRTC, no vendor network).

EC-01 hangup resumes in chat · EC-02 one live call + explicit take-over · EC-04 reconnect
inside the grace window / hangup after it · EC-28 typed text merges into the call's turn
stream. UI rendering of these pushes is FE-004.
"""
import asyncio
import time
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pipecat_flows")
pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")

from fastapi.testclient import TestClient  # noqa: E402

from agent.api.app import Settings, create_app  # noqa: E402
from agent.api.llm import FakeLlm  # noqa: E402
from agent.api.service import Notifier, SessionService, resume_copy  # noqa: E402
from agent.brain.engine import Extraction  # noqa: E402
from agent.llm.extract import parse  # noqa: E402
from agent.obs.tracing import NoopTracer  # noqa: E402
from agent.store import LeaseHeldError, PgStore  # noqa: E402
from agent.store.testing import ephemeral_dsn, reset  # noqa: E402
from agent.voice.config import VoiceConfig  # noqa: E402
from agent.voice.flows import TYPED_ACK, ServiceBrain, VoiceFlow  # noqa: E402
from agent.voice.handoff import CallControl, HandoffSettings  # noqa: E402
from agent.voice.session import CallSession  # noqa: E402

from test_voice_flows import SPEC, _args, _Ctx  # noqa: E402


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


def _x(utterance) -> Extraction:
    return parse(SPEC, _args(utterance))


def _on_call(service) -> str:
    """Agent name typed in chat, call accepted (EC-01 setup)."""
    sid, _, _ = service.create()
    service.llm.push(_x("Call it Nova"), _x("sure, let's talk"))
    service.text_turn(sid, "Call it Nova")
    service.text_turn(sid, "sure, let's talk")
    return sid


async def _talk(service, sid, utterances, *, reconnect=False):
    ctx = _Ctx()
    flow = VoiceFlow(SPEC, ServiceBrain(service, sid), context=ctx)
    opening = await flow.opening(reconnect=reconnect)
    for u in utterances:
        ctx.messages.append({"role": "user", "content": u})
        await flow.handle_record_slots(_args(u), None)
    return flow, opening


def _pushes(store, sid, type_=None):
    evs = store.events_after(sid, kinds=["ui_push"], limit=1000)
    return [e.payload for e in evs if type_ is None or e.payload["type"] == type_]


def _status(snap):
    return {k: v["status"] for k, v in snap["slots"].items()}


def _run(coro):
    return asyncio.run(coro)


# --- EC-02: lease + take-over ----------------------------------------------------------


def test_second_call_is_409_until_explicit_take_over(store):
    app = create_app(store=store, llm=FakeLlm(), spec=SPEC, settings=Settings())
    with TestClient(app) as c:
        body = c.post("/v1/sessions").json()
        sid, auth = body["id"], {"Authorization": f"Bearer {body['token']}"}
        a = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "v=0", "type": "offer"}, headers=auth)
        assert a.status_code == 201 and a.json()["status"] == "lease_acquired"
        tab_a = a.json()["call_id"]

        b = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "v=0"}, headers=auth)
        assert b.status_code == 409 and b.json() == {"error": "call_in_progress", "can_take_over": True}
        assert c.get(f"/v1/sessions/{sid}", headers=auth).json()["call"]["call_id"] == tab_a  # lease untouched

        assert c.post(f"/v1/sessions/{sid}/call/{tab_a}/heartbeat", headers=auth).json() == {"ok": True}

        t = c.post(f"/v1/sessions/{sid}/call", json={"take_over": True}, headers=auth)
        assert t.status_code == 201 and t.json()["status"] == "taken_over" and t.json()["replaced"] == tab_a
        tab_b = t.json()["call_id"]
        assert c.get(f"/v1/sessions/{sid}", headers=auth).json()["call"]["call_id"] == tab_b

        # Tab A's pipeline learns on its next heartbeat and must stop talking.
        hb = c.post(f"/v1/sessions/{sid}/call/{tab_a}/heartbeat", headers=auth)
        assert hb.status_code == 409 and hb.json()["error"] == "call_lease_lost"
        # Tab A hanging up late can't release (or resume over) tab B's call.
        assert c.request("DELETE", f"/v1/sessions/{sid}/call/{tab_a}", headers=auth).json() == {"released": False}
        assert c.get(f"/v1/sessions/{sid}", headers=auth).json()["call"]["call_id"] == tab_b

    states = [(p["data"]["state"], p["data"]["call_id"], p["data"].get("reason")) for p in _pushes(store, sid, "call_state")]
    assert ("ended", tab_a, "taken_over") in states and ("ringing", tab_b, None) in states
    with store.pool.connection() as conn:
        row = conn.execute("select end_reason from calls where id = %s", (tab_a,)).fetchone()
    assert row["end_reason"] == "taken_over"


def test_take_over_hangs_up_the_old_in_process_pipeline(service):
    sid, _, _ = service.create()

    async def go():
        control = CallControl(service, HandoffSettings())
        old = await control.start(sid)
        hung = []

        async def hangup(reason):
            hung.append(reason)
        control.attach(old.call_id, hangup)
        with pytest.raises(LeaseHeldError):
            await control.start(sid)
        assert hung == []
        new = await control.start(sid, take_over=True)
        return old, new, hung

    old, new, hung = _run(go())
    assert hung == ["taken_over"] and new.replaced == old.call_id
    assert service.store.lease(sid).call_id == new.call_id


def test_pipeline_ends_itself_when_its_lease_is_lost():
    beats = []

    async def heartbeat():
        beats.append(1)
        return len(beats) < 2  # second renewal: lease gone (taken over elsewhere)

    s = CallSession(MagicMock(pc_id="pc"), VoiceConfig(), call_id="c1", heartbeat=heartbeat, heartbeat_s=0.01)
    _run(asyncio.wait_for(s._heartbeat_loop(), 2))
    assert s.teardown.reason == "lease_lost" and len(beats) == 2


def test_attach_wires_heartbeat_and_hangup(service):
    sid, _, _ = service.create()

    async def go():
        control = CallControl(service, HandoffSettings(heartbeat_s=7))
        lease = await control.start(sid)
        s = CallSession(MagicMock(pc_id="pc"), VoiceConfig(), call_id=lease.call_id)
        s.attach(control, sid)
        assert s._heartbeat_s == 7 and await s._heartbeat() is True
        await s._on_ended("user_hangup")          # the pipeline ended: lease released
        return await control.heartbeat(sid, lease.call_id)

    assert _run(go()) is False and service.store.lease(sid) is None


# --- EC-01: hangup -> chat resume ---------------------------------------------------------


def test_hangup_resumes_in_chat_naming_whats_left(store):
    app = create_app(store=store, llm=FakeLlm(), spec=SPEC, settings=Settings())
    svc = app.state.service
    sid = _on_call(svc)
    with TestClient(app) as c:
        svc.check_token = lambda _sid, _tok: True  # session created via the service, not the route
        call_id = c.post(f"/v1/sessions/{sid}/call", headers={"Authorization": "Bearer x"}).json()["call_id"]
        _run(_talk(svc, sid, ["I'm Sam, S-A-M", "yes", "help me triage my inbox every morning"]))
        mid = svc.get_snapshot(sid)
        assert mid["active_channel"] == "voice" and mid["node"] == "gmail"

        r = c.request("DELETE", f"/v1/sessions/{sid}/call/{call_id}", json={"reason": "user_hangup"},
                      headers={"Authorization": "Bearer x"})
        assert r.status_code == 200
        body = r.json()

    assert body["released"] is True
    st = body["state"]
    assert _status(st) == {"agent_name": "filled", "user_name": "filled", "need": "filled", "gmail": "empty"}
    assert st["node"] == "gmail" and st["active_channel"] is None and st["call"]["live"] is False
    # nothing re-asked: the chat line picks up at Gmail
    assert "Gmail" in body["reply"] and "call you" not in body["reply"] and "help with" not in body["reply"]
    resume = _pushes(store, sid, "call_resume")
    assert len(resume) == 1
    data = resume[0]["data"]
    assert data["remaining"] == ["gmail"] and data["call_id"] == call_id and data["can_call_back"] is True
    assert "connecting Gmail" in data["message"] and "call back" in data["message"]
    assert any(p["data"] == {"state": "ended", "call_id": call_id, "reason": "user_hangup"}
               for p in _pushes(store, sid, "call_state"))


def test_hangup_is_idempotent_and_resumes_once(service):
    sid = _on_call(service)

    async def go():
        control = CallControl(service)
        lease = await control.start(sid)
        await _talk(service, sid, ["I'm Sam, S-A-M", "yes"])
        first = await control.end(sid, lease.call_id, "user_hangup")
        second = await control.end(sid, lease.call_id, "client_gone")
        return first, second

    (r1, o1), (r2, o2) = _run(go())
    assert r1 and o1 is not None and not r2 and o2 is None
    assert len(_pushes(service.store, sid, "call_resume")) == 1


def test_call_that_never_connected_leaves_chat_untouched(service):
    sid = _on_call(service)
    v = service.store.load(sid).version

    async def go():
        control = CallControl(service)
        lease = await control.start(sid)
        return await control.end(sid, lease.call_id, "user_hangup")

    released, outcome = _run(go())
    assert released and outcome is None and service.store.load(sid).version == v
    assert _pushes(service.store, sid, "call_resume") == []


def test_orphaned_call_is_finished_before_the_next_text_turn(service):
    """Process died mid-call: the lease expires, the next chat turn resumes cleanly."""
    sid = _on_call(service)
    service.acquire_call(sid, ttl_s=0.2)
    _run(_talk(service, sid, ["I'm Sam, S-A-M", "yes"]))
    time.sleep(0.4)
    service.llm.push(_x("help me triage my inbox every morning"))
    service.text_turn(sid, "help me triage my inbox every morning")
    st = service.store.load(sid)
    assert st.active_channel is None and st.slot("need").status == "filled"
    assert len(_pushes(service.store, sid, "call_resume")) == 1


def test_resume_copy():
    assert resume_copy(["gmail"]) == ("The call ended, but everything so far is saved. Still left: "
                                      "connecting Gmail. You can call back or keep going here.")
    assert "your name, what you'd like help with and connecting Gmail" in resume_copy(["user_name", "need", "gmail"])
    assert "all set" in resume_copy([])


# --- EC-04: reconnect grace ------------------------------------------------------------------


class _Clock:
    """Grace timers wait on this instead of wall time."""

    def __init__(self):
        self.waiters = []
        self.fired = False

    async def sleep(self, secs):
        if self.fired:
            return
        fut = asyncio.get_running_loop().create_future()
        self.waiters.append(fut)
        await fut

    def fire(self):
        self.fired = True
        for f in self.waiters:
            if not f.done():
                f.set_result(None)
        self.waiters.clear()


def test_reconnect_inside_grace_resumes_same_call(service):
    sid = _on_call(service)
    clock = _Clock()

    async def go():
        control = CallControl(service, HandoffSettings(grace_s=20), sleep=clock.sleep)
        lease = await control.start(sid)
        s = CallSession(MagicMock(pc_id="pc"), VoiceConfig(), call_id=lease.call_id)
        s.attach(control, sid)
        await _talk(service, sid, ["I'm Sam, S-A-M", "yes"])
        await s._on_ended("client_disconnected")        # Wi-Fi drop: not a hangup
        dropped = service.get_snapshot(sid)
        assert control.in_grace(lease.call_id)
        back = await control.reconnect(sid, lease.call_id)
        assert back is not None and back.call_id == lease.call_id and not control.in_grace(lease.call_id)
        clock.fire()                                     # the cancelled timer never ends the call
        await asyncio.sleep(0)
        _, opening = await _talk(service, sid, [], reconnect=True)
        return lease, dropped, opening

    lease, dropped, opening = _run(go())
    assert dropped["active_channel"] == "voice" and dropped["call"]["live"] is True
    st = service.get_snapshot(sid)
    assert st["node"] == "need" and st["active_channel"] == "voice" and st["call"]["call_id"] == lease.call_id
    assert "we got cut off" in opening["pre_actions"][0]["text"]
    assert "help with" in opening["pre_actions"][0]["text"]          # asks what's next, not the name again
    assert _pushes(service.store, sid, "call_resume") == []
    with service.store.pool.connection() as conn:
        assert conn.execute("select reconnects from calls where id = %s", (lease.call_id,)).fetchone()["reconnects"] == 1
    states = [p["data"]["state"] for p in _pushes(service.store, sid, "call_state")]
    assert states[-2:] == ["reconnecting", "live"]


def test_grace_expiry_is_a_hangup(service):
    sid = _on_call(service)
    clock = _Clock()

    async def go():
        control = CallControl(service, HandoffSettings(grace_s=20), sleep=clock.sleep)
        lease = await control.start(sid)
        await _talk(service, sid, ["I'm Sam, S-A-M", "yes", "help me triage my inbox every morning"])
        await control.disconnected(sid, lease.call_id, "client_disconnected")
        assert service.store.load(sid).active_channel == "voice"
        clock.fire()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if not control.in_grace(lease.call_id):
                break
        late = await control.reconnect(sid, lease.call_id)
        return lease, control.ended.get(lease.call_id), late

    lease, reason, late = _run(go())
    assert reason == "network_timeout" and late is None
    st = service.get_snapshot(sid)
    assert st["active_channel"] is None and st["node"] == "gmail" and st["call"]["live"] is False
    assert _pushes(service.store, sid, "call_resume")[0]["data"]["remaining"] == ["gmail"]


def test_api_reconnect_route_and_real_grace_window(store):
    app = create_app(store=store, llm=FakeLlm(), spec=SPEC, settings=Settings(call_grace_s=0.3))
    svc = app.state.service
    sid = _on_call(svc)
    svc.check_token = lambda _sid, _tok: True
    auth = {"Authorization": "Bearer x"}
    with TestClient(app) as c:
        call_id = c.post(f"/v1/sessions/{sid}/call", headers=auth).json()["call_id"]
        _run(_talk(svc, sid, ["I'm Sam, S-A-M", "yes"]))
        d = c.request("DELETE", f"/v1/sessions/{sid}/call/{call_id}", json={"reason": "network_drop"}, headers=auth)
        assert d.json() == {"released": False, "in_grace": True, "grace_s": 0.3}
        r = c.post(f"/v1/sessions/{sid}/call", json={"resume_call_id": call_id}, headers=auth)
        assert r.status_code == 200 and r.json()["status"] == "resumed" and r.json()["call_id"] == call_id

        c.request("DELETE", f"/v1/sessions/{sid}/call/{call_id}", json={"reason": "network_drop"}, headers=auth)
        deadline = time.time() + 5
        while svc.store.load(sid).active_channel == "voice" and time.time() < deadline:
            time.sleep(0.05)
        late = c.post(f"/v1/sessions/{sid}/call", json={"resume_call_id": call_id}, headers=auth)
        assert late.status_code == 409 and late.json()["error"] == "call_ended"
        assert c.post(f"/v1/sessions/{sid}/call", headers=auth).status_code == 201   # a fresh call is fine
    assert svc.store.load(sid).active_channel is None
    assert len(_pushes(store, sid, "call_resume")) == 1


def test_handoff_settings_from_env():
    s = HandoffSettings.from_env({"PERSONA_CALL_GRACE_S": "5", "PERSONA_CALL_HEARTBEAT_S": "2"})
    assert (s.grace_s, s.heartbeat_s, s.lease_ttl_s) == (5.0, 2.0, 120.0)
    assert HandoffSettings.from_env({}).grace_s == 20.0


# --- EC-28: typing during a live call --------------------------------------------------------


def test_typed_text_during_call_merges_and_is_acknowledged_by_voice(service):
    sid = _on_call(service)
    service.acquire_call(sid, ttl_s=60)
    seen = []

    async def go():
        flow, _ = await _talk(service, sid, ["I'm Sam, S-A-M", "yes", "help me triage my inbox every morning"])
        loop = asyncio.get_running_loop()
        unwatch = flow.brain.watch_text(lambda out: loop.call_soon_threadsafe(seen.append, out))
        before = service.store.load(sid).version
        service.llm.push(Extraction(slots={"gmail": "priya.k@gmail.com"}))
        await asyncio.to_thread(service.text_turn, sid, "priya.k@gmail.com")
        await asyncio.sleep(0.05)
        unwatch()
        node = await flow.typed_turn(seen[0].plan)
        return before, node

    before, node = _run(go())
    st = service.store.load(sid)
    assert st.version == before + 1                       # processed exactly once
    assert st.slot("gmail").status == "candidate" and st.node == "gmail" and st.active_channel == "voice"
    assert len(seen) == 1
    assert node["name"] == "gmail" and node["pre_actions"][0]["text"].startswith(TYPED_ACK)
    typed = [e for e in service.store.events_after(sid, limit=1000)
             if e.kind == "user_utterance" and e.payload["text"] == "priya.k@gmail.com"]
    assert len(typed) == 1 and typed[0].channel == "text"


def test_call_session_speaks_typed_turn_and_moves_node():
    class _FM:
        def __init__(self):
            self.nodes = []

        async def set_node_from_config(self, node):
            self.nodes.append(node)

    class _Flow:
        last = None

        async def typed_turn(self, plan):
            return {"name": "gmail", "pre_actions": [{"type": "tts_say", "text": f"{TYPED_ACK} ok"}]}

    s = CallSession(MagicMock(pc_id="pc"), VoiceConfig(), call_id="c1")
    s.flow, s._flow_manager = _Flow(), _FM()
    s.silence.start()
    s.silence.stage = 2
    _run(s.on_typed(object()))
    assert s._flow_manager.nodes[0]["name"] == "gmail" and s.silence.stage == 0   # typing counts as activity
