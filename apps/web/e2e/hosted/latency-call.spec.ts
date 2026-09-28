import { expect, test } from "@playwright/test";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { HOSTED_WEB, SKIP_REASON } from "./hosted";

// LAT-001 hosted latency probe: a real browser call through the live web + agent (real Deepgram,
// Claude, Cartesia over Cloudflare TURN) with a scripted caller as the fake mic
// (scripts/latency-call-wav.py -> .persona-qa/latency/caller.wav). Both ends are measured in
// the page's clock with a WebAudio RMS detector, on the SENT mic track and the received agent
// track, so no file-offset bookkeeping is involved:
//   connect_ms        click "Call" -> call panel "connected"
//   greeting_ms       click "Call" -> first agent audio
//   turns[i].reply_ms caller speech end -> first agent audio after it (null = no reply before
//                     the silence floor; `nudge` marks replies later than NUDGE_MS)
// Writes .persona-qa/latency/call-<ts>.json. Opt-in: PERSONA_LATENCY_PROBE=1 (+ the hosted project).
const ROOT = resolve(__dirname, "../../../..");
const WAV = process.env.PERSONA_LATENCY_WAV ?? join(ROOT, ".persona-qa/latency/caller.wav");
const ON = !!process.env.PERSONA_LATENCY_PROBE && !!HOSTED_WEB && existsSync(WAV);
const NUDGE_MS = 6500; // the silence floor's first nudge is at 7 s

test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: [
      "--autoplay-policy=no-user-gesture-required",
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
      `--use-file-for-fake-audio-capture=${WAV}%noloop`,
    ],
  },
});

type Lat = { local: { on: number[]; off: number[] }; remote: { on: number[]; off: number[] } };

test("latency: connect + caller speech end -> first agent audio", async ({ page }) => {
  test.skip(!ON, `set PERSONA_LATENCY_PROBE=1 and generate ${WAV}; ${SKIP_REASON}`);
  test.setTimeout(180_000);

  await page.addInitScript(() => {
    const w = window as unknown as { __lat: Lat };
    w.__lat = { local: { on: [], off: [] }, remote: { on: [], off: [] } };
    const watch = (track: MediaStreamTrack, sink: { on: number[]; off: number[] }, hangMs: number) => {
      const ctx = new AudioContext();
      void ctx.resume();
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      ctx.createMediaStreamSource(new MediaStream([track])).connect(an);
      const buf = new Float32Array(an.fftSize);
      let speaking = false;
      let last = 0;
      setInterval(() => {
        an.getFloatTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += v * v;
        const now = performance.now();
        if (Math.sqrt(sum / buf.length) > 0.01) {
          if (!speaking) sink.on.push(now);
          speaking = true;
          last = now;
        } else if (speaking && now - last > hangMs) {
          speaking = false;
          sink.off.push(last);
        }
      }, 5);
    };
    const md = navigator.mediaDevices;
    const gum = md.getUserMedia.bind(md);
    md.getUserMedia = async (c) => {
      const s = await gum(c);
      const t = s.getAudioTracks()[0];
      if (t) watch(t, w.__lat.local, 600); // caller: words inside a sentence stay one utterance
      return s;
    };
    const PC = window.RTCPeerConnection;
    const Wrapped = function (this: RTCPeerConnection, ...a: ConstructorParameters<typeof RTCPeerConnection>) {
      const pc = new PC(...a);
      pc.addEventListener("track", (ev) => {
        if (ev.track.kind === "audio") watch(ev.track, w.__lat.remote, 400);
      });
      return pc;
    } as unknown as typeof RTCPeerConnection;
    Wrapped.prototype = PC.prototype;
    window.RTCPeerConnection = Wrapped;
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Get started" }).click();
  const thread = page.getByTestId("thread");
  await expect(thread).toContainText("what would you like to call me?", { timeout: 20_000 });
  const composer = page.getByRole("textbox");
  await composer.fill("Juno");
  await composer.press("Enter");
  const panel = page.getByTestId("call-panel");
  const callBtn = page.getByTestId("call-offer").getByRole("button", { name: "Call Juno" });
  await expect(callBtn).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(1500); // the offer is on screen: ICE prefetch (if any) has had a moment, like a person
  const t0 = await page.evaluate(() => performance.now());
  await callBtn.click();
  await expect(panel).toHaveAttribute("data-status", /connected|muted/, { timeout: 30_000 });
  const tConnected = await page.evaluate(() => performance.now());

  // Let the script play out (or stop when the agent ends the call, e.g. after graduation).
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    if ((await panel.getAttribute("data-status", { timeout: 1000 }).catch(() => "ended")) === "ended") break; // graduation swaps in the home screen
    await page.waitForTimeout(500);
  }
  await page.waitForTimeout(1000);
  const lat = await page.evaluate(() => (window as unknown as { __lat: Lat }).__lat);
  const r = lat.remote;
  const speakingAt = (t: number) => r.on.some((on, k) => on <= t && (r.off[k] ?? Infinity) >= t);
  const turns = lat.local.off
    .filter((end) => end > tConnected)
    .map((end, i) => {
      const next = r.on.find((on) => on > end);
      const reply = next === undefined ? null : Math.round(next - end);
      return { i: i + 1, speech_end_ms: Math.round(end - t0), reply_ms: reply, nudge: reply !== null && reply > NUDGE_MS, barge_in: speakingAt(end) };
    });
  const out = {
    at: new Date().toISOString(),
    web: HOSTED_WEB,
    connect_ms: Math.round(tConnected - t0),
    greeting_ms: r.on[0] !== undefined ? Math.round(r.on[0] - t0) : null,
    turns,
    local: { on: lat.local.on.map((x) => Math.round(x - t0)), off: lat.local.off.map((x) => Math.round(x - t0)) },
    remote: { on: r.on.map((x) => Math.round(x - t0)), off: r.off.map((x) => Math.round(x - t0)) },
  };
  const dir = join(ROOT, ".persona-qa/latency");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `call-${Date.now()}.json`), JSON.stringify(out, null, 2));
  console.log("LATENCY_CALL " + JSON.stringify({ connect_ms: out.connect_ms, greeting_ms: out.greeting_ms, turns }));
  expect(turns.length).toBeGreaterThan(0);
});
