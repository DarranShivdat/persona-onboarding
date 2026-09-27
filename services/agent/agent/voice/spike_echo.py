"""INFRA-001 spike: minimal SmallWebRTC echo/greeting bot for the hosting decision.

Not the product voice pipeline (that is VOICE-001). No vendor keys needed: the bot
plays a short greeting tone when the browser connects, then echoes the caller's
microphone back. It exists to prove the *media path* (ICE/STUN/TURN) from a hosted,
long-lived process to a browser on a home network and on a UDP-blocked network.

Run locally:

    python -m agent.voice.spike_echo --host 127.0.0.1 --port 7860
    open http://127.0.0.1:7860/            # ?relay=1 forces TURN-only on the client

ICE config (env var NAMES only; see infra/voice-spike/README.md):

    PERSONA_STUN_URLS           comma-separated, default stun:stun.l.google.com:19302
    PERSONA_TURN_URLS           comma-separated turn:/turns: URLs (static creds)
    PERSONA_TURN_USERNAME       static TURN username
    PERSONA_TURN_CREDENTIAL     static TURN credential
    CLOUDFLARE_TURN_KEY_ID      Cloudflare Realtime TURN key id (short-lived creds)
    CLOUDFLARE_TURN_API_TOKEN   Cloudflare Realtime TURN key API token
    PERSONA_SPIKE_TOKEN         optional shared secret required on POST /api/offer

The same ICE server list is handed to BOTH peers: the browser (GET /api/ice) and
aiortc on the server. On hosts without inbound UDP (Fly, Railway) the server side
must allocate a TURN relay too, otherwise the only reachable candidate is a relay on
one side talking to an unreachable host candidate on the other.

Patterns adapted from Darran's Penciled voice-agent (bot.py): idempotent per-call
teardown on disconnect, and the per-IP sliding-window rate limiter on session starts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import secrets
import struct
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

SAMPLE_RATE = 16000
MAX_CALL_SECS = float(os.environ.get("PERSONA_SPIKE_MAX_CALL_SECS", "300"))

# ICE helpers moved to agent.voice.ice (VOICE-001); re-exported for the spike.
from .ice import resolve_ice_servers, static_ice_servers, to_aiortc  # noqa: E402,F401


# --- Greeting + echo ---------------------------------------------------------


def greeting_tone(secs: float = 0.6, freqs: tuple[int, ...] = (523, 659, 784)) -> bytes:
    """A short three-note chime (16-bit mono PCM) so the caller hears the bot first."""
    per_note = int(SAMPLE_RATE * secs / len(freqs))
    samples: list[int] = []
    for f in freqs:
        for i in range(per_note):
            env = min(1.0, i / 400, (per_note - i) / 400)
            samples.append(int(9000 * env * math.sin(2 * math.pi * f * i / SAMPLE_RATE)))
    return struct.pack(f"<{len(samples)}h", *samples)


@dataclass
class CallStats:
    pc_id: str = ""
    started: float = field(default_factory=time.time)
    in_frames: int = 0
    out_frames: int = 0
    connected: bool = False
    ended_reason: str | None = None
    local_candidates: list[str] = field(default_factory=list)


class Echo(FrameProcessor):
    """Turns caller audio into bot audio (loopback) and counts both directions."""

    def __init__(self, stats: CallStats):
        super().__init__()
        self._stats = stats

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self._stats.in_frames += 1
            self._stats.out_frames += 1
            await self.push_frame(
                OutputAudioRawFrame(
                    audio=frame.audio,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
            )
        else:
            await self.push_frame(frame, direction)


# --- Per-call bot --------------------------------------------------------------

CALLS: dict[str, CallStats] = {}


def _candidate_types(connection: SmallWebRTCConnection) -> list[str]:
    """Candidate types in our SDP answer (host/srflx/relay) — shows what ICE had."""
    answer = connection.get_answer() or {}
    types = []
    for line in answer.get("sdp", "").splitlines():
        if line.startswith("a=candidate:") and " typ " in line:
            types.append(line.split(" typ ", 1)[1].split()[0])
    return sorted(set(types))


async def run_bot(connection: SmallWebRTCConnection) -> None:
    stats = CALLS.setdefault(connection.pc_id, CallStats(pc_id=connection.pc_id))
    stats.local_candidates = _candidate_types(connection)
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_sample_rate=SAMPLE_RATE,
        ),
    )
    pipeline = Pipeline([transport.input(), Echo(stats), transport.output()])
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=SAMPLE_RATE
        ),
    )

    # Idempotent per-call teardown (Penciled bot.py pattern): cancel the worker on
    # the first ending so runner.run() returns and the peer is closed, whichever of
    # disconnect / max-duration / pipeline end fires first.
    torn_down = False

    async def teardown(reason: str) -> None:
        nonlocal torn_down
        if torn_down:
            return
        torn_down = True
        stats.ended_reason = reason
        logger.info(f"[{stats.pc_id}] teardown: {reason}")
        try:
            await worker.cancel()
        except Exception as e:  # noqa: BLE001 - best-effort cleanup
            logger.warning(f"worker.cancel during teardown failed: {e}")

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):  # noqa: ANN001
        stats.connected = True
        logger.info(f"[{stats.pc_id}] client connected; playing greeting")
        await worker.queue_frame(
            OutputAudioRawFrame(audio=greeting_tone(), sample_rate=SAMPLE_RATE, num_channels=1)
        )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):  # noqa: ANN001
        await teardown("client disconnected")

    async def max_duration() -> None:
        await asyncio.sleep(MAX_CALL_SECS)
        await worker.queue_frame(EndFrame())
        await asyncio.sleep(2)
        await teardown("max call duration")

    limiter = asyncio.create_task(max_duration())
    try:
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        await runner.run()
    finally:
        limiter.cancel()
        if not stats.ended_reason:
            stats.ended_reason = "pipeline ended"
        try:
            await connection.disconnect()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"connection.disconnect failed: {e}")


# --- HTTP app ------------------------------------------------------------------


class _RateLimiter:
    """Per-key sliding-window counter (in-memory, single-process). From Penciled bot.py."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_secs: float = 60.0) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > window_secs:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_app() -> FastAPI:
    app = FastAPI(title="persona voice spike (echo)")
    handler_ref: dict[str, SmallWebRTCRequestHandler] = {}
    limiter = _RateLimiter()
    background: set[asyncio.Task] = set()

    async def handler() -> SmallWebRTCRequestHandler:
        # A new handler per ICE refresh keeps short-lived TURN creds valid for the
        # server leg too (Cloudflare creds carry a TTL).
        servers = to_aiortc(await resolve_ice_servers())
        h = handler_ref.get("h")
        if h is None:
            h = handler_ref["h"] = SmallWebRTCRequestHandler(ice_servers=servers)
        else:
            h.update_ice_servers(servers)
        return h

    @app.get("/health")
    async def health():
        return {"ok": True, "active_calls": sum(1 for c in CALLS.values() if not c.ended_reason)}

    @app.get("/api/ice")
    async def ice():
        return {"iceServers": await resolve_ice_servers()}

    @app.post("/api/offer")
    async def offer(request: Request):
        token = os.environ.get("PERSONA_SPIKE_TOKEN")
        if token and not secrets.compare_digest(request.headers.get("x-spike-token", ""), token):
            raise HTTPException(status_code=401, detail="bad token")
        if not limiter.allow(_client_ip(request), limit=20):
            raise HTTPException(status_code=429, detail="rate limited")
        body = await request.json()
        req = SmallWebRTCRequest(
            sdp=body["sdp"], type=body["type"], pc_id=body.get("pc_id"),
            restart_pc=body.get("restart_pc"),
        )

        async def on_connection(connection: SmallWebRTCConnection) -> None:
            t = asyncio.create_task(run_bot(connection))
            background.add(t)
            t.add_done_callback(background.discard)

        return await (await handler()).handle_web_request(req, on_connection)

    @app.get("/api/calls")
    async def call_stats(pc_id: str):  # query param: pipecat pc_ids contain '#'
        stats = CALLS.get(pc_id)
        if not stats:
            raise HTTPException(status_code=404, detail="unknown call")
        return stats.__dict__

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return CLIENT_HTML

    return app


