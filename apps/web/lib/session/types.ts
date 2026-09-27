// Session driver contract (ARCHITECTURE §6). The browser only renders what the brain
// pushes: it never decides which node comes next. FE-001 ships MockSessionDriver; FE-002
// adds the real driver (Next route handlers -> agent API, SSE pushes).
import type { SlotName } from "@/lib/flow-types";

export type ChecklistStatus = "empty" | "filled" | "deferred";
export interface ChecklistItem {
  status: ChecklistStatus;
  value?: string;
}
export type Checklist = Record<SlotName, ChecklistItem>;

export type ThreadItem =
  | { id: string; kind: "stamp"; text: string }
  | { id: string; kind: "divider"; text: string }
  | { id: string; kind: "msg"; from: "agent" | "user"; text: string; voice?: boolean }
  | { id: string; kind: "chips"; options: string[] }
  | { id: string; kind: "offer"; title: string; subtitle: string; primary: string; secondary: string; secondaryAction: UIAction }
  | { id: string; kind: "gmail"; card: GmailCard };

export type GmailCardState = "idle" | "connecting" | "connected" | "error" | "wrong_account";
/** Gmail capabilities a partial grant left out (the user unticked scopes on Google's screen). */
export type GmailCapability = "read" | "organize" | "send";
export interface GmailCard {
  state: GmailCardState;
  account?: { name: string; email: string; initials: string };
  /** Connected with reduced capability; absent or empty = full access. */
  missing?: GmailCapability[];
}

export type CallStatus = "ringing" | "connected" | "muted" | "reconnecting" | "ended" | "elsewhere";
export type RingState = "idle" | "ringing" | "speaking" | "listening" | "muted" | "reconnecting" | "ended";
export interface Caption {
  who: string;
  text: string;
  live?: boolean;
  tone?: "hint" | "warn";
}
export interface CallView {
  status: CallStatus;
  /** Seconds on the call when this snapshot was taken (drives the timer display). */
  elapsed: number;
  ring: RingState;
  captions: Caption[];
  badge?: string;
}

export interface HomeView {
  userName: string;
  focus: { label: string; value: string; detail: string };
  tiles: { label: string; value: string }[];
  deferred: { id: string; slot: SlotName; title: string; reason: string; action: string }[];
}

export interface SessionSnapshot {
  surface: "landing" | "chat" | "home";
  agentName: string | null;
  checklist: Checklist;
  /** Slot that has just been filled (highlighted with signal-wash for 1.6s). */
  justFilled: SlotName | null;
  thread: ThreadItem[];
  composer: { placeholder: string; callButton: boolean };
  call: CallView | null;
  home: HomeView | null;
}

/** UI pushes from the brain (ARCHITECTURE §6), plus a full `snapshot` for restores. */
export type UIPush =
  | { type: "snapshot"; snapshot: SessionSnapshot }
  | { type: "transcript"; items: ThreadItem[] }
  | { type: "state"; checklist: Checklist; justFilled: SlotName | null; composer: SessionSnapshot["composer"] }
  | { type: "gmail_connect_card"; itemId: string; card: GmailCard }
  | { type: "call_state"; call: CallView | null }
  | { type: "graduate"; home: HomeView };

/** User intents the browser forwards; the brain decides what they mean. */
export type UIAction =
  | "begin"
  | "decline_call"
  | "gmail_connect"
  | "gmail_reopen"
  | "gmail_not_now"
  | "gmail_retry"
  | "gmail_skip"
  | "gmail_disconnect"
  | "gmail_keep"
  | "call_take_over";

export interface SessionDriver {
  snapshot(): SessionSnapshot;
  sendText(text: string): Promise<void>;
  startCall(): Promise<void>;
  endCall(): Promise<void>;
  setMuted(muted: boolean): Promise<void>;
  act(action: UIAction): Promise<void>;
  onPush(cb: (push: UIPush) => void): () => void;
  /** Fake/real agent output level 0..1 for the ring glow (not part of the snapshot). */
  onLevel?(cb: (level: number) => void): () => void;
  close(): void;
}

/** Pure render reducer: merges a push into the snapshot. It never picks a next node. */
export function applyPush(s: SessionSnapshot, p: UIPush): SessionSnapshot {
  switch (p.type) {
    case "snapshot":
      return p.snapshot;
    case "transcript":
      return { ...s, thread: [...s.thread, ...p.items] };
    case "state":
      return { ...s, checklist: p.checklist, justFilled: p.justFilled, composer: p.composer };
    case "gmail_connect_card": {
      const exists = s.thread.some((i) => i.id === p.itemId);
      const item: ThreadItem = { id: p.itemId, kind: "gmail", card: p.card };
      return {
        ...s,
        thread: exists ? s.thread.map((i) => (i.id === p.itemId ? item : i)) : [...s.thread, item],
      };
    }
    case "call_state":
      return { ...s, call: p.call };
    case "graduate":
      return { ...s, surface: "home", home: p.home, call: null };
  }
}
