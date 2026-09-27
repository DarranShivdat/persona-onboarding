"""Anthropic client seam.

The adapter only needs `client.messages.create(**kwargs)`. Real calls go through the
official SDK (imported lazily so `qa:fast`/`qa:flow` never need it); tests pass a fake
or `ReplayClient`. Responses may be SDK objects or plain dicts (recorded fixtures), so
all reads go through `field()`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Protocol

API_KEY_ENV = "ANTHROPIC_API_KEY"  # name only; the SDK reads it from the environment


class MessagesAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class LLMClient(Protocol):
    messages: MessagesAPI


def make_client(**kwargs: Any) -> LLMClient:
    """Real Anthropic client. Short timeout + one retry: callers fall back to templates."""
    import anthropic  # runtime extra; never imported by the offline test tiers

    kwargs.setdefault("timeout", 8.0)
    kwargs.setdefault("max_retries", 1)
    return anthropic.Anthropic(**kwargs)


def field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def blocks(resp: Any) -> list[Any]:
    return list(field(resp, "content", None) or [])


def usage_dict(resp: Any) -> dict:
    u = field(resp, "usage", None)
    if u is None:
        return {}
    if isinstance(u, dict):
        return dict(u)
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: getattr(u, k, None) for k in keys if getattr(u, k, None) is not None}


def to_jsonable(resp: Any) -> dict:
    """Serialise a response (SDK object or dict) for recording as a fixture."""
    if isinstance(resp, dict):
        return resp
    if hasattr(resp, "model_dump"):
        return resp.model_dump(mode="json")
    return json.loads(json.dumps(resp, default=lambda o: getattr(o, "__dict__", str(o))))


class _ReplayMessages:
    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("ReplayClient: no recorded response left")
        r = self._responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


class ReplayClient:
    """Returns recorded responses in order; records the requests it was given."""

    def __init__(self, responses: Iterable[Any]):
        self.messages = _ReplayMessages(list(responses))

    @classmethod
    def from_file(cls, path: Path | str) -> "ReplayClient":
        data = json.loads(Path(path).read_text())
        return cls(data["responses"] if isinstance(data, dict) else data)