# Minimal client: fresh RTCPeerConnection per call (Penciled phone.html lesson: never
# reuse a closed client), non-trickle (wait for gathering, aiortc answers after its
# own gathering), exposes window.spike for the Playwright proof.
CLIENT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>voice spike</title>
<style>body{font:15px system-ui;margin:2rem;max-width:44rem}pre{background:#f4f4f4;padding:1rem;white-space:pre-wrap}</style>
</head><body>
<h1>SmallWebRTC echo spike</h1>
<p>Call, hear a chime, then hear yourself. <code>?relay=1</code> forces TURN-only
(simulates a UDP-blocked network when the TURN URL is TCP/TLS 443).</p>
<button id="call">Call</button> <button id="hang" disabled>Hang up</button>
<audio id="out" autoplay></audio>
<pre id="log"></pre>
<script>
const qs = new URLSearchParams(location.search);
const log = (m) => { document.getElementById('log').textContent += m + '\\n'; };
const spike = window.spike = { state: 'idle', pcId: null, pc: null, error: null };

async function call() {
  const { iceServers } = await (await fetch('/api/ice')).json();
  const relay = qs.get('relay') === '1';
  const pc = spike.pc = new RTCPeerConnection({ iceServers, iceTransportPolicy: relay ? 'relay' : 'all' });
  pc.onconnectionstatechange = () => { spike.state = pc.connectionState; log('pc: ' + pc.connectionState); };
  pc.ontrack = (e) => { document.getElementById('out').srcObject = e.streams[0] || new MediaStream([e.track]); };
  const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
  mic.getTracks().forEach((t) => pc.addTrack(t, mic));
  pc.addTransceiver('video', { direction: 'recvonly' });
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((res) => {
    if (pc.iceGatheringState === 'complete') return res();
    pc.addEventListener('icegatheringstatechange', () => pc.iceGatheringState === 'complete' && res());
    setTimeout(res, 4000);  // don't hang on a slow/unreachable STUN/TURN server
  });
  const headers = { 'Content-Type': 'application/json' };
  if (qs.get('token')) headers['X-Spike-Token'] = qs.get('token');
  const resp = await fetch('/api/offer', { method: 'POST', headers,
    body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }) });
  if (!resp.ok) throw new Error('offer failed: ' + resp.status);
  const answer = await resp.json();
  spike.pcId = answer.pc_id;
  await pc.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
  log('offer answered, pc_id=' + answer.pc_id + (relay ? ' (relay only)' : ''));
}

