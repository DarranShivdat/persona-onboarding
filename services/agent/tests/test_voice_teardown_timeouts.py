"""Hosted-stack regression: a hanging teardown step must not block hangup forever."""
import asyncio

from agent.voice import handoff
from agent.voice.lifecycle import CallTeardown


def test_hanging_step_times_out_and_later_steps_still_run():
    ran = []

    async def hang():
        await asyncio.Event().wait()

    async def release():
        ran.append("release")

    td = CallTeardown([("transport_cleanup", hang), ("on_ended", release)], step_timeout_s=0.05)
    asyncio.run(asyncio.wait_for(td("hangup"), 2))
    assert ran == ["release"]
    assert td.failures == ["transport_cleanup"]


def test_call_control_end_does_not_wait_for_slow_teardown(monkeypatch):
    monkeypatch.setattr(handoff, "HANGUP_WAIT_S", 0.05)
    finished = []

    class Svc:
        def end_call(self, session_id, call_id, reason):
            return True, None

    async def slow_hangup(reason):
        await asyncio.sleep(0.3)
        finished.append(reason)

    async def run():
        cc = handoff.CallControl.__new__(handoff.CallControl)
        cc.service, cc.ended, cc._pipelines, cc._grace = Svc(), {}, {"c1": slow_hangup}, {}
        loop = asyncio.get_running_loop(); t0 = loop.time()
        released, outcome = await cc.end("s1", "c1", "hangup")
        took = loop.time() - t0
        await asyncio.sleep(0.4)  # teardown keeps running in the background
        return released, took

    released, took = asyncio.run(run())
    assert released is True and took < 0.25
    assert finished == ["hangup"]
