// Fixed snapshots for every spec state (docs/design/spec.md §8, mockups/index.html sample
// content). Used by MockSessionDriver (`?state=<name>`) and harness/visual capture.
import type { Checklist, ChecklistItem, SessionSnapshot, ThreadItem, CallView, GmailCard } from "./types";

export const GATED_STATES = [
  "landing",
  "chat-agent-name",
  "call-offer",
  "call-ringing",
  "call-connected",
  "call-reconnecting",
  "gmail-card-idle",
  "gmail-card-connected",
  "gmail-card-error",
  "graduation",
  "welcome-back",
] as const;
export const EXTRA_STATES = [
  "call-muted",
  "call-ended",
  "gmail-card-connecting",
  "gmail-card-wrong-account",
  "mic-denied",
] as const;
/** Mock-only replies (not mockup-gated). */
export const MOCK_STATES = ["call-declined", "call-elsewhere", "gmail-card-partial"] as const;
export type StateName = (typeof GATED_STATES)[number] | (typeof EXTRA_STATES)[number] | (typeof MOCK_STATES)[number];

let seq = 0;
const id = () => `m${++seq}`;
const stamp = (text: string): ThreadItem => ({ id: id(), kind: "stamp", text });
const divider = (text: string): ThreadItem => ({ id: id(), kind: "divider", text });
const a = (text: string): ThreadItem => ({ id: id(), kind: "msg", from: "agent", text });
const u = (text: string): ThreadItem => ({ id: id(), kind: "msg", from: "user", text });
const av = (text: string): ThreadItem => ({ id: id(), kind: "msg", from: "agent", text, voice: true });
const uv = (text: string): ThreadItem => ({ id: id(), kind: "msg", from: "user", text, voice: true });
const gmail = (card: GmailCard): ThreadItem => ({ id: "gmail-card", kind: "gmail", card });

const EMPTY: ChecklistItem = { status: "empty" };
const filled = (value: string): ChecklistItem => ({ status: "filled", value });
function checklist(c: Partial<Checklist> = {}): Checklist {
  return { agent_name: EMPTY, user_name: EMPTY, need: EMPTY, gmail: EMPTY, ...c };
}
const A = filled("Juno");
const U = filled("Maya");
const N = filled("Inbox triage");
const MAYA = { name: "Maya Reyes", email: "maya.r@gmail.com", initials: "MR" };

const earlyThread = () => [
  stamp("Today 9:41 AM"),
  a("Hi! I’m your new assistant. I’ll help with email, your calendar, and the everyday stuff."),
  a("First things first: what would you like to call me?"),
  u("Juno"),
  a("Juno it is. I like it."),
];
const offer = (): ThreadItem => ({
  id: "offer",
  kind: "offer",
  title: "Talk it through with Juno",
  subtitle: "About a minute. Typing works too.",
  primary: "Call Juno",
  secondary: "Keep texting",
  secondaryAction: "decline_call",
});
const callThread = () => [
  ...earlyThread(),
  a("The rest is easier out loud. Want to hop on a quick call?"),
  divider("Call started · 9:43 AM"),
];
const call = (c: Partial<CallView> & Pick<CallView, "status" | "ring" | "captions">): CallView => ({ elapsed: 0, ...c });

const base = (over: Partial<SessionSnapshot>): SessionSnapshot => ({
  surface: "chat",
  agentName: "Juno",
  checklist: checklist(),
  justFilled: null,
  thread: [],
  composer: { placeholder: "Message Juno…", callButton: false },
  call: null,
  home: null,
  ...over,
});
const inCall = { placeholder: "Type instead of talking…", callButton: false };
const texting = { placeholder: "Message Juno…", callButton: true };

