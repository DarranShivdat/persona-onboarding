"""GRAD-001: home tap-to-edit route + post-graduation text turns (ephemeral Postgres, FakeLlm)."""
import pytest

from agent.brain.engine import Extraction

from test_api_sessions import SECRET, client, dsn, llm, new_session, store, turn  # noqa: F401 - fixtures


def graduated(client, llm):
    sid, auth, _ = new_session(client)
    for t in ("Nova", "no thanks", "Ada", "Triage my inbox every morning"):
        assert turn(client, sid, auth, t).status_code == 200
    llm.push(Extraction(intents=["insist_graduate"]))
    st = turn(client, sid, auth, "just let me in").json()["state"]
    assert st["graduated"] and st["deferred_prompts"] == ["gmail"]
    return sid, auth


def edit(client, sid, auth, slot, value):
    return client.post(f"/v1/sessions/{sid}/edit", json={"slot": slot, "value": value}, headers=auth)


def pushes(store, sid):
    return [e.payload for e in store.events_after(sid, kinds=["ui_push"], limit=1000)]


def test_edit_changes_slot_through_validator_and_pushes(client, llm, store):
    sid, auth = graduated(client, llm)
    r = edit(client, sid, auth, "user_name", "Darran")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"]["slots"]["user_name"]["value"] == "Darran" and body["state"]["graduated"]
    assert body["reply"] == "Thanks, Darran it is."
    last = pushes(store, sid)[-2:]
    assert [p["type"] for p in last] == ["transcript", "state"]
    assert last[0]["data"]["role"] == "assistant"
    assert [p["type"] for p in pushes(store, sid)].count("graduate") == 1  # never re-graduates


def test_invalid_edit_is_422_and_commits_nothing(client, llm, store):
    sid, auth = graduated(client, llm)
    before = client.get(f"/v1/sessions/{sid}", headers=auth).json()
    r = edit(client, sid, auth, "user_name", "12345")
    assert r.status_code == 422 and r.json()["reason"] == "charset" and r.json()["message"]
    after = client.get(f"/v1/sessions/{sid}", headers=auth).json()
    assert after == before


def test_edit_rejects_gmail_unauthorized_and_pre_graduation(client, llm):
    sid, auth = graduated(client, llm)
    assert edit(client, sid, auth, "gmail", "a@b.com").status_code == 422
    assert client.post(f"/v1/sessions/{sid}/edit", json={"slot": "need", "value": "x y z"}).status_code == 401
    sid2, auth2, _ = new_session(client)
    assert edit(client, sid2, auth2, "agent_name", "Nova").status_code == 409


def test_home_text_turn_gets_scoped_reply_not_graduation_repeat(client, llm, store):
    sid, auth = graduated(client, llm)
    r = turn(client, sid, auth, "can you check my inbox?")
    assert r.status_code == 200
    reply = r.json()["reply"]
    assert reply and "all set" not in reply and "doesn't carry out tasks" in reply
    r = turn(client, sid, auth, "rename yourself Juno")
    assert r.json()["state"]["slots"]["agent_name"]["value"] == "Juno"
    texts = [p["data"]["text"] for p in pushes(store, sid) if p["type"] == "transcript"]
    assert texts[-2:] == ["rename yourself Juno", "Okay, Juno it is."]


def test_gmail_oauth_after_graduation_connects(client, llm):
    sid, auth = graduated(client, llm)
    r = client.post(f"/v1/sessions/{sid}/gmail", json={"email": "ada@gmail.com", "google_sub": "g-1"},
                    headers={"X-Persona-Internal-Secret": SECRET})
    st = r.json()["state"]
    assert st["slots"]["gmail"]["status"] == "filled" and st["deferred_prompts"] == []
