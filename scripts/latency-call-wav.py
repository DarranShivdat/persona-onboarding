#!/usr/bin/env python3
"""Build the scripted caller audio for the hosted latency probe (LAT-001).

macOS only (`say` + `afconvert`). Writes .persona-qa/latency/caller.wav (48 kHz mono 16-bit,
gitignored). Chromium plays it as the fake microphone from getUserMedia; the probe measures
speech end on the *sent* mic track in the page, so no offset bookkeeping is needed here.

    python3 scripts/latency-call-wav.py [--out PATH]

Script: long lead silence (dial + greeting), then one caller line per onboarding step with
enough silence after it for the agent's reply (the Gmail pitch is ~12 s).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

RATE = 48000
ROOT = Path(__file__).resolve().parents[1]
LEAD_S = 10.0
LINES = [  # (text, silence after, s)
    ("My name is Darran.", 11.0),
    ("I'd like help triaging my inbox every morning.", 17.0),
    ("What can you actually do?", 17.0),
    ("Let's skip Gmail for now.", 12.0),
]


def tts(text: str, tmp: Path, i: int) -> bytes:
    aiff, wav = tmp / f"l{i}.aiff", tmp / f"l{i}.wav"
    subprocess.run(["say", "-v", os.environ.get("PROBE_VOICE", "Samantha"), "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "1", str(aiff), str(wav)], check=True)
    with wave.open(str(wav)) as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / ".persona-qa" / "latency" / "caller.wav"))
    out = Path(ap.parse_args().out)
    out.parent.mkdir(parents=True, exist_ok=True)
    silence = lambda s: b"\x00\x00" * int(RATE * s)  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        pcm = silence(LEAD_S) + b"".join(tts(t, Path(d), i) + silence(after) for i, (t, after) in enumerate(LINES))
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    print(f"wrote {out} ({len(pcm) / 2 / RATE:.1f}s, {len(LINES)} caller lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
