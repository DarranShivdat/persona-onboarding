import { expect, test } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

// LAT-001 hosted latency probe. A real browser call through the real agent with a scripted
// SPEECH file as the microphone (Chromium fake capture). Measures, in the page's clock:
//   connect_ms  = click "Call" -> call panel "connected"
//   greeting_ms = click "Call" -> first bot audio (the greeting) at the browser
//   turn_ms[i]  = end of the caller's utterance i (known offset in the WAV, from capture start)
//                 -> first bot audio onset after it (WebAudio RMS on the remote track)
// Writes .persona-qa/latency/probe-<stamp>.json (summarize p50/p90 across runs from those).
// See also e2e/hosted/latency-call.spec.ts (hosted project variant). Skipped unless PERSONA_LATENCY_PROBE=1 and
// PERSONA_E2E_REAL_AGENT_URL are set. Generate the WAV with scripts/latency-probe-wav.sh (macOS `say`).
const FIX = resolve(__dirname, "fixtures");
const WAV = join(FIX, "latency-probe.wav");
const SEGS = join(FIX, "latency-probe.json");
const ON = !!process.env.PERSONA_LATENCY_PROBE && !!process.env.PERSONA_E2E_REAL_AGENT_URL;

test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: [
      "--disable-features=WebRtcHideLocalIpsWithMdns",
      "--autoplay-policy=no-user-gesture-required",
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
      `--use-file-for-fake-audio-capture=${WAV}%noloop`,
    ],
  },
});

test("latency probe: connect time + user-stop -> first bot audio", async ({ page }, info) => {
  test.skip(!ON || info.project.name !== "desktop", "set PERSONA_LATENCY_PROBE=1 + PERSONA_E2E_REAL_AGENT_URL (desktop only)");
  test.setTimeout(150_000);
  const segs = JSON.parse(readFileSync(SEGS, "utf8")) as { i: number; speech_end: number }[];

  await page.addInitScript(() => {
    type Meter = { onsets: number[]; offsets: number[] };
    const w = window as unknown as { __lat: { gum: number | null; onsets: number[]; offsets: number[]; user: Meter } };
    w.__lat = { gum: null, onsets: [], offsets: [], user: { onsets: [], offsets: [] } };
    const meter = (track: MediaStreamTrack, into: Meter) => {
      const ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(new MediaStream([track]));
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      let speaking = false;
      let quietSince = performance.now();
      setInterval(() => {
        an.getFloatTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += v * v;
        const rms = Math.sqrt(sum / buf.length);
        const now = performance.now();
        if (rms > 0.01) {
          if (!speaking && now - quietSince > 350) into.onsets.push(now);
          speaking = true;
          quietSince = now;
        } else if (speaking && now - quietSince > 350) {
          speaking = false;
          into.offsets.push(quietSince);
        }
      }, 10);
    };
    const md = navigator.mediaDevices;
    const orig = md.getUserMedia.bind(md);
    md.getUserMedia = async (c) => {
      (w.__lat as { gumCall?: number }).gumCall ??= performance.now();
      const s = await orig(c);
      if (w.__lat.gum === null) {
        w.__lat.gum = performance.now();
        const t = s.getAudioTracks()[0];
        if (t) meter(t, w.__lat.user);
      }
      return s;
    };
    const PC = window.RTCPeerConnection;
    window.RTCPeerConnection = function (this: RTCPeerConnection, ...a: ConstructorParameters<typeof RTCPeerConnection>) {
      const pc = new PC(...a);
      pc.addEventListener("track", (ev) => {
        if (ev.track.kind !== "audio") return;
        meter(ev.track, w.__lat);
      });
      return pc;
    } as unknown as typeof RTCPeerConnection;
    window.RTCPeerConnection.prototype = PC.prototype;
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Get started" }).click();
  const composer = page.getByRole("textbox");
  await expect(page.getByTestId("thread")).toContainText("what would you like to call me?");
  await composer.fill("Juno");
  await composer.press("Enter");
  const panel = page.getByTestId("call-panel");
  // The offer appears only after the name turn returns: wait for it, or the click's
  // auto-wait (the text turn's LLM time) would be counted as connect time.
  const callBtn = page.getByTestId("call-offer").getByRole("button", { name: "Call Juno" });
  await expect(callBtn).toBeVisible({ timeout: 30_000 });
  const t0 = await page.evaluate(() => performance.now());
  await callBtn.click();
  await expect(panel).toHaveAttribute("data-status", /connected|muted/, { timeout: 30_000 });
  const tConnected = await page.evaluate(() => performance.now());

  // Let the whole script play (last utterance + reply), or stop when the call ends.
  const lastEnd = Math.max(...segs.map((s) => s.speech_end));
  const deadline = Date.now() + (lastEnd + 14) * 1000;
  while (Date.now() < deadline) {
    const st = await page.evaluate(() => document.querySelector('[data-testid="call-panel"]')?.getAttribute("data-status") ?? "ended");
    if (st === "ended") break;
    await page.waitForTimeout(500);
  }
  const lat = await page.evaluate(() => (window as unknown as { __lat: { gum: number; onsets: number[]; offsets: number[]; user: { onsets: number[]; offsets: number[] } } }).__lat);
  // A caller turn ends at a mic offset with no mic onset in the next 1.2 s (commas in the
  // spelled name pause ~0.4 s). Its latency = the first bot onset after that offset, if the
  // bot answered before the caller spoke again (else null: no reply in the gap).
  const turns: { user_stop_ms: number; first_audio_ms: number | null }[] = [];
  const uOn = lat.user.onsets;
  lat.user.offsets.forEach((off) => {
    const next = uOn.find((o) => o > off);
    if (next !== undefined && next - off < 1200) return;
    const on = lat.onsets.find((o) => o > off && (next === undefined || o < next));
    turns.push({ user_stop_ms: Math.round(off - lat.gum), first_audio_ms: on === undefined ? null : Math.round(on - off) });
  });
  void segs;
  const out = { at: new Date().toISOString(), web: process.env.PERSONA_WEB_URL, connect_ms: Math.round(tConnected - t0), greeting_ms: lat.onsets[0] === undefined ? null : Math.round(lat.onsets[0] - t0), gum_after_click_ms: Math.round(lat.gum - t0), gum_call_after_click_ms: Math.round(((lat as { gumCall?: number }).gumCall ?? lat.gum) - t0), onsets: lat.onsets.map((o) => Math.round(o - lat.gum)), user_onsets: lat.user.onsets.map((o) => Math.round(o - lat.gum)), user_offsets: lat.user.offsets.map((o) => Math.round(o - lat.gum)), turns };
  const dir = resolve(__dirname, "../../../.persona-qa/latency");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `probe-${Date.now()}.json`), JSON.stringify(out, null, 2));
  console.log("LATENCY_PROBE " + JSON.stringify(out));
});
