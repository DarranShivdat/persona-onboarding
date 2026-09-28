"use client";
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import type { EditableSlot, EditResult, HomeView, ThreadItem, UIAction } from "@/lib/session/types";
import { CheckIcon, MailIcon, XIcon } from "./icons";

const DISMISS_KEY = "persona:home-dismissed";
const EDIT_LABEL: Record<EditableSlot, string> = { agent_name: "your assistant’s name", user_name: "your name", need: "what you’d like help with" };

type OnEdit = (slot: EditableSlot, value: string) => Promise<EditResult>;

/** Graduation / home (spec §4.6). Also the EC-31 return-visit landing. Renders the brain's
 * state; edits are forwarded to the agent, whose validators decide (GRAD-001). */
export function Home({ home, agent, onAct, onEdit }: { home: HomeView; agent: string; onAct: (a: UIAction) => void; onEdit?: OnEdit }) {
  // Dismissing only hides the prompt for this visit (view state, not session state).
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [editing, setEditing] = useState<EditableSlot | null>(null);
  useEffect(() => {
    try {
      setDismissed(JSON.parse(sessionStorage.getItem(DISMISS_KEY) ?? "[]") as string[]);
    } catch {
      // storage unavailable: dismissals last until the page unloads
    }
  }, []);
  const dismiss = (id: string) =>
    setDismissed((x) => {
      const next = [...x, id];
      try {
        sessionStorage.setItem(DISMISS_KEY, JSON.stringify(next));
      } catch {
        // ignore
      }
      return next;
    });
  const gmailPrompt = home.deferred.some((d) => d.slot === "gmail" && !dismissed.includes(d.id));
  const field = (slot: EditableSlot | undefined, value: string, className: string, empty?: boolean) =>
    slot && onEdit ? (
      <EditableValue slot={slot} value={value} empty={empty} className={className} editing={editing === slot} setEditing={setEditing} onEdit={onEdit} />
    ) : (
      <div className={className}>{value}</div>
    );

  return (
    <>
      <h1>You’re all set, {home.userName}.</h1>
      <p className="lede">{agent} is ready. Here’s what it knows so far.</p>
      <div className="grid">
        <div className="tile wide">
          <div className="k">{home.focus.label}</div>
          {field(home.focus.slot, home.focus.value, "v")}
          <p>{home.focus.detail}</p>
        </div>
        {home.tiles.map((t) => (
          <div key={t.label} className="tile">
            <div className="k">{t.label}</div>
            {field(t.slot, t.value, "v", t.empty)}
          </div>
        ))}
        {/* One Connect CTA at a time: while the deferred prompt offers it, an idle tile adds nothing. */}
        {home.gmail && (home.gmail.state !== "idle" || !gmailPrompt) && <GmailTile gmail={home.gmail} onAct={onAct} />}
      </div>
      {home.deferred
        .filter((d) => !dismissed.includes(d.id))
        .map((d) => (
          <div key={d.id} className="defer" role="group" aria-label="Suggested next step" data-testid="deferred-prompt">
            <div className="glyph">
              <MailIcon width={22} height={22} />
            </div>
            <div className="t">
              <b>{d.title}</b>
              <span>{d.reason}</span>
            </div>
            <button
              type="button"
              className="btn primary"
              onClick={() => (d.slot === "gmail" ? onAct("gmail_connect") : setEditing(d.slot))}
            >
              {d.action}
            </button>
            <button type="button" className="x" aria-label="Dismiss" onClick={() => dismiss(d.id)}>
              <XIcon />
            </button>
          </div>
        ))}
      <HomeThread items={home.thread ?? []} agent={agent} />
    </>
  );
}

