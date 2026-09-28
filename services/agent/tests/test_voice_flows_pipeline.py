"""VOICE-002 end-to-end offline: a real Pipecat pipeline + Pipecat Flows `FlowManager`.

Stub STT injection: final transcripts enter the user aggregator as appended user
messages; a scripted LLM service answers each one with a `record_slots` function call
built from the same MockLLM fixtures the text tier uses, and speaks the tool result's
`say` line otherwise. Everything else (FlowManager node transitions, function-call
plumbing, context aggregation) is real Pipecat.
"""
import asyncio

import pytest

pytest.importorskip("pipecat_flows")

from pipecat.frames.frames import (  # noqa: E402
    FunctionCallFromLLM,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineParams, PipelineWorker  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection  # noqa: E402
from pipecat.services.llm_service import LLMService  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402
from pipecat_flows import FlowManager  # noqa: E402

from agent.brain.engine import Turn, apply  # noqa: E402
from agent.brain.state import SessionState  # noqa: E402
from agent.voice.flows import LocalBrain, VoiceFlow  # noqa: E402

from test_voice_flows import COMMON, FIXTURES, SPEC, _args, _view  # noqa: E402


def _content(m):
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""


class ScriptedFlowsLLM(LLMService):
    """Offline stand-in for AnthropicLLMService under Flows."""

    def __init__(self):
        super().__init__()
        self.spoken: list[str] = []
        self._n = 0

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return
        msgs = frame.context.get_messages()
        last = msgs[-1] if msgs else {}
        if last.get("role") == "user" and _content(last) in FIXTURES:
            self._n += 1
            await self.run_function_calls([FunctionCallFromLLM(
                function_name="record_slots", tool_call_id=f"call_{self._n}",
                arguments=_args(_content(last)), context=frame.context)])
            return
        task = next((_content(m) for m in reversed(msgs) if m.get("role") in ("developer", "system")), "")
        line = task.split('keeping every fact: "', 1)[1].rsplit('"', 1)[0] if "keeping every fact" in task else ""
        if line:
            self.spoken.append(line)
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(line))
            await self.push_frame(LLMFullResponseEndFrame())


async def _drive(state: SessionState, utterances: list[str]):
    context = LLMContext()
    aggs = LLMContextAggregatorPair(context)
    llm = ScriptedFlowsLLM()
    worker = PipelineWorker(Pipeline([aggs.user(), llm, aggs.assistant()]),
                            params=PipelineParams(), idle_timeout_secs=None)
    brain = LocalBrain(SPEC, state)
    graduated: list[str] = []

    async def on_grad(line):
        graduated.append(line)

    flow = VoiceFlow(SPEC, brain, context=context, on_graduated=on_grad)
    fm = FlowManager(llm=llm, context_aggregator=aggs, worker=worker)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    run = asyncio.create_task(runner.run())
    try:
        await asyncio.sleep(0.3)
        await fm.initialize(await flow.opening())
        for u in utterances:
            if brain.state.graduated:
                break  # the terminal node advertises no record_slots
            before = brain.state.version
            await worker.queue_frame(LLMMessagesAppendFrame([{"role": "user", "content": u}], run_llm=True))
            for _ in range(100):  # wait for the record_slots turn to land in the brain
                await asyncio.sleep(0.02)
                if brain.state.version > before:
                    break
            else:
                raise AssertionError(f"no record_slots turn for {u!r}")
            await asyncio.sleep(0.1)
    finally:
        await worker.cancel()
        await asyncio.wait_for(run, 5)
    return await brain.current(), fm, llm, graduated


def test_flow_manager_pipeline_drives_the_shared_brain():
    st = apply(SPEC, SessionState(session_id="p1"), Turn(channel="text", event="open")).state
    st, fm, llm, graduated = asyncio.run(_drive(st, ["I'm Sam, S-A-M", "uh", "help me triage my inbox every morning",
                                                    "just let me in"]))
    assert st.graduated and st.active_channel == "voice"
    assert st.slot("user_name").value == "Sam" and st.slot("need").status == "filled"
    assert st.deferred_prompts == ["agent_name", "gmail"]
    assert fm.current_node == "graduated"
    assert len(graduated) == 1
    assert any("help with" in s for s in llm.spoken)   # the brain's ask for `need`, phrased by the LLM slot


def test_pipeline_matches_direct_handler_state():
    """Same script through the real FlowManager and through the bare handler."""
    base = apply(SPEC, SessionState(session_id="p2"), Turn(channel="text", event="open")).state
    piped, *_ = asyncio.run(_drive(base, COMMON))

    async def direct():
        brain = LocalBrain(SPEC, base)
        flow = VoiceFlow(SPEC, brain)
        await flow.opening()
        for u in COMMON:
            await flow.handle_record_slots(_args(u), None)
        return await brain.current()

    assert _view(piped) == _view(asyncio.run(direct()))
