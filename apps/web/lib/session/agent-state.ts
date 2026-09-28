// Agent API wire shapes (services/agent/agent/api/README.md) and the pure render mapping
// from the brain's snapshot to what the UI draws. Rendering only: nothing here decides a
// node, fills a slot or picks what happens next — the brain already did that.
import { SLOT_NAMES, type NodeId, type SlotName, type SlotStatus } from "@/lib/flow-types";
import type { Checklist, GmailCardState, HomeView, SessionSnapshot, ThreadItem } from "./types";

export interface AgentSlot {
  status: SlotStatus;
  value: string | null;
  source: string | null;
  needs_confirm: boolean;
}

/** `GET /v1/sessions/{id}` and the SSE `state` push. */
export interface AgentState {
  id: string;
  version: number;
  status: "active" | "graduated";
  node: NodeId;
  active_channel: "text" | "voice" | null;
  slots: Record<SlotName, AgentSlot>;
  deferred_prompts: SlotName[];
  graduated: boolean;
  call: { live: boolean; call_id: string | null; expires_at: string | null };
}

/** SSE `transcript` push data. */
export interface AgentTranscript {
  role: "user" | "assistant";
  text: string;
  channel: "text" | "voice";
  /** Optional reply suggestions for the ask in `text` (agent_name only); rendered as chips. */
  suggestions?: string[];
}

export function toChecklist(s: AgentState): Checklist {
  const out = {} as Checklist;
  for (const name of SLOT_NAMES) {
    const slot = s.slots[name];
    if (slot?.status === "filled") out[name] = { status: "filled", value: slot.value ?? undefined };
    else if (slot?.status === "skipped" || s.deferred_prompts.includes(name)) out[name] = { status: "deferred" };
    else out[name] = { status: "empty" };
  }
  return out;
}

export function agentNameOf(s: AgentState): string | null {
  const a = s.slots.agent_name;
  return a?.status === "filled" && a.value ? a.value : null;
}

export function composerFor(s: AgentState): SessionSnapshot["composer"] {
  const agent = agentNameOf(s);
  if (s.node === "agent_name" || !agent) return { placeholder: "Type a name…", callButton: false };
  return { placeholder: `Message ${agent}…`, callButton: !s.graduated };
}

const DEFER_COPY: Record<SlotName, (agent: string) => { title: string; reason: string; action: string }> = {
  gmail: (a) => ({ title: "Connect Gmail when you’re ready", reason: `${a} needs it to sort your inbox. Takes a minute.`, action: "Connect" }),
  need: (a) => ({ title: `Tell ${a} what to start on`, reason: `One thing ${a} can take off your plate.`, action: "Add" }),
  user_name: (a) => ({ title: `Tell ${a} your name`, reason: `So ${a} knows who it’s working for.`, action: "Add" }),
  agent_name: () => ({ title: "Name your assistant", reason: "Pick whatever feels right.", action: "Add" }),
};

export function toHome(s: AgentState, extra: { gmail?: GmailCardState; thread?: ThreadItem[] } = {}): HomeView {
  const agent = agentNameOf(s) ?? "Your assistant";
  const user = s.slots.user_name?.status === "filled" ? (s.slots.user_name.value ?? "") : "";
  const need = s.slots.need?.status === "filled" ? (s.slots.need.value ?? "") : "";
  const gmail = s.slots.gmail?.status === "filled" ? s.slots.gmail.value ?? undefined : undefined;
  // Rendering only: the tile shows the brain's gmail slot; the card state only covers an attempt in flight.
  const gmailState: GmailCardState = gmail ? "connected" : extra.gmail === "connecting" || extra.gmail === "error" ? extra.gmail : "idle";
  return {
    userName: user || "there",
    focus: {
      label: `${agent} is starting with`,
      value: need || "Getting to know you",
      detail: "Nothing gets sent or changed without your OK.",
      slot: "need",
    },
    tiles: [
      { label: "Your assistant", value: agentNameOf(s) ?? "Name your assistant", slot: "agent_name", empty: !agentNameOf(s) },
      { label: "You", value: user || "Add your name", slot: "user_name", empty: !user },
    ],
    gmail: { state: gmailState, email: gmail },
    deferred: s.deferred_prompts.map((slot) => ({ id: `defer-${slot}`, slot, ...DEFER_COPY[slot](agent) })),
    thread: extra.thread ?? [],
  };
}

/** Offer card drawn while the brain sits in `call_offer` (its buttons only forward intents). */
export function offerItem(agent: string): ThreadItem {
  return {
    id: "offer",
    kind: "offer",
    title: `Talk it through with ${agent}`,
    subtitle: "About a minute. Typing works too.",
    primary: `Call ${agent}`,
    secondary: "Keep texting",
    secondaryAction: "decline_call",
  };
}

export const EMPTY_CHECKLIST: Checklist = { agent_name: { status: "empty" }, user_name: { status: "empty" }, need: { status: "empty" }, gmail: { status: "empty" } };

export function landingSnapshot(): SessionSnapshot {
  return {
    surface: "landing",
    agentName: null,
    checklist: EMPTY_CHECKLIST,
    justFilled: null,
    thread: [],
    composer: { placeholder: "Type a name…", callButton: false },
    call: null,
    home: null,
  };
}

/** Avatar initials for the Gmail account row ("Maya Reyes" -> "MR", "maya.r@gmail.com" -> "MA"). */
export function initialsOf(name: string, email: string): string {
  const words = name.includes("@") ? [] : name.trim().split(/\s+/).filter(Boolean);
  const s = words.length >= 2 ? words[0]![0]! + words[words.length - 1]![0]! : (words[0] ?? email).slice(0, 2);
  return s.toUpperCase();
}
