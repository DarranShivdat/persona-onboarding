"""VOICE-005: the agent API hosts the call pipeline (real SDP answer), the ICE route, and
the process entrypoint. Offline: fake transport/session doubles for the lease wiring, and
one real SmallWebRTC (aiortc, loopback host candidates) call with fake vendors.

Covers: answer when voice is configured / lease-only otherwise; take-over stops the old
pipeline first; hangup / drop / resume go through VOICE-003 CallControl; idempotent
teardown on shutdown (no leaked pipelines); ICE route auth + rate limit; env report
prints names only.
"""
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")

from fastapi.testclient import TestClient  # noqa: E402

from agent.api.app import Settings, create_app  # noqa: E402
from agent.api.llm import FakeLlm  # noqa: E402
from agent.api.ratelimit import SlidingWindowLimiter  # noqa: E402
from agent.main import env_report  # noqa: E402
from agent.store import PgStore  # noqa: E402
from agent.store.testing import ephemeral_dsn, reset  # noqa: E402
from agent.voice.config import VoiceConfig  # noqa: E402
from agent.voice.host import CallHost  # noqa: E402

from test_voice_flows import SPEC  # noqa: E402

FAKE = VoiceConfig(fake_vendors=True)


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


# --- doubles ---------------------------------------------------------------------------

class FakeConnection:
    made: list["FakeConnection"] = []

    def __init__(self, ice_servers):
        self.ice_servers, self.offer, self.disconnects = ice_servers, None, 0
        FakeConnection.made.append(self)

    async def initialize(self, sdp, type):  # noqa: A002 - Pipecat's signature
        if sdp == "bad":
            raise ValueError("unparseable offer")
        self.offer = (sdp, type)

    def get_answer(self):
        return {"sdp": f"v=0 answer-for:{self.offer[0]}", "type": "answer", "pc_id": f"pc-{id(self)}"}

    async def disconnect(self):
        self.disconnects += 1


class FakeSession:
    """Mimics CallSession's lease wiring and idempotent teardown; `run()` lasts until hangup."""
    made: list["FakeSession"] = []

    def __init__(self, connection, cfg, *, call_id, session_id, service, spec, reconnect):
        self.connection, self.call_id, self.reconnect = connection, call_id, reconnect
        self.hung: list[str] = []
        self.on_ended = None
        self.done = asyncio.Event()
        FakeSession.made.append(self)

    def attach(self, control, session_id):
        self.on_ended, _ = control.hooks(session_id, self.call_id)
        control.attach(self.call_id, self.hangup)

    def release_hooks(self):
        self.on_ended = None

    async def hangup(self, reason="server_hangup"):
        if self.done.is_set():
            return
        self.hung.append(reason)
        self.done.set()
        await self.connection.disconnect()
        if self.on_ended:
            await self.on_ended(reason)

    async def run(self):
        await self.done.wait()


async def _ice():
    return [{"urls": ["stun:stun.example.test:3478"]}, {"urls": ["turn:turn.example.test:443?transport=tcp"],
                                                         "username": "u", "credential": "c"}], 600


def _fake_app(store, **kw):
    """App whose CallHost uses the fake transport/session (sharing the app's CallControl)."""
    FakeConnection.made.clear()
    FakeSession.made.clear()
    app = create_app(store=store, llm=FakeLlm(), spec=SPEC, settings=Settings(call_grace_s=kw.pop("grace", 20.0)),
                     ice=_ice, **kw)
    app.state.call_host = host = CallHost(app.state.calls, FAKE, service=app.state.service, spec=SPEC,
                                          ice_provider=_ice_list, connection_factory=FakeConnection,
                                          session_factory=FakeSession)
    return app, host


async def _ice_list():
    return (await _ice())[0]


def _new(c):
    body = c.post("/v1/sessions").json()
    return body["id"], {"Authorization": f"Bearer {body['token']}"}


def _pushes(store, sid, type_):
    return [e.payload["data"] for e in store.events_after(sid, 0, kinds=["ui_push"]) if e.payload.get("type") == type_]


# --- API: answer vs lease-only ---------------------------------------------------------

