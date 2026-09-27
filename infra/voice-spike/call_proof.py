"""INFRA-001 proof: a real Chromium call against the SmallWebRTC echo spike.

Starts `agent.voice.spike_echo` (unless --url is given), opens Chromium with a fake
microphone (`--use-fake-device-for-media-stream`), places a call, and asserts:
  1. the peer connection reaches `connected`,
  2. the browser receives bot audio (greeting + echo: inbound-rtp bytes grow),
  3. the server receives caller audio (in_frames > 0),
  4. hanging up tears the server-side call down (ended_reason set).
Prints one JSON line with the selected ICE candidate pair and timings.

    python infra/voice-spike/call_proof.py                    # local, all candidates
    python infra/voice-spike/call_proof.py --relay            # TURN-only (needs TURN env)
    python infra/voice-spike/call_proof.py --url https://<host> [--token ...]

Needs: pipecat-ai[webrtc], fastapi, uvicorn, httpx, playwright (python) + chromium.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 - local/known URL
        return json.loads(r.read())


def _wait_healthy(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if _get_json(f"{base}/health").get("ok"):
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise SystemExit(f"server at {base} never became healthy")


def run(base: str, relay: bool, token: str | None, headed: bool) -> dict:
    result: dict = {"url": base, "relay_only": relay}
    query = "?" + "&".join(p for p in ["relay=1" if relay else "", f"token={token}" if token else ""] if p)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            # Use a pre-installed Chromium when the pip playwright's pinned build is
            # not downloaded (e.g. one installed for the Node playwright).
            executable_path=os.environ.get("SPIKE_CHROMIUM") or None,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        ctx = browser.new_context(permissions=["microphone"])
        page = ctx.new_page()
        page.goto(base + "/" + (query if query != "?" else ""))
        t0 = time.time()
        page.click("#call")
        page.wait_for_function(
            "window.spike.state === 'connected' || window.spike.error || window.spike.state === 'failed'",
            timeout=30000,
        )
        state = page.evaluate("window.spike.state")
        result["connect_ms"] = round((time.time() - t0) * 1000)
        result["state"] = state
        result["error"] = page.evaluate("window.spike.error")
        if state != "connected":
            browser.close()
            return result
        pc_id = page.evaluate("window.spike.pcId")
        page.wait_for_timeout(1500)
        first = page.evaluate("window.spike.stats()")
        page.wait_for_timeout(2500)
        second = page.evaluate("window.spike.stats()")
        server = _get_json(f"{base}/api/calls?pc_id={urllib.parse.quote(pc_id, safe='')}")
        result.update(
            pair=second["pair"],
            browser_bytes_received=second["bytesReceived"],
            browser_bytes_sent=second["bytesSent"],
            audio_flowing_to_browser=second["bytesReceived"] > first["bytesReceived"] > 0,
            server_in_frames=server["in_frames"],
            server_candidate_types=server["local_candidates"],
        )
        page.evaluate("window.spike.hangup()")
        deadline = time.time() + 15
        while time.time() < deadline:
            server = _get_json(f"{base}/api/calls?pc_id={urllib.parse.quote(pc_id, safe='')}")
            if server["ended_reason"]:
                break
            time.sleep(0.5)
        result["server_ended_reason"] = server["ended_reason"]
        browser.close()
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="existing server; default spawns a local one")
    ap.add_argument("--relay", action="store_true", help="force iceTransportPolicy=relay")
    ap.add_argument("--token", default=os.environ.get("PERSONA_SPIKE_TOKEN"))
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    proc = None
    base = args.url
    if not base:
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        proc = subprocess.Popen(
            [sys.executable, "-m", "agent.voice.spike_echo", "--port", str(port)],
            cwd=ROOT / "services" / "agent",
            stdout=subprocess.DEVNULL if not os.environ.get("SPIKE_VERBOSE") else None,
            stderr=subprocess.DEVNULL if not os.environ.get("SPIKE_VERBOSE") else None,
        )
    try:
        _wait_healthy(base)
        result = run(base.rstrip("/"), args.relay, args.token, args.headed)
    finally:
        if proc:
            proc.terminate()
            proc.wait(timeout=10)
    print(json.dumps(result))
    ok = (
        result.get("state") == "connected"
        and result.get("audio_flowing_to_browser")
        and result.get("server_in_frames", 0) > 0
        and result.get("server_ended_reason")
        and (not args.relay or (result.get("pair") or {}).get("local") == "relay")
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
