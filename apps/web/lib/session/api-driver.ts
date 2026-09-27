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
import type { Caption, CallView, GmailCapability, GmailCard, SessionDriver, SessionSnapshot, ThreadItem, UIAction, UIPush } from "./types";
import { MicError, openMic, prepareCall, releaseMic, type CallMedia, type LinkState, type MicProblem } from "./webrtc";

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

/** EC-03 (spec §4.8): mic trouble is explained in text; the chat stays at the same node. */
const MIC_COPY: Record<MicProblem, string> = {
  denied:
    "I can’t hear you yet: your browser blocked the microphone. To allow it, click the icon at the left of the address bar, set Microphone to Allow, then call again.",
  missing: "I can’t hear you yet: I couldn’t find a microphone. Plug one in or check your sound settings, then call again.",
  unsupported: "I can’t hear you yet: this browser can’t make calls from here. Try the latest Chrome, Safari, or Firefox, then call again.",
};
/** Re-asks the question the brain is already waiting on (wording only; the node doesn't move). */
function keepTexting(s: AgentState): string {
  const empty = (k: SlotName) => s.slots[k]?.status !== "filled";
  const ask =
    (s.node === "call_offer" || s.node === "user_name") && empty("user_name")
      ? " So, what should I call you?"
      : (s.node === "call_offer" || s.node === "need") && empty("need")
        ? " So, what’s one thing you’d love a hand with?"
        : s.node === "gmail"
          ? " Last step is Gmail, with the button below."
          : "";
  return `Or we can just keep texting.${ask}`;
}
const HINT: Caption = { who: "", text: "", tone: "hint" };
const CAPTION_TURNS = 3;
const SPEAKING_ON = 0.12;
const SPEAKING_OFF = 0.05;

/** ICE servers for the browser leg. NEXT_PUBLIC_PERSONA_ICE_URLS: comma-separated, "none" = host only. */
function defaultIce(): RTCIceServer[] {
  const raw = process.env.NEXT_PUBLIC_PERSONA_ICE_URLS ?? "stun:stun.l.google.com:19302";
  const urls = raw.split(",").map((u) => u.trim()).filter((u) => u && u !== "none");
  return urls.length ? [{ urls }] : [];
}

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
  /** ICE servers for the call (defaults from NEXT_PUBLIC_PERSONA_ICE_URLS). */
  iceServers?: RTCIceServer[];
}

export class ApiSessionDriver implements SessionDriver {
  private state: AgentState | null;
  private items: ThreadItem[] = [];
  private justFilled: SlotName | null = null;
  private call: SessionSnapshot["call"] = null;
  private callId: string | null = null;
  /** The call leg this tab publishes on. At most one; only after we hold the lease. */
  private media: CallMedia | null = null;
  private dialing = false;
  /** Bumped by hang-up/cancel so a dial still in flight gives up (and hands its lease back). */
  private dialSeq = 0;
  private connectedAt: number | null = null;
  /** Lease held by another tab/device (EC-02/EC-09): shown as "in another tab", never joined. */
  private otherCallId: string | null = null;
  private otherLive = false;
  private speaking = false;
  private levelListeners = new Set<(l: number) => void>();
  private offPageHide: (() => void) | null = null;
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
  private readonly iceServers: RTCIceServer[];

  constructor(initial: AgentState | null, opts: ApiDriverOptions = {}) {
    this.state = initial;
    this.base = opts.basePath ?? "/api/session";
    this.oauthBase = opts.oauthPath ?? "/api/oauth/google";
    this.reconnectMs = opts.reconnectMs ?? 1500;
    this.iceServers = opts.iceServers ?? defaultIce();
    this.syncLease(initial);
    this.snap = this.compose();
    if (typeof window !== "undefined") {
      this.listenOAuth();
      this.listenPageHide();
    }
  }

  snapshot(): SessionSnapshot {
    return this.snap;
  }

  onPush(cb: (p: UIPush) => void): () => void {
    this.listeners.add(cb);
    if (this.state) this.connect();
    return () => this.listeners.delete(cb);
  }

