"""Gmail client, OAuth refresh/revoke, value demo and the confirm gate against recorded
fixtures (tests/fixtures/gmail). No DB; zero external network (asserted)."""
from pathlib import Path

import pytest

pytest.importorskip("httpx")
pytest.importorskip("cryptography")

from agent.gmail import (  # noqa: E402
    ConfirmationRequired, ConfirmGate, Confirmed, GmailClient, GmailError, GoogleOAuth, ReconnectRequired, value_demo)
from agent.gmail.demo import FIELDS, SNIPPET_MAX  # noqa: E402
from agent.gmail.testing import Replay, block_external_network  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "gmail"
MSGS = ["messages_list", "message_190a1f0000000001", "message_190a1f0000000002", "message_190a1f0000000003"]
SID = "sess-1"
DRAFT = {"to": "sam@example.com", "subject": "Re: Dinner Thursday?", "body": "Yes — 7 works!"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = block_external_network(monkeypatch)
    yield
    assert attempts == []


def oauth(*names):
    rp = Replay(FX, names)
    return GoogleOAuth("cid.apps.googleusercontent.com", "fixture-secret", http=rp.client()), rp


def gmail(*names):
    rp = Replay(FX, names)
    return GmailClient("ya29.FIXTURE-ACCESS-TOKEN", http=rp.client()), rp


# --- oauth -----------------------------------------------------------------------

def test_refresh_success_returns_in_memory_access_token():
    o, rp = oauth("token_refresh_ok")
    tok = o.refresh("1//refresh")
    assert tok.token == "ya29.FIXTURE-ACCESS-TOKEN" and tok.fresh()
    assert "https://www.googleapis.com/auth/gmail.send" in tok.scopes
    assert "ya29" not in repr(tok) and "fixture-secret" not in repr(o)
    form = rp.calls[0].content.decode()
    assert "grant_type=refresh_token" in form and "refresh_token=1%2F%2Frefresh" in form


def test_invalid_grant_is_reconnect_signal():
    o, _ = oauth("token_invalid_grant")
    with pytest.raises(ReconnectRequired) as e:
        o.refresh("1//dead")
    assert e.value.reason == "invalid_grant" and e.value.code == "gmail_reconnect_required"
    assert "1//dead" not in str(e.value)


def test_transient_refresh_failure_is_not_reconnect():
    o, _ = oauth("token_server_error")
    with pytest.raises(GmailError) as e:
        o.refresh("1//x")
    assert not isinstance(e.value, ReconnectRequired) and e.value.code == "token_refresh_failed"


@pytest.mark.parametrize("fx", ["revoke_ok", "revoke_invalid_token"])
def test_revoke_treats_already_invalid_as_revoked(fx):
    o, rp = oauth(fx)
    assert o.revoke("1//x") is True and rp.paths() == ["POST /revoke"]


# --- value demo -------------------------------------------------------------------

def test_value_demo_returns_safe_metadata_only():
    c, rp = gmail(*MSGS)
    out = value_demo(c)
    assert 1 <= len(out) <= 3
    for m in out:
        assert tuple(m) == FIELDS and len(m["snippet"]) <= SNIPPET_MAX
    assert out[0] == {"id": "190a1f0000000001", "from": "Sam Rivera <sam@example.com>", "subject": "Dinner Thursday?",
                      "snippet": "Hey! Are we still on for Thursday at 7? I can book the place on 5th.",
                      "date": "Wed, 24 Sep 2026 18:02:11 -0700"}
    assert out[1]["snippet"].endswith("…") and "  " not in out[1]["snippet"]
    assert "must-not-leak" not in repr(out)
    # read-only: GETs only, and metadata format (never full bodies)
    assert all(r.method == "GET" for r in rp.calls)
    assert all(r.url.params.get("format") == "metadata" for r in rp.calls[1:])
    assert rp.calls[0].headers["authorization"] == "Bearer ya29.FIXTURE-ACCESS-TOKEN"


def test_value_demo_caps_and_empty_inbox_never_fabricates():
    c, rp = gmail(*MSGS)
    assert len(value_demo(c, limit=1)) == 1 and rp.calls[0].url.params["maxResults"] == "1"
    c, _ = gmail("messages_list_empty")
    assert value_demo(c) == []


def test_rejected_access_token_is_reconnect():
    c, _ = gmail("messages_list_unauthorized")
    with pytest.raises(ReconnectRequired):
        value_demo(c)


# --- confirm gate: no send/draft/modify without an explicit prior confirm ------------

def test_send_blocked_without_confirm_and_no_http():
    c, rp = gmail("send_ok", "draft_ok", "modify_ok")
    with pytest.raises(ConfirmationRequired):
        c.send(**DRAFT, confirmed=None)
    with pytest.raises(ConfirmationRequired):
        c.create_draft(**DRAFT, confirmed=None)
    with pytest.raises(ConfirmationRequired):
        c.modify("190a1f0000000001", remove_labels=("UNREAD",), confirmed=None)
    assert rp.calls == []


def test_forged_grant_rejected():
    c, rp = gmail("send_ok")
    with pytest.raises(ConfirmationRequired):
        c.send(**DRAFT, confirmed=Confirmed(SID, "send", "x" * 64))
    assert rp.calls == []


def test_gate_requires_prior_proposal_for_exact_params():
    gate = ConfirmGate()
    with pytest.raises(ConfirmationRequired, match="missing_confirm_token"):
        gate.confirm(SID, None, "send", DRAFT)
    with pytest.raises(ConfirmationRequired, match="unknown"):
        gate.confirm(SID, "made-up", "send", DRAFT)
    tok = gate.propose(SID, "send", DRAFT)
    with pytest.raises(ConfirmationRequired, match="mismatch"):   # recipient changed after the OK
        gate.confirm(SID, tok, "send", {**DRAFT, "to": "someone-else@example.com"})
    with pytest.raises(ConfirmationRequired, match="unknown_or_used"):  # single use, even after mismatch
        gate.confirm(SID, tok, "send", DRAFT)
    tok = gate.propose(SID, "send", DRAFT)
    with pytest.raises(ConfirmationRequired, match="mismatch"):   # other session
        gate.confirm("sess-2", tok, "send", DRAFT)
    tok = gate.propose(SID, "draft", DRAFT)
    with pytest.raises(ConfirmationRequired, match="mismatch"):   # draft OK is not a send OK
        gate.confirm(SID, tok, "send", DRAFT)


def test_gate_expiry():
    gate = ConfirmGate(ttl_s=-1)
    tok = gate.propose(SID, "send", DRAFT)
    with pytest.raises(ConfirmationRequired, match="expired"):
        gate.confirm(SID, tok, "send", DRAFT)


def test_confirmed_send_goes_through_once():
    gate = ConfirmGate()
    c, rp = gmail("send_ok")
    grant = gate.confirm(SID, gate.propose(SID, "send", DRAFT), "send", DRAFT)
    with pytest.raises(ConfirmationRequired, match="mismatch"):
        c.send(**{**DRAFT, "body": "changed"}, confirmed=grant)
    assert rp.calls == []
    assert c.send(**DRAFT, confirmed=grant)["labelIds"] == ["SENT"]
    assert rp.paths() == ["POST /gmail/v1/users/me/messages/send"]
    with pytest.raises(ConfirmationRequired, match="already_used"):
        c.send(**DRAFT, confirmed=grant)
    assert len(rp.calls) == 1


def test_confirmed_draft_and_modify():
    gate = ConfirmGate()
    c, rp = gmail("draft_ok", "modify_ok")
    assert c.create_draft(**DRAFT, confirmed=gate.confirm(SID, gate.propose(SID, "draft", DRAFT), "draft", DRAFT))["id"]
    mod = {"id": "190a1f0000000001", "add": [], "remove": ["UNREAD"]}
    grant = gate.confirm(SID, gate.propose(SID, "modify", mod), "modify", mod)
    assert c.modify("190a1f0000000001", remove_labels=("UNREAD",), confirmed=grant)["id"] == "190a1f0000000001"
    assert rp.paths() == ["POST /gmail/v1/users/me/drafts", "POST /gmail/v1/users/me/messages/190a1f0000000001/modify"]
