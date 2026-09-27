"""Recorded-fixture replay for Gmail/Google HTTP (tests; no network).

Fixture file (tests/fixtures/gmail/<name>.json):
  {"request": {"method": "GET", "url": "https://..."}, "response": {"status": 200, "json": {...}}}
`url` matches on scheme+host+path (query ignored). Fixtures are synthetic but shaped like
real Google responses; they contain no real inbox data or credentials.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

import httpx


class Replay:
    def __init__(self, fixtures_dir: Path, names: Iterable[str] = ()):
        self.dir = Path(fixtures_dir)
        self.routes: dict[tuple[str, str], list[dict]] = {}
        self.calls: list[httpx.Request] = []
        for n in names:
            self.add(n)

    def add(self, name: str) -> "Replay":
        fx = json.loads((self.dir / f"{name}.json").read_text())
        key = (fx["request"]["method"].upper(), _strip(fx["request"]["url"]))
        self.routes.setdefault(key, []).append(fx["response"])
        return self

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        queue = self.routes.get((request.method, _strip(str(request.url))))
        if not queue:
            return httpx.Response(599, json={"error": "no_fixture", "url": _strip(str(request.url))})
        resp = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(resp.get("status", 200), json=resp.get("json", {}))

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.calls]


def _strip(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


def block_external_network(monkeypatch) -> list:
    """Make any non-loopback socket connect fail loudly; returns the list of attempts."""
    attempts: list = []
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if self.family == getattr(socket, "AF_UNIX", None) or host in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, address)
        attempts.append(address)
        raise ConnectionRefusedError(f"network blocked in tests: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded)
    return attempts