  onLevel(cb: (l: number) => void): () => void {
    this.levelListeners.add(cb);
    return () => this.levelListeners.delete(cb);
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
    if (action === "call_take_over") return this.takeOver();
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

  /**
   * Real call path: mic -> RTCPeerConnection offer -> POST call (acquires the lease; the agent
   * answers with its SDP) -> apply answer. Never publishes without the lease (EC-02/EC-09).
   */
  async startCall(): Promise<void> {
    if (!this.state || this.callId || this.dialing) return;
    if (this.otherLive) return this.render(); // another device holds the call: take-over is explicit
    this.dialing = true;
    const seq = ++this.dialSeq;
    let mic: MediaStream | null = null;
    let media: CallMedia | null = null;
    try {
      try {
        mic = await openMic();
      } catch (e) {
        return this.micTrouble(e instanceof MicError ? e.problem : "denied");
      }
      this.call = { status: "ringing", elapsed: 0, ring: "ringing", captions: [{ ...HINT, text: `Captions appear here when ${agentNameOf(this.state) ?? "your assistant"} answers.` }] };
      this.connectedAt = null;
      this.render();
      try {
        media = await prepareCall(mic, this.iceServers);
      } catch {
        releaseMic(mic);
        return this.callFailed("Couldn’t start the call on this browser. Let’s keep texting.");
      }
      if (seq !== this.dialSeq) return media.close(); // cancelled while gathering
      const r = await fetch(`${this.base}/call`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(media.offer) }).catch(() => null);
      if (r?.status === 409) {
        media.close();
        this.call = null;
        this.otherLive = true;
        await this.refreshState();
        return this.render();
      }
      if (!r?.ok) {
        media.close();
        return this.callFailed("Couldn’t reach the call line. Your progress is saved, so let’s keep texting or try again.");
      }
      const body = (await r.json()) as { call_id: string; answer?: RTCSessionDescriptionInit | null };
      this.callId = body.call_id;
      if (seq !== this.dialSeq) {
        // Cancelled while the agent was answering: never publish, give the lease straight back.
        media.close();
        return void (await this.releaseLease("user_hangup"));
      }
      if (this.otherCallId === body.call_id) this.otherCallId = null;
      if (!body.answer?.sdp) {
        // Lease-only agent (voice not configured): give the lease back rather than ring forever.
        media.close();
        await this.releaseLease("voice_unavailable");
        return this.callFailed("Voice calls aren’t available right now. Let’s keep texting.");
      }
      this.media = media;
      media.onLink((l) => this.onLink(l));
      media.onLevel((l) => this.onRemoteLevel(l));
      try {
        await media.applyAnswer({ type: body.answer.type ?? "answer", sdp: body.answer.sdp });
      } catch {
        this.teardown();
        await this.releaseLease("bad_answer");
        return this.callFailed("The call didn’t connect. Your progress is saved, so let’s keep texting or try again.");
      }
    } finally {
      this.dialing = false;
    }
  }

  async endCall(): Promise<void> {
    this.dialSeq++;
    const ringing = this.call?.status === "ringing";
    this.teardown();
    await this.releaseLease("user_hangup");
    // Cancelling before the agent picked up just closes the rail.
    if (this.call) this.call = ringing ? null : this.endedView();
    this.render();
  }

  async setMuted(muted: boolean): Promise<void> {
    if (!this.call || this.call.status === "ended" || this.call.status === "elsewhere") return;
    this.media?.setMuted(muted);
    this.call = { ...this.call, status: muted ? "muted" : "connected", ring: muted ? "muted" : this.speaking ? "speaking" : "listening", badge: muted ? "You’re muted" : undefined };
    this.render();
  }

