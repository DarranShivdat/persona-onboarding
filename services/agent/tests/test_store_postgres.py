"""agent/store against an ephemeral Postgres with the real migrations applied."""
import threading
import time

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")

from agent.brain.state import SessionState, SlotValue  # noqa: E402
from agent.store import (  # noqa: E402
    LeaseHeldError,
    NotFoundError,
    PgStore,
    VersionConflictError,
    state_from_row,
    state_to_columns,
)
from agent.store.testing import ephemeral_dsn, reset  # noqa: E402


@pytest.fixture(scope="module")
def dsn():
    d = ephemeral_dsn()
    if d is None:
        pytest.skip("no ephemeral Postgres (set PERSONA_TEST_DATABASE_URL or install initdb/pg_ctl)")
    return d


@pytest.fixture
def store(dsn):
    reset(dsn)
    s = PgStore(dsn, max_size=4)
    yield s
    s.close()


def _ev(kind, **payload):
    return {"kind": kind, "channel": "text", "payload": payload, "trace_id": "t1"}


def test_codec_roundtrip_is_lossless():
    st = SessionState(
        session_id="00000000-0000-0000-0000-000000000001", version=3, node="need", active_channel="voice",
        slots={"user_name": SlotValue("Ada", "filled", "voice", 0.9, "person_name", 1),
               "agent_name": SlotValue("Butt Bot", "candidate", "text", None, None, 0, True)},
        node_attempts={"need": 2}, deferred_prompts=["gmail"], explained=["need"], call_offer_resolved=True,
    )
    row = {"id": st.session_id, "version": 3, "call_lease_holder": None, **state_to_columns(st)}
    assert state_from_row(row) == st


def test_create_and_load(store):
    st = store.create_session(flow_version=1, token_hash="h")
    loaded = store.load(st.session_id)
    assert loaded.version == 0 and loaded.node == "greet" and loaded.slots == {}
    assert store.token_hash(st.session_id) == "h"


def test_unknown_or_malformed_id_is_not_found(store):
    with pytest.raises(NotFoundError):
        store.load("not-a-uuid")
    with pytest.raises(NotFoundError):
        store.load("00000000-0000-0000-0000-000000000000")


def test_commit_bumps_version_and_appends_events_atomically(store):
    st = store.create_session(flow_version=1, token_hash="h")
    st.node = "call_offer"
    st.slot("agent_name").value, st.slot("agent_name").status = "Nova", "filled"
    st.explained.append("agent_name")
    assert store.commit(st, expected_version=0, events=[_ev("transition", to="call_offer")]) == 1
    loaded = store.load(st.session_id)
    assert loaded.version == 1 and loaded.filled("agent_name") and loaded.explained == ["agent_name"]
    evs = store.events_after(st.session_id)
    assert [(e.kind, e.trace_id) for e in evs] == [("transition", "t1")]


def test_stale_version_conflicts_and_writes_nothing(store):
    st = store.create_session(flow_version=1, token_hash="h")
    store.commit(st, expected_version=0, events=[_ev("a")])
    st.node = "need"
    with pytest.raises(VersionConflictError):
        store.commit(st, expected_version=0, events=[_ev("b")])
    assert store.load(st.session_id).node == "greet"
    assert [e.kind for e in store.events_after(st.session_id)] == ["a"]


def test_two_concurrent_writers_one_wins(store):
    st = store.create_session(flow_version=1, token_hash="h")
    barrier, results = threading.Barrier(2), []

    def writer(node):
        mine = store.load(st.session_id)  # both read version 0
        barrier.wait()
        mine.node = node
        try:
            results.append(("ok", store.commit(mine, expected_version=mine.version, events=[_ev(node)])))
        except VersionConflictError:
            results.append(("conflict", None))

    ts = [threading.Thread(target=writer, args=(n,)) for n in ("user_name", "need")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r[0] for r in results) == ["conflict", "ok"]
    loaded = store.load(st.session_id)
    assert loaded.version == 1
    assert [e.kind for e in store.events_after(st.session_id)] == [loaded.node]


def test_graduation_sets_status_and_graduated_at(store):
    st = store.create_session(flow_version=1, token_hash="h")
    st.node, st.graduated = "graduated", True
    store.commit(st, expected_version=0, events=[])
    assert store.load(st.session_id).graduated
    with store.pool.connection() as c:
        row = c.execute("select status, graduated_at from sessions where id = %s", (st.session_id,)).fetchone()
    assert row["status"] == "graduated" and row["graduated_at"] is not None


def test_call_lease_is_exclusive_until_released_or_expired(store):
    sid = store.create_session(flow_version=1, token_hash="h").session_id
    lease = store.acquire_call_lease(sid, ttl_s=30)
    assert store.lease(sid).call_id == lease.call_id
    with pytest.raises(LeaseHeldError):
        store.acquire_call_lease(sid, ttl_s=30)
    assert store.release_call_lease(sid, lease.call_id, reason="user_hangup")
    assert store.lease(sid) is None
    short = store.acquire_call_lease(sid, ttl_s=0.2)
    time.sleep(0.3)
    assert store.lease(sid) is None
    assert store.acquire_call_lease(sid, ttl_s=30).call_id != short.call_id
    with store.pool.connection() as c:
        calls = c.execute("select end_reason from calls where session_id = %s order by started_at", (sid,)).fetchall()
    assert [r["end_reason"] for r in calls] == ["user_hangup", None, None]


def test_turn_commit_never_touches_the_call_lease(store):
    sid = store.create_session(flow_version=1, token_hash="h").session_id
    lease = store.acquire_call_lease(sid, ttl_s=30)
    st = store.load(sid)
    st.call_lease_holder = None
    store.commit(st, expected_version=st.version, events=[])
    assert store.lease(sid).call_id == lease.call_id


def test_gmail_connection_upsert_in_turn_transaction(store):
    st = store.create_session(flow_version=1, token_hash="h")
    g = {"email": "a@gmail.com", "google_sub": "123", "scopes": ["gmail.modify"]}
    store.commit(st, expected_version=0, events=[], gmail=g)
    st.version = 1
    store.commit(st, expected_version=1, events=[], gmail={**g, "email": "b@gmail.com"})
    with store.pool.connection() as c:
        rows = c.execute("select email from gmail_connections where session_id = %s", (st.session_id,)).fetchall()
    assert [r["email"] for r in rows] == ["b@gmail.com"]