def test_call_with_sdp_returns_answer_when_voice_configured(store):
    app, host = _fake_app(store)
    with TestClient(app) as c:
        sid, auth = _new(c)
        r = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "v=0 offer", "type": "offer"}, headers=auth)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["answer"]["type"] == "answer" and body["answer"]["sdp"] == "v=0 answer-for:v=0 offer"
        assert body["status"] == "lease_acquired"
        conn = FakeConnection.made[-1]
        assert conn.offer == ("v=0 offer", "offer")
        # server leg uses the same ICE list the browser gets (TURN included)
        assert any("turn:" in u for s in conn.ice_servers for u in s["urls"])
        assert host.live_calls() == [body["call_id"]]
        assert FakeSession.made[-1].reconnect is False


def test_lease_only_when_voice_not_configured(store):
    app = create_app(store=store, llm=FakeLlm(), spec=SPEC, settings=Settings(), voice=VoiceConfig())
    assert app.state.call_host is None
    with TestClient(app) as c:
        sid, auth = _new(c)
        r = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "v=0", "type": "offer"}, headers=auth)
        assert r.status_code == 201 and r.json()["answer"] is None


def test_setup_failure_releases_the_lease(store):
    app, host = _fake_app(store)
    with TestClient(app) as c:
        sid, auth = _new(c)
        r = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "bad", "type": "offer"}, headers=auth)
        assert r.status_code == 502 and r.json()["error"] == "call_setup_failed"
        assert FakeConnection.made[-1].disconnects == 1  # peer closed, not leaked
        assert c.get(f"/v1/sessions/{sid}", headers=auth).json()["call"]["live"] is False
        # the next dial gets the lease
        assert c.post(f"/v1/sessions/{sid}/call", json={"sdp": "v=0", "type": "offer"}, headers=auth).status_code == 201
        assert host.live_calls()


def test_non_offer_sdp_type_is_rejected(store):
    app, _ = _fake_app(store)
    with TestClient(app) as c:
        sid, auth = _new(c)
        assert c.post(f"/v1/sessions/{sid}/call", json={"sdp": "v=0", "type": "answer"}, headers=auth).status_code == 422


# --- take-over / hangup / drop / resume -------------------------------------------------

def test_take_over_stops_the_old_pipeline_before_the_new_one_starts(store):
    app, host = _fake_app(store)
    with TestClient(app) as c:
        sid, auth = _new(c)
        a = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "A", "type": "offer"}, headers=auth).json()
        assert c.post(f"/v1/sessions/{sid}/call", json={"sdp": "B", "type": "offer"}, headers=auth).status_code == 409
        assert len(FakeSession.made) == 1  # a refused dial never builds a pipeline
        b = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "B", "type": "offer", "take_over": True}, headers=auth).json()
        old, new = FakeSession.made
        assert b["status"] == "taken_over" and b["replaced"] == a["call_id"]
        assert old.hung == ["taken_over"] and old.connection.disconnects == 1
        assert new.hung == [] and host.live_calls() == [b["call_id"]]
        # the taken-over leg never resumes in chat
        assert _pushes(store, sid, "call_resume") == []


def test_hangup_goes_through_call_control_and_resumes_in_chat(store):
    app, host = _fake_app(store)
    svc = app.state.service
    with TestClient(app) as c:
        sid, auth = _new(c)
        call_id = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "A", "type": "offer"}, headers=auth).json()["call_id"]
        # what the connected pipeline does first: the brain's call_started moves the session onto voice
        c.portal.call(lambda: _event(svc, sid, "call_started"))
        r = c.request("DELETE", f"/v1/sessions/{sid}/call/{call_id}", json={"reason": "user_hangup"}, headers=auth)
        assert r.json()["released"] is True
        sess = FakeSession.made[-1]
        assert sess.hung == ["user_hangup"]
        assert len(_pushes(store, sid, "call_resume")) == 1
        # idempotent: a second hangup (e.g. pagehide beacon) is a no-op
        again = c.request("DELETE", f"/v1/sessions/{sid}/call/{call_id}", json={"reason": "page_closed"}, headers=auth)
        assert again.json() == {"released": False}
        assert len(_pushes(store, sid, "call_resume")) == 1 and sess.hung == ["user_hangup"]
        _settle(c)
        assert host.live_calls() == []


async def _event(svc, sid, event):
    from agent.voice.flows import ServiceBrain

    return await ServiceBrain(svc, sid).event(event)