  close(): void {
    this.dialSeq++;
    this.offOAuth?.();
    this.offOAuth = null;
    this.offPageHide?.();
    this.offPageHide = null;
    this.teardown();
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
        const cs = data as { state?: string; call_id?: string | null };
        const id = cs.call_id ?? null;
        if (cs.state === "ended") {
          if (id && id === this.callId) {
            // The agent ended our leg (goodbye, lease lost, or taken over on another device).
            this.teardown();
            this.callId = null;
            if (this.call) this.call = this.endedView();
          } else if (!id || id === this.otherCallId) {
            this.otherCallId = null;
            this.otherLive = false;
          }
        } else if (id && id !== this.callId && !this.dialing) {
          this.otherCallId = id;
          this.otherLive = true;
        }
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
    if (t.channel === "voice" && this.call && this.media) {
      const who = from === "user" ? "You" : (agentNameOf(this.state!) ?? "Assistant");
      const caps = this.call.captions.filter((c) => !c.tone).map((c) => ({ ...c, live: false }));
      this.call = { ...this.call, captions: [...caps, { who, text: t.text, live: from === "agent" }].slice(-CAPTION_TURNS) };
    }
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
    this.syncLease(next);
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

  // --- call lease + media (FE-004) --------------------------------------------------------

  /** Mirror the brain's lease view: a live call that isn't ours belongs to another tab/device. */
  private syncLease(s: AgentState | null) {
    const c = s?.call;
    if (!c) return;
    if (c.live && c.call_id && c.call_id !== this.callId && !this.dialing) {
      this.otherCallId = c.call_id;
      this.otherLive = true;
    } else if (!c.live || c.call_id === this.callId) {
      this.otherCallId = null;
      this.otherLive = false;
    }
  }

  /** EC-02/EC-09: explicit take-over ends the other leg's lease first, then dials here. */
  private async takeOver(): Promise<void> {
    if (!this.otherCallId) await this.refreshState();
    const other = this.otherCallId;
    if (other) {
      await fetch(`${this.base}/call/${encodeURIComponent(other)}`, { method: "DELETE", headers: { "content-type": "application/json" }, body: JSON.stringify({ reason: "take_over" }) }).catch(() => null);
    }
    this.otherCallId = null;
    this.otherLive = false;
    this.call = null;
    await this.startCall();
  }

  private async refreshState() {
    const r = await fetch(this.base, { cache: "no-store" }).catch(() => null);
    if (!r?.ok) return;
    const body = (await r.json().catch(() => null)) as { state?: AgentState } | null;
    if (body?.state) this.applyState(body.state);
  }

  private onLink(l: LinkState) {
    if (!this.call || !this.media) return;
    if (l === "connected") {
      this.connectedAt ??= Date.now();
      const muted = this.call.status === "muted";
      this.call = { ...this.call, status: muted ? "muted" : "connected", ring: muted ? "muted" : "listening", elapsed: this.elapsed(), badge: muted ? "You’re muted" : undefined, captions: this.call.captions.filter((c) => !c.tone) };
    } else if (l === "reconnecting") {
      this.call = {
        ...this.call,
        status: "reconnecting",
        ring: "reconnecting",
        elapsed: this.elapsed(),
        badge: "Your progress is saved",
        captions: [...this.call.captions.filter((c) => !c.tone), { ...HINT, tone: "warn", text: "The line dropped. Trying to reconnect." }],
      };
    } else if (l === "failed") {
      this.teardown();
      void this.releaseLease("network_drop");
      this.call = this.endedView();
    }
    this.render();
  }

  private onRemoteLevel(l: number) {
    this.levelListeners.forEach((cb) => cb(l));
    const c = this.call;
    if (!c || c.status !== "connected") return;
    const speaking = this.speaking ? l > SPEAKING_OFF : l > SPEAKING_ON;
    if (speaking === this.speaking) return;
    this.speaking = speaking;
    this.call = { ...c, ring: speaking ? "speaking" : "listening", elapsed: this.elapsed() };
    this.render();
  }

  private micTrouble(problem: MicProblem) {
    const n = ++this.pending;
    this.items.push({ id: `mic-${n}`, kind: "msg", from: "agent", text: MIC_COPY[problem] });
    this.items.push({ id: `mic-${n}-ask`, kind: "msg", from: "agent", text: keepTexting(this.state!) });
    this.call = null;
    this.render();
  }

  private callFailed(text: string) {
    this.teardown();
    this.call = null;
    this.items.push({ id: `callerr-${++this.pending}`, kind: "stamp", text });
    this.render();
  }

  private elapsed(): number {
    return this.connectedAt ? Math.floor((Date.now() - this.connectedAt) / 1000) : 0;
  }

  private endedView(): CallView {
    return { status: "ended", elapsed: this.elapsed(), ring: "ended", captions: (this.call?.captions ?? []).filter((c) => !c.tone).map((c) => ({ ...c, live: false })) };
  }

  private teardown() {
    this.media?.close();
    this.media = null;
    this.speaking = false;
  }

  private async releaseLease(reason: string) {
    const id = this.callId;
    this.callId = null;
    if (id) await fetch(`${this.base}/call/${encodeURIComponent(id)}`, { method: "DELETE", headers: { "content-type": "application/json" }, body: JSON.stringify({ reason }) }).catch(() => null);
  }

  /** Closing the tab mid-call hands the lease back now instead of waiting out its TTL. */
  private listenPageHide() {
    const onHide = () => {
      const id = this.callId;
      if (!id) return;
      void fetch(`${this.base}/call/${encodeURIComponent(id)}`, { method: "DELETE", headers: { "content-type": "application/json" }, body: JSON.stringify({ reason: "page_closed" }), keepalive: true }).catch(() => null);
    };
    window.addEventListener("pagehide", onHide);
    this.offPageHide = () => window.removeEventListener("pagehide", onHide);
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
    // Another tab/device holds the call: the rail shows it (spec §4.4, EC-02) unless we have our own leg.
    const call: CallView | null = this.call ?? (this.otherLive && !s.graduated ? { status: "elsewhere", elapsed: 0, ring: "ended", captions: [] } : null);
    if (s.node === "call_offer" && agent && !call) thread.push(offerItem(agent));
    return {
      surface: s.graduated ? "home" : "chat",
      agentName: agent,
      checklist: toChecklist(s),
      justFilled: this.justFilled,
      thread,
      composer: call && call.status !== "ended" && call.status !== "elsewhere" ? { placeholder: "Type instead of talking…", callButton: false } : composerFor(s),
      call,
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