spike.stats = async () => {
  const out = { bytesReceived: 0, bytesSent: 0, pair: null };
  if (!spike.pc) return out;
  const report = await spike.pc.getStats();
  report.forEach((s) => {
    if (s.type === 'inbound-rtp' && s.kind === 'audio') out.bytesReceived += s.bytesReceived;
    if (s.type === 'outbound-rtp' && s.kind === 'audio') out.bytesSent += s.bytesSent;
    if (s.type === 'candidate-pair' && s.nominated && s.state === 'succeeded') {
      const l = report.get(s.localCandidateId), r = report.get(s.remoteCandidateId);
      out.pair = { local: l && l.candidateType, remote: r && r.candidateType,
                   protocol: l && l.protocol, relayProtocol: l && l.relayProtocol, rttMs: s.currentRoundTripTime * 1000 };
    }
  });
  return out;
};
spike.hangup = () => { if (spike.pc) spike.pc.close(); spike.pc = null; spike.state = 'closed'; };

document.getElementById('call').onclick = () => {
  document.getElementById('call').disabled = true; document.getElementById('hang').disabled = false;
  call().catch((e) => { spike.error = String(e); log('error: ' + e); });
};
document.getElementById('hang').onclick = () => {
  spike.hangup(); log('hung up');
  document.getElementById('call').disabled = false; document.getElementById('hang').disabled = true;
};
</script></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "7860")))
    args = parser.parse_args()
    logger.info(f"ICE (client view, creds redacted): {json.dumps([{'urls': s['urls']} for s in static_ice_servers()])}")
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
