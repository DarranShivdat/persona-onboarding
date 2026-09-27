// Agent API wire shapes (services/agent/agent/api/README.md) and the pure render mapping
// from the brain's snapshot to what the UI draws. Rendering only: nothing here decides a
// node, fills a slot or picks what happens next — the brain already did that.
import { SLOT_NAMES, type NodeId, type SlotName, type SlotStatus } from "@/lib/flow-types";
import type { Checklist, HomeView, SessionSnapshot, ThreadItem } from "./types";

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
  need: (a) => ({ title: "Tell me what to start on", reason: `One thing ${a} can take off your plate.`, action: "Add" }),
  user_name: (a) => ({ title: "Tell me your name", reason: `So ${a} knows who it’s working for.`, action: "Add" }),
  agent_name: () => ({ title: "Name your assistant", reason: "Pick whatever feels right.", action: "Add" }),
};

export function toHome(s: AgentState): HomeView {
  const agent = agentNameOf(s) ?? "Your assistant";
  const user = s.slots.user_name?.status === "filled" ? (s.slots.user_name.value ?? "") : "";
  const need = s.slots.need?.status === "filled" ? (s.slots.need.value ?? "") : "";
  return {
    userName: user || "there",
    focus: {
      label: `${agent} is starting with`,
      value: need || "Getting to know you",
      detail: "Nothing gets sent or changed without your OK.",
    },
    tiles: [
      { label: "Your assistant", value: agent },
      ...(user ? [{ label: "You", value: user }] : []),
    ],
    deferred: s.deferred_prompts.map((slot) => ({ id: `defer-${slot}`, slot, ...DEFER_COPY[slot](agent) })),
  };
}

/** Offer card drawn while the brain sits in `call_offer` (its buttons only forward intents). */
export function offerItem(agent: string): ThreadItem {
  return {
    id: "offer",
    kind: "offer",
    title: `Talk it through with ${agent}`,
    subtitle: "A quick call, about two minutes.",
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
