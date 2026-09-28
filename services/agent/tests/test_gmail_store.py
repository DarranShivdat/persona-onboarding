"""Encrypted refresh-token store + GmailService + API wiring, against an ephemeral Postgres
and recorded Google fixtures (tests/fixtures/gmail). Zero external network (asserted)."""
from pathlib import Path

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("cryptography")

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent.api.app import Settings, create_app  # noqa: E402
from agent.api.llm import FakeLlm  # noqa: E402
from agent.gmail import GmailService, GoogleOAuth, ReconnectRequired, TokenCipher, generate_key  # noqa: E402
from agent.gmail.testing import Replay, block_external_network  # noqa: E402
from agent.store import PgStore  # noqa: E402
from agent.store.testing import ephemeral_dsn, reset  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "gmail"
MSGS = ["messages_list", "message_190a1f0000000001", "message_190a1f0000000002", "message_190a1f0000000003"]
SECRET = "test-internal-secret"
REFRESH = "1//0g-FIXTURE-REFRESH-TOKEN-abc123"
SCOPES = ["openid", "email", "https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.send"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = block_external_network(monkeypatch)
    yield
    assert attempts == []


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
def cipher():
    return TokenCipher(generate_key())


def make(store, cipher, *fixtures):
    rp = Replay(FX, fixtures)
    http = rp.client()
    svc = GmailService(store=store, cipher=cipher, oauth=GoogleOAuth("cid", "fixture-secret", http=http), http=http)
    app = create_app(store=store, llm=FakeLlm(), gmail=svc, settings=Settings(internal_secret=SECRET))
    return TestClient(app), svc, rp


def session(c):
    r = c.post("/v1/sessions").json()
    return r["id"], {"Authorization": f"Bearer {r['token']}"}


def connect(c, sid, **extra):
    body = {"email": "ada@gmail.com", "google_sub": "g-1", "scopes": SCOPES, **extra}
    return c.post(f"/v1/sessions/{sid}/gmail", json=body, headers={"X-Persona-Internal-Secret": SECRET})


def raw_db(dsn, sql, *args):
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql, args).fetchall()


# --- store: encrypt at rest -------------------------------------------------------

def test_post_stores_encrypted_refresh_token_only(store, cipher, dsn):
    c, _, _ = make(store, cipher)
    sid, _ = session(c)
    r = connect(c, sid, refresh_token=REFRESH)
    assert r.status_code == 200, r.text
    assert r.json()["gmail"] == {"token_status": "stored"}
    assert r.json()["state"]["slots"]["gmail"]["status"] == "filled"
    assert REFRESH not in r.text
    conn = store.gmail_connection(sid)
    assert conn.token_status == "stored" and conn.token_key_id == cipher.key_id and conn.scopes == SCOPES
    assert REFRESH.encode() not in conn.refresh_token_enc
    assert cipher.decrypt(conn.refresh_token_enc, aad=sid) == REFRESH  # roundtrip through Postgres
    assert "refresh_token_enc" not in repr(conn)
    # token never lands in the event log (ui pushes, transcript, brain events)
    dump = raw_db(dsn, "select payload::text from session_events where session_id = %s", sid)
    assert all(REFRESH not in row[0] for row in dump)


def test_missing_refresh_token_is_accepted_with_status(store, cipher):
    c, _, _ = make(store, cipher)
    sid, _ = session(c)
    r = connect(c, sid)  # FE-003 still stubs the refresh token
    assert r.status_code == 200 and r.json()["gmail"] == {"token_status": "missing"}
    assert store.gmail_connection(sid).refresh_token_enc is None


def test_no_key_configured_never_stores_plaintext(store):
    c, _, _ = make(store, None)
    sid, _ = session(c)
    r = connect(c, sid, refresh_token=REFRESH)
    assert r.json()["gmail"] == {"token_status": "key_unavailable"}
    assert store.gmail_connection(sid).refresh_token_enc is None


def test_reconnect_without_token_keeps_same_account_token_only(store, cipher):
    c, _, _ = make(store, cipher)
    sid, _ = session(c)
    connect(c, sid, refresh_token=REFRESH)
    assert connect(c, sid).json()["gmail"]["token_status"] == "stored"
    assert cipher.decrypt(store.gmail_connection(sid).refresh_token_enc, aad=sid) == REFRESH
    r = connect(c, sid, google_sub="g-other", email="other@gmail.com")
    assert r.json()["gmail"]["token_status"] == "missing"
    assert store.gmail_connection(sid).refresh_token_enc is None


# --- refresh on use ------------------------------------------------------------------

def test_refresh_success_then_cached_in_memory(store, cipher):
    c, svc, rp = make(store, cipher, "token_refresh_ok")
    sid, _ = session(c)
    connect(c, sid, refresh_token=REFRESH)
    assert svc.access_token(sid) == "ya29.FIXTURE-ACCESS-TOKEN"
    assert svc.access_token(sid) == "ya29.FIXTURE-ACCESS-TOKEN"
    assert rp.paths() == ["POST /token"]  # second call served from memory
    assert f"refresh_token={REFRESH.replace('/', '%2F')}" in rp.calls[0].content.decode()
    conn = store.gmail_connection(sid)
    assert conn.last_refresh_at is not None and conn.token_status == "stored"


