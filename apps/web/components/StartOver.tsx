"use client";
import { useEffect, useRef, useState } from "react";

/** Fired on confirm so a live call is ended cleanly before the page navigates away. */
export const START_OVER_EVENT = "persona:start-over";

/** RESET-001: header "Start over". A confirm step guards against a stray tap; confirming clears
 * the session cookie server-side and lands on the landing step; the new session starts lazily. */
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
  const confirm = () => {
    // RESET-002: < 1 s. Hang up any live call now (the driver hands the lease back with a
    // keepalive request), then navigate straight to the reset route: it only expires the
    // cookie and 303s to "/", where the next session is created lazily on the first message.
    setBusy(true);
    window.dispatchEvent(new Event(START_OVER_EVENT));
    window.location.assign("/api/session/reset");
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
            <button type="button" className="btn primary" onClick={confirm} disabled={busy}>
              {busy ? "Starting over…" : "Yes, start over"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
