#!/usr/bin/env bash
# Build the caller audio for apps/web/e2e/latency-probe.spec.ts (LAT-001).
# macOS only (say + afconvert). Writes apps/web/e2e/fixtures/latency-probe.{wav,json};
# the wav (~8 MB) is gitignored, the json (speech offsets) is committed.
#   16 s lead-in (connect + greeting), then five utterances, 12 s of silence after each.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=apps/web/e2e/fixtures
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$OUT"
i=0
while IFS= read -r line; do
  i=$((i + 1))
  say -v Samantha -o "$TMP/u$i.aiff" "$line"
  afconvert -f WAVE -d LEI16@48000 -c 1 "$TMP/u$i.aiff" "$TMP/u$i.wav"
done <<'LINES'
My name is Darran. D, A, R, R, A, N.
I'd like help automating my emails.
What can you actually do?
Let's skip Gmail for now.
No thanks, skip it.
LINES
python3 - "$TMP" "$OUT" "$i" <<'PY'
import array, json, sys, wave
tmp, out, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
RATE, LEAD, GAP = 48000, 16.0, 12.0
pcm, segs = array.array("h"), []
pcm.extend([0] * int(LEAD * RATE))
for k in range(1, n + 1):
    with wave.open(f"{tmp}/u{k}.wav") as w:
        a = array.array("h", w.readframes(w.getnframes()))
    loud = [j for j, v in enumerate(a) if abs(v) > 300]
    start = len(pcm) / RATE
    segs.append({"i": k, "start": round(start, 3), "end": round(start + len(a) / RATE, 3),
                 "speech_start": round(start + loud[0] / RATE, 3), "speech_end": round(start + loud[-1] / RATE, 3)})
    pcm.extend(a)
    pcm.extend([0] * int(GAP * RATE))
pcm.extend([0] * int(10 * RATE))
with wave.open(f"{out}/latency-probe.wav", "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm.tobytes())
json.dump(segs, open(f"{out}/latency-probe.json", "w"))
print(json.dumps(segs))
PY
