#!/usr/bin/env python3
"""LAT-001 local benchmark: voice-turn handler time over a slow database link.

Starts a throwaway Postgres (agent.store.testing: PERSONA_TEST_DATABASE_URL or local
initdb/pg_ctl), puts a TCP shim in front of it that delays every chunk by RTT/2 each way
(default 65 ms RTT = Fly sjc <-> Supabase us-east-1), and drives voice turns through the
production path: VoiceFlow.handle_record_slots -> ServiceBrain -> SessionService -> PgStore.
The LLM is not involved (extraction args come from the offline fixtures), so the number is
the handler: load -> brain.apply -> commit, plus the store round trips.

    python3 scripts/bench-voice-turn.py [--rtt-ms 65] [--calls 5] [--budget-ms 200]

Exit 1 if the handler p50 is over --budget-ms.
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import statistics
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "agent"), str(ROOT / "services" / "agent" / "tests")]

SCRIPT = ["uh", "I'm Sam, S-A-M", "what do you do with my email?", "help me triage my inbox every morning",
          "actually call me Samantha, S-A-M-A-N-T-H-A", "not now", "just let me in"]


class LatencyShim:
    """127.0.0.1 TCP proxy adding `delay_s` to each direction (order-preserving)."""

    def __init__(self, target: tuple[str, int], delay_s: float):
        self.target, self.delay = target, delay_s
        self.loop = asyncio.new_event_loop()
        self.port = 0
        ready = threading.Event()
        threading.Thread(target=self._serve, args=(ready,), daemon=True).start()
        ready.wait(5)

    def _serve(self, ready: threading.Event) -> None:
        asyncio.set_event_loop(self.loop)

        async def pipe(reader, writer):
            q: asyncio.Queue = asyncio.Queue()

            async def pump():
                while True:
                    due, data = await q.get()
                    if data is None:
                        writer.close()
                        return
                    await asyncio.sleep(max(0.0, due - self.loop.time()))
                    writer.write(data)
                    await writer.drain()

            task = asyncio.ensure_future(pump())
            try:
                while data := await reader.read(65536):
                    q.put_nowait((self.loop.time() + self.delay, data))
            except ConnectionError:
                pass
            q.put_nowait((0, None))
            await task

        async def handle(cr, cw):
            try:
                sr, sw = await asyncio.open_connection(*self.target)
            except OSError:
                cw.close()
                return
            await asyncio.gather(pipe(cr, sw), pipe(sr, cw), return_exceptions=True)

        async def start():
            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            self.port = server.sockets[0].getsockname()[1]
            ready.set()
            await server.serve_forever()

        self.loop.run_until_complete(start())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtt-ms", type=float, default=65.0)
    ap.add_argument("--calls", type=int, default=5)
    ap.add_argument("--budget-ms", type=float, default=200.0)
    args = ap.parse_args()

    from agent.api.llm import FakeLlm
    from agent.api.service import Notifier, SessionService
    from agent.obs.tracing import NoopTracer
    from agent.store import PgStore
    from agent.store.testing import ephemeral_dsn
    from agent.voice.flows import ServiceBrain, VoiceFlow
    from agent.voice.timing import TurnTimer, format_line
    from test_voice_flows import SPEC, _args, _Ctx

    dsn = ephemeral_dsn()
    if dsn is None:
        print("bench: no Postgres (set PERSONA_TEST_DATABASE_URL or install initdb/pg_ctl)", file=sys.stderr)
        return 2
    parts = urlsplit(dsn)
    shim = LatencyShim((parts.hostname or "127.0.0.1", parts.port or 5432), args.rtt_ms / 2000)
    netloc = parts.netloc.rsplit(":", 1)[0] if parts.port else parts.netloc
    slow = urlunsplit(parts._replace(netloc=f"{netloc}:{shim.port}"))

    store = PgStore(slow, min_size=2, max_size=4)
    service = SessionService(store=store, llm=FakeLlm(), spec=SPEC, tracer=NoopTracer(), notifier=Notifier())
    store.ping()  # warm a pooled connection, then time one round trip
    t0 = time.perf_counter()
    store.ping()
    print(f"bench: shim RTT check {1000 * (time.perf_counter() - t0):.0f} ms (target {args.rtt_ms:.0f})")

    records: list[dict] = []

    async def call() -> None:
        sid, _, _ = service.create()
        timer = TurnTimer(emit=records.append)
        ctx = _Ctx()
        flow = VoiceFlow(SPEC, ServiceBrain(service, sid), context=ctx, timer=timer)
        await flow.opening()
        for u in SCRIPT:
            if flow.last.plan.graduate:
                break
            timer.user_stopped()
            ctx.messages.append({"role": "user", "content": u})
            await flow.handle_record_slots(_args(u))
            timer.first_audio()

    try:
        for _ in range(args.calls):
            asyncio.run(call())
    finally:
        store.close()

    for r in records[: len(SCRIPT)]:
        print(format_line(r))
    hs = sorted(r["handler"] for r in records)
    dbs = sorted(r["db"] for r in records)
    p50, p95 = statistics.median(hs), hs[min(len(hs) - 1, int(0.95 * len(hs)))]
    print(f"bench: {len(hs)} voice turns @ {args.rtt_ms:.0f} ms RTT: handler p50 {p50:.0f} ms, p95 {p95:.0f} ms,"
          f" max {hs[-1]:.0f} ms; db p50 {statistics.median(dbs):.0f} ms;"
          f" db round trips/turn {sorted({r['db_calls'] for r in records})}")
    ok = p50 < args.budget_ms
    print(f"bench: {'PASS' if ok else 'FAIL'} handler p50 < {args.budget_ms:.0f} ms")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
