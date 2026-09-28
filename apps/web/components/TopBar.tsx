import type { Checklist as ChecklistT, SessionSnapshot, UIAction } from "@/lib/session/types";
import type { SlotName } from "@/lib/flow-types";

const ITEMS: { slot: SlotName; long: string; short: string }[] = [
  { slot: "agent_name", long: "Assistant name", short: "Assistant" },
  { slot: "user_name", long: "Your name", short: "You" },
  { slot: "need", long: "What you need", short: "Need" },
  { slot: "gmail", long: "Gmail", short: "Gmail" },
];

/** Implicit progress: 4 status items, never a stepper (spec §4.1). Not interactive. */
export function Checklist({ items, just, variant }: { items: ChecklistT; just: SlotName | null; variant: "desk" | "mob" }) {
  return (
    <ul className={`checklist ${variant}-only`} aria-label="Setup progress" data-testid={`checklist-${variant}`}>
      {ITEMS.map(({ slot, long, short }) => {
        const it = items[slot];
        const val = it.status === "deferred" ? "Later" : it.status === "filled" ? (it.value ?? "") : "Not yet";
        const cls = it.status === "deferred" ? "deferred" : it.status === "filled" ? "done" : "";
        return (
          <li key={slot} aria-label={`${long}: ${val}`} className={`check ${cls} ${just === slot ? "just" : ""}`} data-slot={slot}>
            <div className="k" aria-hidden="true">
              <span className="dot" />
              {variant === "desk" ? long : short}
            </div>
            <div className="v" aria-hidden="true">
              {val}
            </div>
            <div className="bar" aria-hidden="true">
              <i />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function TopBar({ checklist, just, right }: { checklist?: ChecklistT; just?: SlotName | null; right?: string }) {
  return (
    <header className="topbar">
      <span className="wordmark">Persona</span>
      {(checklist || right) && <span className="spacer desk-only" />}
      {checklist && <Checklist items={checklist} just={just ?? null} variant="desk" />}
      {checklist && <span className="spacer desk-only" />}
      {right && <span className="help">{right}</span>}
    </header>
  );
}

/** Agent unreachable: say so plainly and offer a retry (never a dead button). */
export function Notice({ notice, onAct }: { notice: NonNullable<SessionSnapshot["notice"]>; onAct: (a: UIAction) => void }) {
  return (
    <div className="notice" role="alert" data-testid="notice">
      <span>{notice.text}</span>
      <button type="button" className="btn secondary" onClick={() => onAct(notice.action)}>
        {notice.label}
      </button>
    </div>
  );
}
