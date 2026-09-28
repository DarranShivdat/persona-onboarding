"use client";
import { useEffect, useRef, useState } from "react";

/** RESET-001: header "Start over". A confirm step guards against a stray tap; confirming clears
 * the session cookie server-side and reloads into a fresh session (agent_name step). */
export function StartOver() {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const close = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };
  const confirm = async () => {
    setBusy(true);
    try {
      await fetch("/api/session/reset", { method: "POST", cache: "no-store" });
    } catch {
      /* the reload below still lands somewhere sensible (landing) */
    }
    window.location.assign("/");
  };

  return (
    <div className="startover">
      <button ref={triggerRef} type="button" className="btn secondary sm" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)} data-testid="start-over">
        Start over
      </button>
      {open && (
        <div className="startover-pop" role="alertdialog" aria-modal="false" aria-labelledby="startover-title" aria-describedby="startover-desc" data-testid="start-over-confirm">
          <p id="startover-title" className="t">Start over?</p>
          <p id="startover-desc" className="d">This clears your answers and chat on this device and starts a fresh setup.</p>
          <div className="row">
            <button ref={cancelRef} type="button" className="btn secondary" onClick={close} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="btn primary" onClick={() => void confirm()} disabled={busy}>
              {busy ? "Starting over…" : "Yes, start over"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