const BUILDERS: Record<StateName, () => SessionSnapshot> = {
  landing: () => base({ surface: "landing", agentName: null }),

  "chat-agent-name": () =>
    base({
      agentName: null,
      thread: [
        stamp("Today 9:41 AM"),
        a("Hi! I’m your new assistant. I’ll help with email, your calendar, and the everyday stuff."),
        a("First things first: what would you like to call me?"),
        { id: id(), kind: "chips", options: ["Juno", "Atlas", "Surprise me"] },
      ],
      composer: { placeholder: "Type a name…", callButton: false },
    }),

  "call-offer": () =>
    base({
      checklist: checklist({ agent_name: A }),
      justFilled: "agent_name",
      thread: [
        ...earlyThread(),
        a("The rest is easier out loud. Want to hop on a quick call? Or we can keep texting, totally fine either way."),
        offer(),
      ],
      composer: texting,
    }),

  "call-declined": () =>
    base({
      checklist: checklist({ agent_name: A }),
      thread: [
        ...earlyThread(),
        a("The rest is easier out loud. Want to hop on a quick call? Or we can keep texting, totally fine either way."),
        offer(),
        u("Keep texting"),
        a("Texting it is. What should I call you?"),
      ],
      composer: texting,
    }),

  "call-ringing": () =>
    base({
      checklist: checklist({ agent_name: A }),
      thread: callThread(),
      composer: { placeholder: "Message Juno…", callButton: false },
      call: call({
        status: "ringing",
        ring: "ringing",
        captions: [{ who: "", text: "Captions appear here when Juno answers.", tone: "hint" }],
      }),
    }),

  "call-connected": () =>
    base({
      checklist: checklist({ agent_name: A, need: N }),
      justFilled: "need",
      thread: [
        ...callThread(),
        av("Hey, it’s Juno! What should I call you?"),
        uv("Honestly I just need help with my inbox, it’s a disaster."),
        av("Got it, inbox rescue is on the list. And your name?"),
      ],
      composer: inCall,
      call: call({
        status: "connected",
        elapsed: 42,
        ring: "speaking",
        captions: [
          { who: "You", text: "Honestly I just need help with my inbox, it’s a disaster." },
          { who: "Juno", text: "Got it, inbox rescue is on the list. And your name?", live: true },
        ],
      }),
    }),

  "call-muted": () =>
    base({
      checklist: checklist({ agent_name: A, need: N }),
      thread: [...callThread(), av("Got it, inbox rescue is on the list. And your name?")],
      composer: inCall,
      call: call({
        status: "muted",
        elapsed: 58,
        ring: "muted",
        badge: "You’re muted",
        captions: [{ who: "Juno", text: "Got it, inbox rescue is on the list. And your name?" }],
      }),
    }),

  "call-reconnecting": () =>
    base({
      checklist: checklist({ agent_name: A, user_name: U, need: N }),
      thread: [...callThread(), av("Nice to meet you, Maya."), uv("Mostly I want the junk out of the way…")],
      composer: { placeholder: "Type while we reconnect…", callButton: false },
      call: call({
        status: "reconnecting",
        elapsed: 71,
        ring: "reconnecting",
        badge: "Your progress is saved",
        captions: [
          { who: "You", text: "Mostly I want the junk out of the way…" },
          { who: "", text: "The line dropped. Trying to reconnect.", tone: "warn" },
        ],
      }),
    }),

  "call-ended": () =>
    base({
      checklist: checklist({ agent_name: A, user_name: U, need: N }),
      thread: [
        ...callThread(),
        av("Nice to meet you, Maya."),
        divider("Call ended · 1:36"),
        a("We got cut off, no worries. I still have your name and what you need. Only Gmail is left. Call back, or connect it right here?"),
      ],
      composer: texting,
      call: call({ status: "ended", elapsed: 96, ring: "ended", captions: [{ who: "Juno", text: "Nice to meet you, Maya." }] }),
    }),

  "call-elsewhere": () =>
    base({
      checklist: checklist({ agent_name: A }),
      thread: earlyThread(),
      composer: texting,
      call: call({ status: "elsewhere", elapsed: 0, ring: "ended", captions: [{ who: "", tone: "hint", text: "This call is open in another tab. Take it over here, or keep typing." }] }),
    }),

  "gmail-card-idle": () =>
    base({
      checklist: checklist({ agent_name: A, user_name: U, need: N }),
      thread: [
        ...callThread(),
        av("Nice to meet you, Maya. To help with your inbox I’ll need Gmail. I just put a button on your screen."),
        gmail({ state: "idle" }),
      ],
      composer: inCall,
      call: call({
        status: "connected",
        elapsed: 84,
        ring: "speaking",
        captions: [
          { who: "You", text: "Maya." },
          { who: "Juno", text: "To help with your inbox I’ll need Gmail. I just put a button on your screen.", live: true },
        ],
      }),
    }),

  "gmail-card-connecting": () =>
    base({
      checklist: checklist({ agent_name: A, user_name: U, need: N }),
      thread: [...callThread(), av("I just put a button on your screen."), gmail({ state: "connecting" })],
      composer: inCall,
      call: call({
        status: "connected",
        elapsed: 99,
        ring: "idle",
        captions: [{ who: "Juno", text: "Take your time. I’ll hang on while you sign in with Google." }],
      }),
    }),

  "gmail-card-connected": () =>
    base({
      checklist: checklist({ agent_name: A, user_name: U, need: N, gmail: filled("maya.r@gmail.com") }),
      justFilled: "gmail",
      thread: [
        ...callThread(),
        av("I just put a button on your screen."),
        gmail({ state: "connected", account: MAYA }),
        av("Got it, connected as maya.r@gmail.com."),
      ],
      composer: inCall,
      call: call({
        status: "connected",
        elapsed: 125,
        ring: "speaking",
        captions: [
          { who: "Juno", text: "Got it, connected as maya.r at gmail dot com. That’s everything I need.", live: true },
        ],
      }),
    }),

  // Partial grant (ARCHITECTURE §11): the user unticked "send" on Google's consent screen.
  "gmail-card-partial": () => {
    const s = fixture("gmail-card-connected");
    return { ...s, thread: s.thread.map((i) => (i.kind === "gmail" ? gmail({ state: "connected", account: MAYA, missing: ["send"] }) : i)) };
  },

  "gmail-card-error": () =>
    base({
      checklist: checklist({ agent_name: A, user_name: U, need: N }),
      thread: [
        stamp("Today 9:47 AM"),
        a("To help with your inbox I’ll need Gmail."),
        gmail({ state: "error" }),
        a("Looks like Google didn’t finish. Want to try again, or skip it for now? You can always connect later."),
      ],
      composer: texting,
    }),

  "gmail-card-wrong-account": () =>
    base({
      checklist: checklist({ agent_name: A, user_name: U, need: N, gmail: filled("maya@work.co") }),
      thread: [
        stamp("Today 9:48 AM"),
        u("oops, that’s my work account"),
        a("No problem. Here’s the account I have. Switch it whenever you’re ready."),
        gmail({ state: "wrong_account", account: { name: "Maya R.", email: "maya@work.co", initials: "MR" } }),
      ],
      composer: { placeholder: "Message Juno…", callButton: false },
    }),

  "mic-denied": () =>
    base({
      checklist: checklist({ agent_name: A }),
      thread: [
        ...earlyThread(),
        offer(),
        u("Call Juno"),
        a(
          "Your browser’s blocking the mic, so I can’t hear you. Allow microphone access for this site in your browser’s settings, then call again.",
        ),
        a("Or we can just keep texting. So, what should I call you?"),
      ],
      composer: texting,
    }),

  graduation: () =>
    base({
      surface: "home",
      checklist: checklist({ agent_name: A, user_name: U, need: N, gmail: { status: "deferred" } }),
      composer: { placeholder: "Message Juno…", callButton: false },
      home: {
        userName: "Maya",
        focus: {
          label: "Juno is starting with",
          value: "Getting your inbox under control",
          detail:
            "First up: a short list of what needs a reply, and the junk ready to archive. Nothing gets sent or changed without your OK.",
          slot: "need",
        },
        tiles: [
          { label: "Your assistant", value: "Juno", slot: "agent_name" },
          { label: "You", value: "Maya", slot: "user_name" },
        ],
        gmail: { state: "idle" },
        thread: [],
        deferred: [
          {
            id: "defer-gmail",
            slot: "gmail",
            title: "Connect Gmail when you’re ready",
            reason: "Juno needs it to sort your inbox. Takes a minute.",
            action: "Connect",
          },
        ],
      },
    }),

  "welcome-back": () =>
    base({
      checklist: checklist({ agent_name: A, user_name: U, need: N }),
      thread: [
        stamp("Yesterday 9:41 AM"),
        a("Got it, inbox rescue is on the list."),
        av("Nice to meet you, Maya."),
        divider("Call ended · 1:36"),
        stamp("Today 8:15 AM"),
        a("Welcome back, Maya! We’re nearly done. Just Gmail left, so I can start on that inbox."),
        {
          id: "offer",
          kind: "offer",
          title: "Finish on a quick call",
          subtitle: "Under a minute. Or connect right here.",
          primary: "Call Juno",
          secondary: "Connect Gmail here",
          secondaryAction: "gmail_connect",
        },
      ],
      composer: texting,
    }),
};

export function isStateName(s: string | null | undefined): s is StateName {
  return !!s && Object.prototype.hasOwnProperty.call(BUILDERS, s);
}

export function fixture(name: StateName): SessionSnapshot {
  seq = 0;
  return BUILDERS[name]();
}