def test_invalid_grant_marks_connection_and_api_returns_reconnect(store, cipher):
    c, svc, _ = make(store, cipher, "token_invalid_grant")
    sid, auth = session(c)
    connect(c, sid, refresh_token=REFRESH)
    r = c.get(f"/v1/sessions/{sid}/gmail/demo", headers=auth)
    assert r.status_code == 409 and r.json() == {"error": "gmail_reconnect_required", "reason": "invalid_grant"}
    conn = store.gmail_connection(sid)
    assert conn.token_status == "invalid" and conn.last_error == "invalid_grant" and conn.refresh_token_enc is None
    with pytest.raises(ReconnectRequired) as e:  # no second Google call; stays reconnect
        svc.access_token(sid)
    assert e.value.reason == "invalid"
    # reconnecting with a fresh token restores it
    assert connect(c, sid, refresh_token="1//new").json()["gmail"]["token_status"] == "stored"


def test_undecryptable_token_is_reconnect(store, cipher):
    c, _, _ = make(store, cipher)
    sid, _ = session(c)
    connect(c, sid, refresh_token=REFRESH)
    other = GmailService(store=store, cipher=TokenCipher(generate_key()),
                         oauth=GoogleOAuth("cid", "s", http=Replay(FX).client()))
    with pytest.raises(ReconnectRequired) as e:
        other.access_token(sid)
    assert e.value.reason == "undecryptable"


def test_demo_endpoint_value_demo_shape(store, cipher):
    c, _, rp = make(store, cipher, "token_refresh_ok", *MSGS)
    sid, auth = session(c)
    connect(c, sid, refresh_token=REFRESH)
    assert c.get(f"/v1/sessions/{sid}/gmail/demo").status_code == 401
    r = c.get(f"/v1/sessions/{sid}/gmail/demo", headers=auth)
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert 1 <= len(msgs) <= 3 and all(set(m) == {"id", "from", "subject", "snippet", "date"} for m in msgs)
    assert "ya29" not in r.text and REFRESH not in r.text
    assert all(req.method == "GET" for req in rp.calls[1:])


def test_demo_without_connection_or_token_is_reconnect(store, cipher):
    c, _, rp = make(store, cipher)
    sid, auth = session(c)
    assert c.get(f"/v1/sessions/{sid}/gmail/demo", headers=auth).json()["reason"] == "not_connected"
    connect(c, sid)
    assert c.get(f"/v1/sessions/{sid}/gmail/demo", headers=auth).json()["reason"] == "missing"
    assert rp.calls == []


# --- revoke / disconnect ------------------------------------------------------------

def test_disconnect_revokes_clears_and_is_idempotent(store, cipher):
    c, _, rp = make(store, cipher, "revoke_ok")
    sid, auth = session(c)
    connect(c, sid, refresh_token=REFRESH)
    h = {"X-Persona-Internal-Secret": SECRET}
    assert c.delete(f"/v1/sessions/{sid}/gmail").status_code == 401
    r = c.delete(f"/v1/sessions/{sid}/gmail", headers=h)
    assert r.status_code == 200 and r.json() == {"revoked": True, "token_status": "revoked"}
    assert rp.paths() == ["POST /revoke"] and f"token={REFRESH.replace('/', '%2F')}" in rp.calls[0].content.decode()
    conn = store.gmail_connection(sid)
    assert conn.refresh_token_enc is None and conn.revoked_at is not None and conn.token_status == "revoked"
    first = conn.revoked_at
    r = c.delete(f"/v1/sessions/{sid}/gmail", headers=h)
    assert r.status_code == 200 and r.json()["revoked"] is False
    assert store.gmail_connection(sid).revoked_at == first and len(rp.calls) == 1
    assert c.get(f"/v1/sessions/{sid}/gmail/demo", headers=auth).json()["reason"] == "revoked"
    # revoked token status survives a stale invalid_grant mark
    store.mark_gmail_invalid(sid, error="invalid_grant")
    assert store.gmail_connection(sid).token_status == "revoked"


def test_disconnect_clears_tokens_even_if_google_unreachable(store, cipher):
    c, _, rp = make(store, cipher)  # no revoke fixture -> 599 from replay
    sid, _ = session(c)
    connect(c, sid, refresh_token=REFRESH)
    r = c.delete(f"/v1/sessions/{sid}/gmail", headers={"X-Persona-Internal-Secret": SECRET})
    assert r.json()["revoked"] is True and store.gmail_connection(sid).refresh_token_enc is None


def test_disconnect_unknown_or_unconnected_session(store, cipher):
    c, _, _ = make(store, cipher)
    h = {"X-Persona-Internal-Secret": SECRET}
    assert c.delete("/v1/sessions/00000000-0000-4000-8000-000000000000/gmail", headers=h).status_code == 404
    sid, _ = session(c)
    assert c.delete(f"/v1/sessions/{sid}/gmail", headers=h).json()["revoked"] is False


def test_gmail_post_still_fails_closed_without_secret(store, cipher):
    rp = Replay(FX)
    svc = GmailService(store=store, cipher=cipher, oauth=None, http=rp.client())
    with TestClient(create_app(store=store, llm=FakeLlm(), gmail=svc, settings=Settings())) as c:
        sid, _ = session(c)
        assert connect(c, sid, refresh_token=REFRESH).status_code == 503
        assert c.delete(f"/v1/sessions/{sid}/gmail", headers={"X-Persona-Internal-Secret": "x"}).status_code == 503
    assert store.gmail_connection(sid) is None
