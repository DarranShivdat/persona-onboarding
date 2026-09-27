// ApiSessionDriver: the real driver (FE-002). Talks only to same-origin route handlers
// (app/api/session/*), which hold the session token in an httpOnly cookie and forward to
// the agent API. The brain decides everything; this class folds its SSE pushes into a
// SessionSnapshot for rendering and forwards user intents as turns.
import {
  agentNameOf,
  composerFor,
  initialsOf,
  landingSnapshot,
  offerItem,
  toChecklist,
  toHome,
  type AgentState,
  type AgentTranscript,
} from "./agent-state";
import type { SlotName } from "@/lib/flow-types";
import type { GmailCapability, GmailCard, SessionDriver, SessionSnapshot, ThreadItem, UIAction, UIPush } from "./types";

const PUSH_TYPES = ["transcript", "state", "gmail_connect_card", "gmail_connected", "start_call", "end_call", "call_state", "graduate"] as const;
const JUST_FILLED_MS = 1600;

/** User intents from buttons are forwarded as the words on the button; the brain interprets them. */
const ACTION_TEXT: Partial<Record<UIAction, string>> = {
  decline_call: "Keep texting",
  gmail_not_now: "Not now",
  gmail_skip: "Skip for now",
  gmail_keep: "Keep this account",
};

/** What the OAuth popup reports (app/api/oauth/google/callback). Rendering data only. */
type OAuthResult =
  | { type: "persona:gmail"; status: "connected" | "wrong_account"; email: string; name: string; missing: GmailCapability[] }
  | { type: "persona:gmail"; status: "error"; reason: string };
const CLOSED_GRACE_MS = 1500;

function isOAuthResult(d: unknown): d is OAuthResult {
  const r = d as Partial<OAuthResult> | null;
  return !!r && r.type === "persona:gmail" && (r.status === "error" || ((r.status === "connected" || r.status === "wrong_account") && typeof (r as { email?: unknown }).email === "string"));
}

export interface ApiDriverOptions {
  /** Base path of the same-origin proxy. */
  basePath?: string;
  /** Base path of the Google OAuth routes (popup). */
  oauthPath?: string;
  /** Reconnect backoff after the stream is closed for good (ms). */
  reconnectMs?: number;
}

export class ApiSessionDriver implements SessionDriver {
  private state: AgentState | null;
  private items: ThreadItem[] = [];
  private justFilled: SlotName | null = null;
  private call: SessionSnapshot["call"] = null;
  private callId: string | null = null;
  private snap: SessionSnapshot;
  private lastEventId = 0;
  private pending = 0;
  private es: EventSource | null = null;
  private listeners = new Set<(p: UIPush) => void>();
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private starting: Promise<void> | null = null;
  private popup: Window | null = null;
  private switchAccount = false;
  /** An OAuth attempt is in flight: its popup result is still owed to the card. */
  private oauthPending = false;
  private offOAuth: (() => void) | null = null;
  private readonly base: string;
  private readonly oauthBase: string;
  private readonly reconnectMs: number;

  constructor(initial: AgentState | null, opts: ApiDriverOptions = {}) {
    this.state = initial;
    this.base = opts.basePath ?? "/api/session";
    this.oauthBase = opts.oauthPath ?? "/api/oauth/google";
    this.reconnectMs = opts.reconnectMs ?? 1500;
    this.snap = this.compose();
    if (typeof window !== "undefined") this.listenOAuth();
  }

  snapshot(): SessionSnapshot {
    return this.snap;
  }

  onPush(cb: (p: UIPush) => void): () => void {
    this.listeners.add(cb);
    if (this.state) this.connect();
    return () => this.listeners.delete(cb);
  }

  async act(action: UIAction): Promise<void> {
    if (action === "begin") return this.begin();
    // OAuth actions open the popup synchronously (inside the click) so it isn't blocked.
    if (action === "gmail_connect" || action === "gmail_retry") return this.startOAuth(false);
    if (action === "gmail_disconnect") return this.startOAuth(true); // EC-22: re-run OAuth with the account chooser
    if (action === "gmail_reopen") return this.reopenOAuth();
    if (action === "gmail_keep") {
      const c = this.gmailCard();
      if (c?.state === "wrong_account") this.upsertGmail({ ...c, state: "connected" });
    }
    const text = ACTION_TEXT[action];
    if (text) return this.sendText(text);
    // call_take_over: FE-004 / VOICE-001.
  }

