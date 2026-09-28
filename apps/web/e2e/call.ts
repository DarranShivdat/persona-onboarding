import type { Browser, BrowserContext, Page } from "@playwright/test";
import { AGENT_URL } from "./live";

// FE-004 call helpers: fake media for the page under test and a "bot" peer that answers the
// offer through the stub agent's signaling double (e2e/stub-agent.mjs). No real mic, no vendors.

/** Grant a fake mic (an oscillator) and record every RTCPeerConnection the app creates. */
export async function fakeMic(context: BrowserContext): Promise<void> {
  await context.addInitScript(() => {
    const w = window as unknown as { __pcs: RTCPeerConnection[] };
    w.__pcs = [];
    const Native = window.RTCPeerConnection;
    window.RTCPeerConnection = class extends Native {
      constructor(cfg?: RTCConfiguration) {
        super(cfg);
        w.__pcs.push(this);
      }
    } as typeof RTCPeerConnection;
    const getUserMedia = async () => {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const dst = ctx.createMediaStreamDestination();
      osc.connect(dst);
      osc.start();
      return dst.stream;
    };
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
  });
}

/** Deny the mic the way a browser does when the user blocks it (EC-03). */
export async function denyMic(context: BrowserContext | Page): Promise<void> {
  await context.addInitScript(() => {
    const deny = () => Promise.reject(new DOMException("Permission denied", "NotAllowedError"));
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: deny }, configurable: true });
  });
}

/** Connection states of the app's peer connections (from fakeMic's recorder). */
export function pcStates(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as unknown as { __pcs: RTCPeerConnection[] }).__pcs.map((pc) => pc.connectionState));
}

export interface Bot {
  page: Page;
  /** Connection states of every leg the bot has answered. */
  states(): Promise<string[]>;
  close(): Promise<void>;
}

/** Stand-in for the Pipecat bot: answers each offer for `sessionId` and plays a tone back. */
export async function startBot(browser: Browser, sessionId: string): Promise<Bot> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${AGENT_URL}/health`);
  await page.evaluate((id) => {
    const w = window as unknown as { __bot: RTCPeerConnection[] };
    w.__bot = [];
    const base = `/__test/sessions/${id}/bot`;
    void (async () => {
      for (;;) {
        const r = await fetch(`${base}/offer`).catch(() => null);
        if (!r || r.status !== 200) continue;
        const o = (await r.json()) as { call_id: string; sdp: string };
        const pc = new RTCPeerConnection();
        w.__bot.push(pc);
        const ctx = new AudioContext();
        const osc = ctx.createOscillator();
        const dst = ctx.createMediaStreamDestination();
        osc.connect(dst);
        osc.start();
        await pc.setRemoteDescription({ type: "offer", sdp: o.sdp });
        dst.stream.getTracks().forEach((t) => pc.addTrack(t, dst.stream));
        await pc.setLocalDescription(await pc.createAnswer());
        await new Promise<void>((res) => {
          if (pc.iceGatheringState === "complete") return res();
          pc.addEventListener("icegatheringstatechange", () => pc.iceGatheringState === "complete" && res());
          setTimeout(res, 3000);
        });
        await fetch(`${base}/answer`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ call_id: o.call_id, sdp: pc.localDescription!.sdp, type: "answer" }) });
      }
    })();
  }, sessionId);
  return {
    page,
    states: () => page.evaluate(() => (window as unknown as { __bot: RTCPeerConnection[] }).__bot.map((pc) => pc.connectionState)),
    close: () => context.close(),
  };
}
