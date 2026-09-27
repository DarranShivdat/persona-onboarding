"use client";
import { useLayoutEffect, useRef } from "react";
import type { ThreadItem, UIAction } from "@/lib/session/types";
import { GmailCard } from "./GmailCard";
import { PhoneIcon } from "./icons";
import { Ring } from "./Ring";

interface Props {
  items: ThreadItem[];
  agent: string;
  inCall: boolean;
  onChip: (text: string) => void;
  onCall: () => void;
  onAct: (a: UIAction) => void;
}

/** The single transcript (text + mirrored voice turns). role=log, aria-live=polite. */
export function Thread({ items, agent, inCall, onChip, onCall, onAct }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items]);

  return (
    <div className="thread" role="log" aria-live="polite" aria-label="Conversation" ref={ref} data-testid="thread">
      {items.map((it) => {
        switch (it.kind) {
          case "stamp":
            return (
              <div key={it.id} className="stamp">
                {it.text}
              </div>
            );
          case "divider":
            return (
              <div key={it.id} className="divider" role="separator">
                {it.text}
              </div>
            );
          case "msg":
            return (
              <div key={it.id} className={`msg ${it.from} ${it.voice ? "voice" : ""}`} data-from={it.from}>
                {it.text}
              </div>
            );
          case "chips":
            return (
              <div key={it.id} className="chips">
                {it.options.map((o) => (
                  <button key={o} type="button" className="chip" onClick={() => onChip(o)}>
                    {o}
                  </button>
                ))}
              </div>
            );
          case "offer":
            return (
              <div key={it.id} className="offer" role="group" aria-label={it.title} data-testid="call-offer">
                <Ring />
                <div>
                  <h4>{it.title}</h4>
                  <p>{it.subtitle}</p>
                </div>
                <div className="row">
                  <button type="button" className="btn primary" onClick={onCall}>
                    <PhoneIcon width={16} height={16} />
                    {it.primary}
                  </button>
                  <button
                    type="button"
                    className="btn secondary"
                    onClick={() => onAct(it.secondaryAction)}
                  >
                    {it.secondary}
                  </button>
                </div>
              </div>
            );
          case "gmail":
            return <GmailCard key={it.id} card={it.card} agent={agent} inCall={inCall} onAct={onAct} />;
        }
      })}
    </div>
  );
}