function EditableValue({
  slot,
  value,
  empty,
  className,
  editing,
  setEditing,
  onEdit,
}: {
  slot: EditableSlot;
  value: string;
  empty?: boolean;
  className: string;
  editing: boolean;
  setEditing: (s: EditableSlot | null) => void;
  onEdit: OnEdit;
}) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);
  useEffect(() => {
    if (editing) {
      setDraft(empty ? "" : value);
      setError(null);
      requestAnimationFrame(() => input.current?.select());
    } else if (wasEditing.current) trigger.current?.focus();
    wasEditing.current = editing;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  const save = async () => {
    if (busy) return;
    if (!empty && draft.trim() === value) return setEditing(null);
    setBusy(true);
    const r = await onEdit(slot, draft);
    setBusy(false);
    if (r.ok) setEditing(null);
    else setError(r.message);
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void save();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setEditing(null);
    }
  };

  if (!editing) {
    return (
      <button
        ref={trigger}
        type="button"
        className={`${className} editable${empty ? " empty" : ""}`}
        aria-label={`Edit ${EDIT_LABEL[slot]}: ${value}`}
        data-testid={`edit-${slot}`}
        onClick={() => setEditing(slot)}
      >
        <span>{value}</span>
        <PencilIcon />
      </button>
    );
  }
  const errId = `edit-${slot}-error`;
  return (
    <form
      className="edit-field"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <input
        ref={input}
        className="edit-input"
        value={draft}
        maxLength={slot === "need" ? 200 : 50}
        aria-label={EDIT_LABEL[slot].charAt(0).toUpperCase() + EDIT_LABEL[slot].slice(1)}
        aria-invalid={!!error}
        aria-describedby={error ? errId : undefined}
        data-testid={`edit-${slot}-input`}
        onChange={(e) => {
          setDraft(e.target.value);
          setError(null);
        }}
        onKeyDown={onKey}
      />
      <div className="edit-actions">
        <button type="submit" className="btn primary sm" disabled={busy} aria-busy={busy}>
          Save
        </button>
        <button type="button" className="btn quiet sm" onClick={() => setEditing(null)}>
          Cancel
        </button>
      </div>
      {error && (
        <p className="edit-error" id={errId} role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function GmailTile({ gmail, onAct }: { gmail: NonNullable<HomeView["gmail"]>; onAct: (a: UIAction) => void }) {
  const copy = {
    connected: { value: "Connected", action: null },
    connecting: { value: "Waiting for Google…", action: { label: "Open Google again", act: "gmail_reopen" as UIAction } },
    error: { value: "Didn’t connect", action: { label: "Try again", act: "gmail_retry" as UIAction } },
    idle: { value: "Not connected", action: { label: "Connect Gmail", act: "gmail_connect" as UIAction } },
    wrong_account: { value: "Connected", action: null },
  }[gmail.state];
  return (
    <div className="tile gmail" data-testid="gmail-tile" data-state={gmail.state}>
      <div className="k">Gmail</div>
      <div className="v">
        {gmail.state === "connected" && <CheckIcon width={16} height={16} className="ok" />} {copy.value}
      </div>
      {gmail.email && <span className="sub">{gmail.email}</span>}
      {copy.action && (
        <button type="button" className="btn secondary sm" onClick={() => onAct(copy.action!.act)}>
          {copy.action.label}
        </button>
      )}
    </div>
  );
}

function HomeThread({ items, agent }: { items: ThreadItem[]; agent: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    ref.current?.lastElementChild?.scrollIntoView?.({ block: "nearest" });
  }, [items]);
  if (!items.length) return null;
  return (
    <div className="home-thread" role="log" aria-live="polite" aria-label={`Conversation with ${agent}`} ref={ref} data-testid="home-thread">
      {items.map((it) =>
        it.kind === "msg" ? (
          <div key={it.id} className={`msg ${it.from}`} data-from={it.from}>
            {it.text}
          </div>
        ) : it.kind === "stamp" ? (
          <div key={it.id} className="stamp">
            {it.text}
          </div>
        ) : null,
      )}
    </div>
  );
}

const PencilIcon = () => (
  <svg className="pencil" viewBox="0 0 24 24" width={16} height={16} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4z" />
    <path d="M13.5 6.5l4 4" />
  </svg>
);
