"use client";
import { useEffect, useState } from "react";
import type { CallView } from "@/lib/session/types";
import { EndIcon, KeysIcon, MicIcon, MicOffIcon, PhoneIcon } from "./icons";
import { Ring } from "./Ring";

interface Props {
  call: CallView;
  agent: string;
  level: number | undefined;
  onMute: (muted: boolean) => void;
  onEnd: () => void;
  onTypeInstead: () => void;
  onCallAgain: () => void;
  onKeepTyping: () => void;
  onTakeOver: () => void;
}

export const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

const ANNOUNCE: Record<CallView["status"], string> = {
  ringing: "Calling",
  connected: "Call connected",
  muted: "Call connected, you're muted",
  reconnecting: "Reconnecting. Your progress is saved.",
  ended: "Call ended",
  elsewhere: "On a call in another tab",
};

/** The timer ticks locally for display only; the brain owns call state. */
function useTimer(call: CallView) {
  const live = call.status === "connected" || call.status === "muted";
  const [secs, setSecs] = useState(call.elapsed);
  useEffect(() => setSecs(call.elapsed), [call.elapsed]);
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [live]);
  return secs;
}

/** Phone simulator (spec §4.4): device on the desktop rail, stacked panel on mobile. */
export function CallPanel({ call, agent, level, onMute, onEnd, onTypeInstead, onCallAgain, onKeepTyping, onTakeOver }: Props) {
  const secs = useTimer(call);
  const status =
    call.status === "ringing"
      ? "Calling…"
      : call.status === "reconnecting"
        ? "Reconnecting…"
        : call.status === "ended"
          ? `Call ended · ${fmt(call.elapsed)}`
          : call.status === "elsewhere"
            ? "On a call in another tab"
            : fmt(secs);
  const muted = call.status === "muted";
  const ringing = call.status === "ringing";
  const done = call.status === "ended" || call.status === "elsewhere";

  return (
    <aside className="rail" aria-label={`Call with ${agent}`} data-testid="call-panel" data-status={call.status}>
      <div className="device">
        <div className="who">
          <div className="name">{agent}</div>
          <div className={`status ${call.status === "reconnecting" ? "warn" : ""}`} data-testid="call-status" data-mask="timer">
            {status}
          </div>
          {call.badge && <div className="badge">{call.badge}</div>}
        </div>
        <div className="stage" data-mask="ring">
          <Ring state={call.ring} level={call.ring === "speaking" ? level : undefined} />
        </div>
        <div className="captions" aria-live="polite" aria-label="Live captions" data-mask="captions">
          <div className="label">
            <span>Live captions</span>
          </div>
          {call.captions.map((c, i) => (
            <div key={i} className={`cap ${c.live ? "live" : ""} ${c.tone ?? ""}`}>
              <b>{c.who}</b>
              {c.tone ? <span>{c.text}</span> : c.text}
            </div>
          ))}
        </div>
        <div className="controls">
          {done ? (
            <>
              <button type="button" className="ctl accept" onClick={call.status === "elsewhere" ? onTakeOver : onCallAgain}>
                <span className="b">
                  <PhoneIcon />
                </span>
                {call.status === "elsewhere" ? "Take over here" : "Call again"}
              </button>
              <button type="button" className="ctl" onClick={onKeepTyping}>
                <span className="b">
                  <KeysIcon />
                </span>
                Keep typing
              </button>
            </>
          ) : (
            <>
              <button type="button" className={`ctl ${muted ? "on" : ""}`} aria-pressed={muted} disabled={ringing} onClick={() => onMute(!muted)}>
                <span className="b">{muted ? <MicOffIcon /> : <MicIcon />}</span>
                {muted ? "Unmute" : "Mute"}
              </button>
              <button type="button" className="ctl" onClick={onTypeInstead}>
                <span className="b">
                  <KeysIcon />
                </span>
                Type instead
              </button>
              <button type="button" className="ctl end" onClick={onEnd}>
                <span className="b">
                  <EndIcon />
                </span>
                {ringing ? "Cancel" : "End"}
              </button>
            </>
          )}
        </div>
      </div>
      <div className="sr-only" role="status">
        {ANNOUNCE[call.status]}
      </div>
    </aside>
  );
}