def _settle(c):
    c.portal.call(asyncio.sleep, 0.05)


def test_media_drop_opens_grace_and_resume_renegotiates_same_call(store):
    app, host = _fake_app(store)
    with TestClient(app) as c:
        sid, auth = _new(c)
        call_id = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "A", "type": "offer"}, headers=auth).json()["call_id"]
        first = FakeSession.made[-1]
        c.portal.call(first.hangup, "client_disconnected")  # the pipeline saw the peer go away
        assert app.state.calls.in_grace(call_id)
        assert c.get(f"/v1/sessions/{sid}", headers=auth).json()["call"]["live"] is True  # lease kept for grace
        r = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "A2", "type": "offer", "resume_call_id": call_id},
                   headers=auth)
        assert r.status_code == 200 and r.json()["status"] == "resumed" and r.json()["call_id"] == call_id
        assert r.json()["answer"]["sdp"].endswith("A2")
        second = FakeSession.made[-1]
        assert second is not first and second.reconnect is True
        assert not app.state.calls.in_grace(call_id)
        _settle(c)
        assert host.live_calls() == [call_id]


def test_resume_while_old_pipeline_still_runs_retires_it_without_ending_the_call(store):
    app, host = _fake_app(store)
    with TestClient(app) as c:
        sid, auth = _new(c)
        call_id = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "A", "type": "offer"}, headers=auth).json()["call_id"]
        first = FakeSession.made[-1]
        c.portal.call(app.state.calls.dropped, sid, call_id)  # browser reported the drop first
        r = c.post(f"/v1/sessions/{sid}/call", json={"sdp": "A2", "type": "offer", "resume_call_id": call_id},
                   headers=auth)
        assert r.status_code == 200
        assert first.hung == ["resumed"]
        assert c.get(f"/v1/sessions/{sid}", headers=auth).json()["call"]["live"] is True
        assert _pushes(store, sid, "call_resume") == []
        _settle(c)
        assert host.live_calls() == [call_id]


def test_shutdown_hangs_up_every_live_call(store):
    app, host = _fake_app(store)
    with TestClient(app) as c:
        sids = [_new(c) for _ in range(2)]
        for sid, auth in sids:
            c.post(f"/v1/sessions/{sid}/call", json={"sdp": "A", "type": "offer"}, headers=auth)
        assert len(host.live_calls()) == 2
    # lifespan shutdown ran
    assert [s.hung for s in FakeSession.made] == [["server_shutdown"], ["server_shutdown"]]
    assert all(s.connection.disconnects == 1 for s in FakeSession.made)
    assert host.live_calls() == [] and host._live == {}
    for sid, _ in sids:
        assert store.lease(sid) is None


# --- ICE route ---------------------------------------------------------------------------

def test_ice_route_requires_the_session_token_and_is_rate_limited(store):
    app = create_app(store=store, llm=FakeLlm(), spec=SPEC, settings=Settings(), ice=_ice,
                     ice_limiter=SlidingWindowLimiter(2, 60))
    with TestClient(app) as c:
        sid, auth = _new(c)
        assert c.get(f"/v1/sessions/{sid}/ice").status_code == 401
        assert c.get(f"/v1/sessions/{sid}/ice", headers={"Authorization": "Bearer nope"}).status_code == 401
        r = c.get(f"/v1/sessions/{sid}/ice", headers=auth)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        body = r.json()
        assert body["ttl_s"] == 600 and body["ice_servers"][1]["username"] == "u"
        assert c.get(f"/v1/sessions/{sid}/ice", headers=auth).status_code == 200
        assert c.get(f"/v1/sessions/{sid}/ice", headers=auth).status_code == 429


def test_static_ice_fallback_has_no_ttl(monkeypatch):
    from agent.voice import ice

    for k in ("CLOUDFLARE_TURN_KEY_ID", "CLOUDFLARE_TURN_API_TOKEN", "PERSONA_STUN_URLS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PERSONA_TURN_URLS", "turns:turn.example.test:443?transport=tcp")
    monkeypatch.setenv("PERSONA_TURN_USERNAME", "u")
    monkeypatch.setenv("PERSONA_TURN_CREDENTIAL", "c")
    servers, ttl = asyncio.run(ice.resolve_ice())
    assert ttl is None and servers[0]["urls"] == [ice.DEFAULT_STUN] and servers[1]["credential"] == "c"
    assert ice.ice_mode() == "static_turn"
    assert ice.static_ice_servers({"PERSONA_STUN_URLS": "none"}) == []