  async sendText(text: string): Promise<void> {
    const t = text.trim();
    if (!t) return;
    if (!this.state) await this.begin();
    // Optimistic echo until the brain's own `transcript` push arrives over SSE.
    const id = `pending-${++this.pending}`;
    this.items.push({ id, kind: "msg", from: "user", text: t });
    this.render();
    let ok = false;
    for (let attempt = 0; attempt < 2 && !ok; attempt++) {
      try {
        const r = await fetch(`${this.base}/turns`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ text: t }),
        });
        ok = r.ok;
        if (r.ok) {
          const body = (await r.json()) as { state?: AgentState };
          if (body.state) this.applyState(body.state);
        } else if (r.status !== 409) break; // 409 version_conflict: reload + retry once
      } catch {
        break;
      }
    }
    if (!ok) {
      this.items = this.items.filter((i) => i.id !== id);
      this.items.push({ id: `err-${id}`, kind: "stamp", text: "Couldn’t send that. Check your connection and try again." });
      this.render();
    }
  }

  async startCall(): Promise<void> {
    if (!this.state || this.callId) return;
    const r = await fetch(`${this.base}/call`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" }).catch(() => null);
    if (r?.ok) {
      const body = (await r.json()) as { call_id: string };
      this.callId = body.call_id;
      // WebRTC (the SDP answer) lands in FE-004/VOICE-001; until then the lease is held and
      // the panel shows the ringing state.
      this.call = { status: "ringing", elapsed: 0, ring: "ringing", captions: [] };
    } else if (r?.status === 409) {
      this.call = { status: "elsewhere", elapsed: 0, ring: "idle", captions: [] };
    }
    this.render();
  }

  async endCall(): Promise<void> {
    const id = this.callId;
    this.callId = null;
    if (id) await fetch(`${this.base}/call/${encodeURIComponent(id)}`, { method: "DELETE", headers: { "content-type": "application/json" }, body: JSON.stringify({ reason: "user_hangup" }) }).catch(() => null);
    if (this.call) this.call = { ...this.call, status: "ended", ring: "ended" };
    this.render();
  }

  async setMuted(muted: boolean): Promise<void> {
    if (!this.call) return;
    this.call = { ...this.call, status: muted ? "muted" : "connected", ring: muted ? "muted" : "listening" };
    this.render();
  }

  close(): void {
    this.offOAuth?.();
    this.offOAuth = null;
    this.es?.close();
    this.es = null;
    this.timers.forEach(clearTimeout);
    this.timers.clear();
  }

  // --- session + stream ---------------------------------------------------------------

  private begin(): Promise<void> {
    this.starting ??= (async () => {
      const r = await fetch(this.base, { method: "POST" });
      if (!r.ok) throw new Error(`session start failed: ${r.status}`);
      const body = (await r.json()) as { state: AgentState };
      this.applyState(body.state);
      this.connect();
    })().finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  private connect() {
    if (this.es || typeof EventSource === "undefined") return;
    // Without a cursor the agent replays every push from the start, which rebuilds the
    // chat after a refresh (EC-08). With one, only later pushes arrive.
    const url = this.lastEventId ? `${this.base}/events?after=${this.lastEventId}` : `${this.base}/events`;
    const es = new EventSource(url);
    this.es = es;
    for (const type of PUSH_TYPES) es.addEventListener(type, (e) => this.onEvent(type, e as MessageEvent<string>));
    es.onerror = () => {
      // CONNECTING: the browser retries on its own and sends Last-Event-ID. CLOSED (e.g. a
      // proxy 5xx): reconnect ourselves from our cursor.
      if (es.readyState !== EventSource.CLOSED) return;
      es.close();
      if (this.es === es) this.es = null;
      this.later(this.reconnectMs, () => {
        if (this.listeners.size) this.connect();
      });
    };
  }

  private onEvent(type: (typeof PUSH_TYPES)[number], e: MessageEvent<string>) {
    const id = Number(e.lastEventId);
    if (Number.isFinite(id) && id > 0) {
      if (id <= this.lastEventId) return; // de-dupe replays / overlapping reconnects
      this.lastEventId = id;
    }
    let data: unknown;
    try {
      data = JSON.parse(e.data);
    } catch {
      return;
    }
    switch (type) {
      case "transcript":
        return this.onTranscript(data as AgentTranscript, id);
      case "state":
        return this.applyState(data as AgentState);
      case "gmail_connect_card":
        // One card per session, re-shown in place (the brain may push it again on a reask).
        if (!this.gmailCard()) this.upsertGmail({ state: "idle" });
        return this.render();
      case "gmail_connected": {
        const email = (data as { email?: string }).email ?? this.state?.slots.gmail?.value ?? "";
        const cur = this.gmailCard();
        // The popup's result already drew this account (with its name / partial grant / wrong-account hint).
        if (cur?.account && cur.account.email === email && cur.state !== "error") return this.render();
        this.upsertGmail({ state: "connected", account: email ? { name: email, email, initials: initialsOf(email, email) } : undefined });
        return this.render();
      }
      case "call_state": {
        const cs = data as { state?: string };
        if (cs.state === "ended" && this.call) this.call = { ...this.call, status: "ended", ring: "ended" };
        return this.render();
      }
      case "graduate":
      case "start_call":
      case "end_call":
        // Surface follows `state.graduated`; call pushes wire up with FE-004.
        return this.render();
    }
  }

  private onTranscript(t: AgentTranscript, id: number) {
    if (!t?.text) return;
    const from = t.role === "user" ? "user" : "agent";
    if (from === "user") {
      const i = this.items.findIndex((x) => x.kind === "msg" && x.id.startsWith("pending-") && x.text === t.text);
      if (i >= 0) this.items.splice(i, 1);
    }
    this.items.push({ id: `e${id || Date.now()}`, kind: "msg", from, text: t.text, voice: t.channel === "voice" || undefined });
    this.render();
  }

  private gmailCard(): GmailCard | null {
    const it = this.items.find((i) => i.id === "gmail-card");
    return it?.kind === "gmail" ? it.card : null;
  }

  private upsertGmail(card: GmailCard) {
    const item: ThreadItem = { id: "gmail-card", kind: "gmail", card };
    const i = this.items.findIndex((x) => x.id === "gmail-card");
    if (i >= 0) this.items[i] = item;
    else this.items.push(item);
  }

  private applyState(next: AgentState) {
    if (this.state && next.version < this.state.version) return; // stale (turn reply vs SSE race)
    const prev = this.state;
    this.state = next;
    const newly = prev ? (Object.keys(next.slots) as SlotName[]).find((k) => next.slots[k]?.status === "filled" && prev.slots[k]?.status !== "filled") : undefined;
    if (newly) {
      this.justFilled = newly;
      this.later(JUST_FILLED_MS, () => {
        this.justFilled = null;
        this.render();
      });
    }
    this.render();
  }

  // --- Google OAuth popup (FE-003) ------------------------------------------------------

  private startOAuth(switchAccount: boolean): void {
    if (typeof window === "undefined") return;
    this.switchAccount = switchAccount;
    this.oauthPending = true;
    this.upsertGmail({ state: "connecting" });
    this.openPopup();
    this.render();
  }

  private reopenOAuth(): void {
    if (this.popup && !this.popup.closed) this.popup.focus();
    else this.openPopup();
  }

  private openPopup() {
    const url = `${this.oauthBase}/start${this.switchAccount ? "?switch=1" : ""}`;
    // null when a blocker ate it: the connecting card offers "Open the Google window again".
    this.popup = window.open(url, "persona-google", "popup,width=520,height=680");
  }

  private listenOAuth() {
    const onResult = (d: unknown) => {
      if (isOAuthResult(d)) this.onOAuthResult(d);
    };
    const onMessage = (e: MessageEvent) => {
      if (e.origin === window.location.origin) onResult(e.data);
    };
    // The popup reports via postMessage and a BroadcastChannel (Google's COOP can sever `opener`).
    let bc: BroadcastChannel | null = null;
    try {
      bc = new BroadcastChannel("persona-gmail");
      bc.onmessage = (e) => onResult(e.data);
    } catch {
      bc = null;
    }
    // EC-21: the window was closed without finishing. Checked when focus comes back to us.
    const onFocus = () => {
      if (this.gmailCard()?.state !== "connecting" || !this.popup?.closed) return;
      this.later(CLOSED_GRACE_MS, () => {
        if (this.gmailCard()?.state !== "connecting" || !this.popup?.closed) return;
        this.popup = null;
        this.upsertGmail({ state: "error" });
        this.render();
      });
    };
    window.addEventListener("message", onMessage);
    window.addEventListener("focus", onFocus);
    this.offOAuth = () => {
      window.removeEventListener("message", onMessage);
      window.removeEventListener("focus", onFocus);
      bc?.close();
    };
  }

  private onOAuthResult(r: OAuthResult) {
    // Once per attempt: ignores the duplicate (postMessage + BroadcastChannel) and other tabs.
    // It may land after the brain's `gmail_connected` push (or after we assumed the window was
    // closed) and then enriches the card with the name / partial grant / wrong-account hint.
    if (!this.oauthPending) return;
    this.oauthPending = false;
    this.popup = null;
    if (r.status === "error") this.upsertGmail({ state: "error" });
    else this.upsertGmail({ state: r.status, account: { name: r.name, email: r.email, initials: initialsOf(r.name, r.email) }, missing: r.missing });
    this.render();
  }

  // --- render ---------------------------------------------------------------------------

  private compose(): SessionSnapshot {
    const s = this.state;
    if (!s) return landingSnapshot();
    const agent = agentNameOf(s);
    const thread = [...this.items];
    if (s.node === "call_offer" && agent && !this.call) thread.push(offerItem(agent));
    return {
      surface: s.graduated ? "home" : "chat",
      agentName: agent,
      checklist: toChecklist(s),
      justFilled: this.justFilled,
      thread,
      composer: this.call && this.call.status !== "ended" ? { placeholder: "Type instead of talking…", callButton: false } : composerFor(s),
      call: this.call,
      home: s.graduated ? toHome(s) : null,
    };
  }

  private render() {
    this.snap = this.compose();
    const p: UIPush = { type: "snapshot", snapshot: this.snap };
    this.listeners.forEach((cb) => cb(p));
  }

  private later(ms: number, fn: () => void) {
    const t = setTimeout(() => {
      this.timers.delete(t);
      fn();
    }, ms);
    this.timers.add(t);
  }
}
