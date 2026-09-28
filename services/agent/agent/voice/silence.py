"""Per-node silence floor: never dead air on a call (invariant 7, EC-10).

After the bot finishes a line, if the caller says nothing: ≈7s a spoken nudge that
re-asks the node's question, ≈15s a second nudge with an example, ≈23s offer to continue
in the chat and end the call politely (reason `silence_timeout` -> chat resume). Any
caller speech or transcript resets the ladder; nothing fires while either side is
speaking. The lines are templated per node (never LLM output) and never move state.

`SilenceFloor` is pure (injected clock); `run_silence_floor` polls it with an injected
sleep; `SilenceObserver` feeds it from Pipecat frames.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Optional

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from ..brain.spec import FlowSpec
from ..llm import templates as T

NUDGE, EXAMPLE, PARK = "nudge", "example", "park"

STILL_THERE = "Are you still there?"
TAKE_YOUR_TIME = "Take your time, I'm here whenever you're ready."
EXAMPLES = {
    "user_name": "You can just say your first name, like \"I'm Sam.\"",
    "need": "For example, you could say \"help me stay on top of my inbox\", or \"keep my calendar in check.\"",
    "gmail": "Just tap Connect Gmail on your screen, or say \"later\" and we'll do it afterwards.",
}
GENERIC_EXAMPLE = "You can say something like \"let's keep going\" whenever you're ready."
PARK_LINE = ("No worries, sounds like now isn't a great time. Everything so far is saved, "
             "so you can pick up by typing in the chat. Bye for now!")


@dataclass(frozen=True)
class SilencePolicy:
    nudge_s: float = 7.0
    example_s: float = 15.0
    park_s: float = 23.0
    poll_s: float = 0.25

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "SilencePolicy":
        env = os.environ if env is None else env
        return cls(
            nudge_s=float(env.get("PERSONA_SILENCE_NUDGE_S") or cls.nudge_s),
            example_s=float(env.get("PERSONA_SILENCE_EXAMPLE_S") or cls.example_s),
            park_s=float(env.get("PERSONA_SILENCE_PARK_S") or cls.park_s),
        )


def silence_line(spec: FlowSpec, node: Optional[str], action: str) -> str:
    """Spoken line for `action` at `node` (unknown node -> generic line)."""
    if action == PARK:
        return PARK_LINE
    n = spec.nodes.get(node or "", {})
    slot = n.get("slot") if n.get("kind") == "collect" else None
    if action == NUDGE:
        if slot and slot in T.ASK:
            return f"{STILL_THERE} {T.ask_line(slot, 'voice')}"
        return f"{STILL_THERE} {TAKE_YOUR_TIME}"
    return EXAMPLES.get(slot or "", GENERIC_EXAMPLE)


class SilenceFloor:
    """Silence ladder state. Time counts from the end of the bot's last (non-nudge) line
    or the caller's last activity; nudges don't restart it, so the ladder is absolute."""

    def __init__(self, policy: Optional[SilencePolicy] = None, *, clock: Callable[[], float] = time.monotonic):
        self.policy = policy or SilencePolicy()
        self._clock = clock
        self.armed = False
        self.stage = 0                      # 0 none, 1 nudged, 2 example given, 3 parked
        self._since = clock()
        self._bot = self._user = False

    def start(self) -> None:
        self.armed, self.stage, self._since = True, 0, self._clock()

    def stop(self) -> None:
        self.armed = False

    def user_activity(self) -> None:
        self.stage, self._since = 0, self._clock()

    def user_speaking(self, speaking: bool) -> None:
        self._user = speaking
        self.user_activity()

    def bot_speaking(self, speaking: bool) -> None:
        if self._bot and not speaking and self.stage == 0:
            self._since = self._clock()     # the question just ended: the caller's turn starts now
        self._bot = speaking

    def due(self) -> Optional[str]:
        """The action due now, if any (advances the ladder)."""
        if not self.armed or self._bot or self._user or self.stage >= 3:
            return None
        quiet = self._clock() - self._since
        p = self.policy
        for stage, (at, action) in enumerate(((p.nudge_s, NUDGE), (p.example_s, EXAMPLE), (p.park_s, PARK))):
            if self.stage == stage and quiet >= at:
                self.stage = stage + 1
                return action
        return None


async def run_silence_floor(floor: SilenceFloor, *, speak: Callable[[str], Awaitable[None]],
                            park: Callable[[str], Awaitable[None]], line: Callable[[str], str],
                            sleep: Callable[[float], Awaitable[None]]) -> None:
    """Poll the floor; speak nudges; on park, hand the goodbye to `park` and stop."""
    while True:
        await sleep(floor.policy.poll_s)
        action = floor.due()
        if action == PARK:
            floor.stop()
            await park(line(PARK))
            return
        if action:
            await speak(line(action))


class SilenceObserver(BaseObserver):
    """Feeds `SilenceFloor` from pipeline frames (observed at every hop; the floor's
    inputs are idempotent)."""

    def __init__(self, floor: SilenceFloor):
        super().__init__()
        self.floor = floor

    async def on_push_frame(self, data: FramePushed) -> None:
        f = data.frame
        if isinstance(f, BotStartedSpeakingFrame):
            self.floor.bot_speaking(True)
        elif isinstance(f, BotStoppedSpeakingFrame):
            self.floor.bot_speaking(False)
        elif isinstance(f, UserStartedSpeakingFrame):
            self.floor.user_speaking(True)
        elif isinstance(f, UserStoppedSpeakingFrame):
            self.floor.user_speaking(False)
        elif isinstance(f, (TranscriptionFrame, InterimTranscriptionFrame)):
            self.floor.user_activity()
