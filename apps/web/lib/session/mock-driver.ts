// MockSessionDriver: stands in for the agent service until FE-002. It only replays fixed
// fixture snapshots (a canned "brain" reply per state + action); the UI never computes
// transitions itself. `?state=<name>` picks the starting fixture (used by qa:visual).
import { fixture, type StateName } from "./fixtures";
import type { SessionDriver, SessionSnapshot, UIAction, UIPush } from "./types";

type Reply = StateName | ((d: MockSessionDriver) => void);

/** Canned replies: [current fixture][action] -> fixture the fake brain pushes next. */
const REPLIES: Partial<Record<StateName, Partial<Record<UIAction | "call" | "hangup" | "mute" | "unmute", Reply>>>> = {
  landing: { begin: "chat-agent-name" },
  "call-offer": { decline_call: "call-declined", call: "call-ringing" },
  "call-declined": { call: "call-ringing" },
  "mic-denied": { call: "call-ringing" },
  "call-ringing": { hangup: "call-offer" },
  "call-connected": { mute: "call-muted", hangup: "call-ended" },
  "call-muted": { unmute: "call-connected", hangup: "call-ended" },
  "call-reconnecting": { hangup: "call-ended" },
  "call-ended": { call: "call-ringing" },
  "call-elsewhere": { call_take_over: "call-connected" },
  "gmail-card-idle": { gmail_connect: "gmail-card-connecting", hangup: "call-ended" },
  "gmail-card-connecting": { gmail_reopen: "gmail-card-connecting", hangup: "call-ended" },
  "gmail-card-connected": { gmail_disconnect: "gmail-card-idle", hangup: "call-ended" },
  "gmail-card-error": { gmail_retry: "gmail-card-connecting", gmail_skip: "graduation" },
  "gmail-card-wrong-account": { gmail_disconnect: "gmail-card-idle", gmail_keep: "graduation" },
  "welcome-back": { call: "call-ringing" },
};

export interface MockOptions {
  /** Test hook: how the mock learns mic permission. Defaults to real getUserMedia. */
  requestMic?: () => Promise<boolean>;
  /** Emit a fake agent audio level for the ring (disabled for visual capture). */
  fakeLevels?: boolean;
}

async function browserMic(): Promise<boolean> {
  try {
    if (!navigator.mediaDevices?.getUserMedia) return false;
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}

export class MockSessionDriver implements SessionDriver {
  private state: StateName;
  private snap: SessionSnapshot;
  private listeners = new Set<(p: UIPush) => void>();
  private levelListeners = new Set<(l: number) => void>();
  private timers: ReturnType<typeof setTimeout>[] = [];
  private levelTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    initial: StateName,
    private opts: MockOptions = {},
  ) {
    this.state = initial;
    this.snap = fixture(initial);
    if (opts.fakeLevels) this.startLevels();
  }

  snapshot(): SessionSnapshot {
    return this.snap;
  }

  onPush(cb: (p: UIPush) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  onLevel(cb: (l: number) => void): () => void {
    this.levelListeners.add(cb);
    return () => this.levelListeners.delete(cb);
  }

  async sendText(text: string): Promise<void> {
    const t = text.trim();
    if (!t) return;
    // Echo the user's turn the way the brain's transcript push would.
    this.emit({ type: "transcript", items: [{ id: `u${Date.now()}`, kind: "msg", from: "user", text: t }] });
  }

  async startCall(): Promise<void> {
    const ok = await (this.opts.requestMic ?? browserMic)();
    if (!ok) {
      // Real driver reports mic_denied to the brain, which answers in text (EC-03).
      this.replay("mic-denied");
      return;
    }
    this.handle("call");
    if (this.state === "call-ringing") this.later(1500, () => this.replay("call-connected"));
  }

  async endCall(): Promise<void> {
    this.handle("hangup");
  }

  async setMuted(muted: boolean): Promise<void> {
    this.handle(muted ? "mute" : "unmute");
  }

  async act(action: UIAction): Promise<void> {
    this.handle(action);
  }

  close(): void {
    this.timers.forEach(clearTimeout);
    if (this.levelTimer) clearInterval(this.levelTimer);
    this.listeners.clear();
    this.levelListeners.clear();
  }

  private handle(key: UIAction | "call" | "hangup" | "mute" | "unmute") {
    const r = REPLIES[this.state]?.[key];
    if (!r) return;
    if (typeof r === "function") r(this);
    else this.replay(r);
  }

  private replay(name: StateName) {
    this.state = name;
    this.snap = fixture(name);
    this.emit({ type: "snapshot", snapshot: this.snap });
  }

  private emit(p: UIPush) {
    if (p.type !== "snapshot") {
      // keep our own snapshot in sync for later snapshot() calls
      this.snap = p.type === "transcript" ? { ...this.snap, thread: [...this.snap.thread, ...p.items] } : this.snap;
    }
    this.listeners.forEach((cb) => cb(p));
  }

  private later(ms: number, fn: () => void) {
    this.timers.push(setTimeout(fn, ms));
  }

  private startLevels() {
    let t = 0;
    this.levelTimer = setInterval(() => {
      t += 1;
      const lvl = 0.45 + 0.35 * Math.sin(t / 2.3) * Math.sin(t / 5.1) + 0.15 * Math.random();
      this.levelListeners.forEach((cb) => cb(Math.max(0, Math.min(1, lvl))));
    }, 120);
  }
}