# --- entrypoint ---------------------------------------------------------------------------

def test_env_report_lists_missing_names_never_values():
    env = {"PERSONA_DATABASE_URL": "postgresql://secret-host/db", "DEEPGRAM_API_KEY": "dg-secret-value",
           "PERSONA_TRACING": "langfuse"}
    r = env_report(env)
    flat = repr(r)
    assert "secret" not in flat and "dg-" not in flat
    assert "database" in r["enabled"] and "voice" in r["enabled"]
    assert r["missing"]["gmail_route"] == ["PERSONA_INTERNAL_SECRET"]
    assert r["missing"]["tracing"] == ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]
    assert "CLOUDFLARE_TURN_KEY_ID" in r["missing"]["turn"][0]
    assert "voice" in env_report({})["missing"]


def test_main_help():
    agent_root = Path(__file__).resolve().parents[1]  # qa:flow runs pytest from the repo root
    out = subprocess.run([sys.executable, "-m", "agent.main", "--help"], capture_output=True, text=True,
                         timeout=60, cwd=agent_root)
    assert out.returncode == 0 and "--port" in out.stdout


# --- real SmallWebRTC, fake vendors -----------------------------------------------------------

def test_real_smallwebrtc_call_with_fake_vendors(store):
    """aiortc client offer -> POST /call -> real SDP answer -> peers connect -> the brain's
    opening (call_started) is pushed as a voice transcript -> client hangs up -> grace ->
    lease released + chat resume; no pipeline left running."""
    pytest.importorskip("aiortc")
    pytest.importorskip("pipecat_flows")
    import httpx
    from aiortc import RTCPeerConnection, RTCSessionDescription

    app = create_app(store=store, llm=FakeLlm(), spec=SPEC, settings=Settings(call_grace_s=0.3),
                     voice=VoiceConfig(fake_vendors=True, max_call_secs=30),
                     ice=lambda: _none_ice())
    host = app.state.call_host
    host._ice = _host_only
    assert host is not None

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            body = (await c.post("/v1/sessions")).json()
            sid, auth = body["id"], {"Authorization": f"Bearer {body['token']}"}
            pc = RTCPeerConnection()
            pc.addTransceiver("audio", direction="sendrecv")
            pc.addTransceiver("video", direction="recvonly")
            got_audio = asyncio.Event()

            @pc.on("track")
            def _on_track(track):
                if track.kind == "audio":
                    got_audio.set()

            await pc.setLocalDescription(await pc.createOffer())
            r = await c.post(f"/v1/sessions/{sid}/call", headers=auth,
                             json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
            assert r.status_code == 201, r.text
            res = r.json()
            assert res["answer"] and "a=candidate" in res["answer"]["sdp"]
            await pc.setRemoteDescription(RTCSessionDescription(sdp=res["answer"]["sdp"], type="answer"))
            for _ in range(100):
                if pc.connectionState == "connected" and _pushes(store, sid, "transcript"):
                    break
                await asyncio.sleep(0.1)
            assert pc.connectionState == "connected"
            assert got_audio.is_set()
            voice_lines = [t for t in _pushes(store, sid, "transcript") if t["channel"] == "voice"]
            assert voice_lines and voice_lines[-1]["role"] == "assistant"
            assert any(p["state"] == "live" for p in _pushes(store, sid, "call_state"))
            assert store.load(sid).active_channel == "voice"

            await pc.close()  # hang up without DELETE: drop -> grace (0.3s) -> end
            for _ in range(100):
                if store.lease(sid) is None and _pushes(store, sid, "call_resume"):
                    break
                await asyncio.sleep(0.1)
            assert _pushes(store, sid, "call_resume"), "hangup must resume in chat"
            assert store.lease(sid) is None
            assert host.live_calls() == []
            await host.close()
            await app.state.calls.close()

    asyncio.run(asyncio.wait_for(go(), 60))


async def _none_ice():
    return [], None


async def _host_only():
    return []  # loopback host candidates only: no STUN/TURN network in tests
