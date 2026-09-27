"""VOICE-002: one session row for text and voice (invariant 6).

Start in chat via SessionService (the text turn path), continue on the call via the
Flows handler over ServiceBrain, then type again: every turn lands on the same row and
the result equals the all-text run of the same script. Needs an ephemeral Postgres.
"""
import asyncio

import pytest

pytest.importorskip("pipecat_flows")
pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")

from agent.api.llm import FakeLlm  # noqa: E402
from agent.api.service import Notifier, SessionService  # noqa: E402
from agent.brain.engine import Extraction  # noqa: E402
from agent.llm.extract import parse  # noqa: E402
from agent.obs.tracing import NoopTracer  # noqa: E402
from agent.store import PgStore  # noqa: E402
from agent.store.testing import ephemeral_dsn, reset  # noqa: E402
from agent.voice.flows import ServiceBrain, VoiceFlow  # noqa: E402

from test_voice_flows import COMMON, SPEC, _args, _Ctx, _view  # noqa: E402


@pytest.fixture(scope="module")
def dsn():
    d = ephemeral_dsn()
    if d is None:
        pytest.skip("no ephemeral Postgres (set PERSONA_TEST_DATABASE_URL or install initdb/pg_ctl)")
    return d


@pytest.fixture
def service(dsn):
    reset(dsn)
    store = PgStore(dsn, max_size=4)
    yield SessionService(store=store, llm=FakeLlm(), spec=SPEC, tracer=NoopTracer(), notifier=Notifier())
    store.close()


def _x(utterance) -> Extraction:
    return parse(SPEC, _args(utterance))


def test_chat_then_call_then_chat_share_one_session(service):
    call_part, typed_after = COMMON[:5], COMMON[5:]

    # all-text reference run
    ref, _, _ = service.create()
    service.llm.push(_x("Call it Nova"), _x("I'd rather type"), *[_x(u) for u in COMMON])
    for u in ["Call it Nova", "I'd rather type", *COMMON]:
        service.text_turn(ref, u)

    # chat -> call -> chat on one session
    sid, _, _ = service.create()
    service.llm.push(_x("Call it Nova"), _x("sure, let's talk"))
    service.text_turn(sid, "Call it Nova")
    service.text_turn(sid, "sure, let's talk")

    async def call():
        ctx = _Ctx()  # the user aggregator appends each final transcript here
        flow = VoiceFlow(SPEC, ServiceBrain(service, sid), context=ctx)
        await flow.opening()
        for u in call_part:
            ctx.messages.append({"role": "user", "content": u})
            await flow.handle_record_slots(_args(u), None)

    asyncio.run(call())
    mid = service.get_snapshot(sid)
    assert mid["active_channel"] == "voice" and mid["slots"]["user_name"]["source"] == "voice"
    assert mid["slots"]["need"]["status"] == "filled"

    service.llm.push(*[_x(u) for u in typed_after])
    for u in typed_after:
        service.text_turn(sid, u)

    got, want = service.store.load(sid), service.store.load(ref)
    assert _view(got) == _view(want)
    # events from both channels landed on the same row, in order
    utts = [(e.channel, e.payload["text"]) for e in service.store.events_after(sid, kinds=["user_utterance"])]
    assert utts == [("text", "Call it Nova"), ("text", "sure, let's talk"),
                    *[("voice", u) for u in call_part], *[("text", u) for u in typed_after]]
