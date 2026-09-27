import type { GmailCard as Card, GmailCapability, UIAction } from "@/lib/session/types";
import { AlertIcon, CheckIcon, MailIcon } from "./icons";

const CAN: Record<GmailCapability, string> = { read: "read your email", organize: "organize your inbox", send: "send email" };

/** Gmail connect card (spec §4.5). `inCall` enables the compact variant on mobile (CSS). */
export function GmailCard({ card, agent, inCall, onAct }: { card: Card; agent: string; inCall: boolean; onAct: (a: UIAction) => void }) {
  const head = (title: string, sub: string | null, glyph: React.ReactNode) => (
    <div className="head">
      <div className="glyph">{glyph}</div>
      <div>
        <h3>{title}</h3>
        {sub && <div className="sub">{sub}</div>}
      </div>
    </div>
  );

  if (card.state === "idle" || card.state === "connecting") {
    const busy = card.state === "connecting";
    return (
      <div className={`gcard ${inCall ? "in-call" : ""}`} role="group" aria-label="Connect Gmail" data-testid="gmail-card" data-state={card.state}>
        {head("Connect Gmail", `So ${agent} can work in your inbox`, <MailIcon />)}
        <ul className="full">
          <li>
            <b>Read</b>
            <span>to see what needs your attention</span>
          </li>
          <li>
            <b>Organize</b>
            <span>labels, archive, mark as read, drafts</span>
          </li>
          <li>
            <b>Send</b>
            <span>only after you say OK</span>
          </li>
        </ul>
        <p className="full">Nothing is sent or changed without your OK. You sign in on Google, so {agent} never sees your password.</p>
        <p className="compact">{agent} will read, organize, draft, and send email for you. Nothing is sent or changed without your OK.</p>
        <div className="note">
          <b>Heads up:</b> this is a trial, so Google will say it hasn’t verified the app. Choose <b>Continue</b> to go on.
        </div>
        <div className="actions">
          {busy ? (
            <>
              <button type="button" className="btn primary" aria-busy="true" disabled>
                <span className="spinner" aria-hidden="true" />
                Waiting for Google…
              </button>
              <button type="button" className="btn quiet" onClick={() => onAct("gmail_reopen")}>
                Open the Google window again
              </button>
            </>
          ) : (
            <>
              <button type="button" className="btn primary" onClick={() => onAct("gmail_connect")}>
                Continue with Google
              </button>
              <button type="button" className="btn quiet" onClick={() => onAct("gmail_not_now")}>
                Not now
              </button>
            </>
          )}
        </div>
        <div className="fine full">You can disconnect Gmail anytime in Settings.</div>
      </div>
    );
  }

  if (card.state === "connected" || card.state === "wrong_account") {
    const acct = card.account;
    return (
      <div className="gcard ok" role="group" aria-label="Gmail connected" data-testid="gmail-card" data-state={card.state}>
        {head("Gmail connected", null, <CheckIcon />)}
        {acct && (
          <div className="acct">
            <div className="avatar" aria-hidden="true">
              {acct.initials}
            </div>
            <div className="who">
              <b>{acct.name}</b>
              <span>{acct.email}</span>
            </div>
          </div>
        )}
        {card.state === "wrong_account" ? (
          <>
            <p>Wrong account? Disconnect it and pick another one on Google.</p>
            <div className="actions">
              <button type="button" className="btn primary" onClick={() => onAct("gmail_disconnect")}>
                Use a different account
              </button>
              <button type="button" className="btn quiet" onClick={() => onAct("gmail_keep")}>
                Keep this one
              </button>
            </div>
          </>
        ) : (
          <>
            {!!card.missing?.length && (
              // Partial grant (ARCHITECTURE §11): connected, with the reduced capability stated plainly.
              <p className="limited" data-testid="gmail-limited">
                Connected without permission to {card.missing.map((c) => CAN[c]).join(" or ")}. {agent} can still do the rest. Reconnect anytime to allow it.
              </p>
            )}
            <div className="actions">
              {!!card.missing?.length && (
                <button type="button" className="btn quiet" onClick={() => onAct("gmail_retry")}>
                  Allow full access
                </button>
              )}
              <button type="button" className="btn quiet" onClick={() => onAct("gmail_disconnect")}>
                Not you? Use a different account
              </button>
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="gcard err" role="group" aria-label="Gmail not connected" data-testid="gmail-card" data-state="error">
      {head("Gmail isn’t connected yet", null, <AlertIcon />)}
      <div className="alert" role="alert">
        Google didn’t finish signing in. The window may have closed, or access wasn’t allowed.
      </div>
      <p>
        Try again and choose <b>Continue</b> on the “unverified app” screen, then <b>Allow</b>.
      </p>
      <div className="actions">
        <button type="button" className="btn primary" onClick={() => onAct("gmail_retry")}>
          Try again
        </button>
        <button type="button" className="btn quiet" onClick={() => onAct("gmail_skip")}>
          Skip for now
        </button>
      </div>
    </div>
  );
}
