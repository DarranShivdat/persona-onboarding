"""Offline LLM clients for the harness tiers (qa:convo), in Anthropic Messages shape.

  MockLLM         : deterministic fake keyed by utterance. Extraction requests (they
                    carry `tools`) answer with a `record_slots` tool_use built from a
                    fixture; phrasing requests answer with empty text, so the Phraser
                    degrades to its templates (deterministic replies).
  RecordingClient : wraps any client (live SDK or MockLLM) and keeps every
                    (kind, utterance, response) so a run can be written as JSONL.
  JsonlReplayClient: replays those JSONL lines in order and fails loudly if the
                    request kind/utterance drifts from what was recorded.

Nothing here touches the network; `make_client` (client.py) is the only live path.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from ..brain.spec import FlowSpec
from .client import to_jsonable
from .schema import TOOL_NAME

_UTTERANCE = re.compile(r"<user_message>(.*?)</user_message>", re.DOTALL)


def request_kind(kwargs: dict) -> str:
    return "extract" if kwargs.get("tools") else "phrase"


def request_utterance(kwargs: dict) -> Optional[str]:
    msgs = kwargs.get("messages") or []
    content = msgs[-1].get("content") if msgs else None
    if not isinstance(content, str):
        return None
    m = _UTTERANCE.search(content)
    return m.group(1) if m else None


def tool_response(spec: FlowSpec, slots: Optional[dict] = None, intents: Iterable[str] = (),
                  confidence: Optional[dict] = None) -> dict:
    """A `record_slots` tool_use message covering every slot (null when absent)."""
    slots = slots or {}
    confidence = confidence or {}
    names = list(spec.slots)
    return {
        "id": "msg_mock", "type": "message", "role": "assistant", "model": "mock",
        "stop_reason": "tool_use",
        "content": [{
            "type": "tool_use", "id": "toolu_mock", "name": TOOL_NAME,
            "input": {
                "slots": {n: slots.get(n) for n in names},
                "confidence": {n: float(confidence.get(n, 0.95 if slots.get(n) else 0.0)) for n in names},
                "intents": list(intents),
            },
        }],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def text_response(text: str) -> dict:
    return {
        "id": "msg_mock", "type": "message", "role": "assistant", "model": "mock",
        "stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


class LLMTimeout(Exception):
    """Stand-in for the SDK's timeout error (the adapter catches any Exception)."""


class FixtureError(BaseException):
    """Missing fixture / replay drift. BaseException on purpose: the adapter swallows
    `Exception` into a templated re-ask, which would hide a broken script."""


ReplayMismatch = FixtureError


class _Messages:
    def __init__(self, create: Callable[..., Any]):
        self.create = create


class MockLLM:
    """Fixture extractor keyed by utterance (exact, whitespace-normalised).

    `extractions[utterance] = {"slots": {...}, "intents": [...], "confidence": {...}}`.
    An unknown utterance raises FixtureError: a script must never silently fall back.
    `faults` is a list consumed one per extraction call: None = answer normally,
    "timeout" = raise LLMTimeout, "no_tool" = reply with text instead of a tool call.
    """

    def __init__(self, spec: FlowSpec, extractions: dict[str, dict],
                 phrase: Optional[Callable[[dict], str]] = None, faults: Iterable[Optional[str]] = ()):
        self.spec = spec
        self.extractions = {_norm(k): v for k, v in extractions.items()}
        self.phrase_fn = phrase or (lambda brief: "")
        self.faults = list(faults)
        self.calls: list[dict] = []
        self.messages = _Messages(self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if request_kind(kwargs) == "phrase":
            brief = kwargs["messages"][-1]["content"]
            return text_response(self.phrase_fn(brief))
        fault = self.faults.pop(0) if self.faults else None
        if fault == "timeout":
            raise LLMTimeout("injected extraction timeout")
        if fault == "no_tool":
            return text_response("Sure!")
        utt = request_utterance(kwargs)
        key = _norm(utt or "")
        if key not in self.extractions:
            raise FixtureError(f"MockLLM: no extraction fixture for utterance {utt!r}")
        x = self.extractions[key]
        return tool_response(self.spec, x.get("slots"), x.get("intents", ()), x.get("confidence"))


class RecordingClient:
    """Pass-through that records every call (errors included) for JSONL replay."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.lines: list[dict] = []
        self.messages = _Messages(self._create)

    def _create(self, **kwargs: Any) -> Any:
        line: dict[str, Any] = {"kind": request_kind(kwargs), "utterance": request_utterance(kwargs)}
        try:
            resp = self.inner.messages.create(**kwargs)
        except Exception as e:
            line["error"] = f"{type(e).__name__}: {e}"
            self.lines.append(line)
            raise
        line["response"] = to_jsonable(resp)
        self.lines.append(line)
        return resp

    def write(self, path: Path | str, header: dict) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"header": header}] + self.lines
        p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


class JsonlReplayClient:
    """Replays a RecordingClient JSONL file; each request must match the next line."""

    def __init__(self, lines: list[dict], header: Optional[dict] = None):
        self.header = header or {}
        self._lines = list(lines)
        self.calls: list[dict] = []
        self.messages = _Messages(self._create)

    @classmethod
    def from_file(cls, path: Path | str) -> "JsonlReplayClient":
        rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
        header = rows[0]["header"] if rows and "header" in rows[0] else {}
        return cls([r for r in rows if "header" not in r], header)

    @property
    def remaining(self) -> int:
        return len(self._lines)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._lines:
            raise ReplayMismatch(f"replay exhausted: unexpected {request_kind(kwargs)} request")
        line = self._lines.pop(0)
        kind, utt = request_kind(kwargs), request_utterance(kwargs)
        if line["kind"] != kind or (kind == "extract" and line.get("utterance") != utt):
            raise ReplayMismatch(f"replay drift: recorded {line['kind']} {line.get('utterance')!r}, "
                                 f"got {kind} {utt!r} (re-record with --record)")
        if "error" in line:
            raise LLMTimeout(line["error"])
        return line["response"]


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()
