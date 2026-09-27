"use client";
import { useState } from "react";
import type { HomeView, UIAction } from "@/lib/session/types";
import { MailIcon, XIcon } from "./icons";

/** Graduation / home (spec §4.6). Also the EC-31 return-visit landing. */
export function Home({ home, agent, onAct }: { home: HomeView; agent: string; onAct: (a: UIAction) => void }) {
  // Dismissing only hides the prompt for this visit (view state, not session state).
  const [dismissed, setDismissed] = useState<string[]>([]);
  return (
    <>
      <h1>You’re all set, {home.userName}.</h1>
      <p className="lede">{agent} is ready. Here’s what it knows so far.</p>
      <div className="grid">
        <div className="tile wide">
          <div className="k">{home.focus.label}</div>
          <div className="v">{home.focus.value}</div>
          <p>{home.focus.detail}</p>
        </div>
        {home.tiles.map((t) => (
          <div key={t.label} className="tile">
            <div className="k">{t.label}</div>
            <div className="v">{t.value}</div>
          </div>
        ))}
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
            <button type="button" className="btn primary" onClick={() => onAct("gmail_connect")}>
              {d.action}
            </button>
            <button type="button" className="x" aria-label="Dismiss" onClick={() => setDismissed((x) => [...x, d.id])}>
              <XIcon />
            </button>
          </div>
        ))}
    </>
  );
}
