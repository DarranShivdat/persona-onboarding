"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiSessionDriver } from "@/lib/session/api-driver";
import type { AgentState } from "@/lib/session/agent-state";
import { MockSessionDriver } from "@/lib/session/mock-driver";
import type { StateName } from "@/lib/session/fixtures";
import { applyPush, type SessionDriver, type UIAction } from "@/lib/session/types";
import { CallPanel } from "./CallPanel";
import { Composer } from "./Composer";
import { Home } from "./Home";
import { Ring } from "./Ring";
import { Thread } from "./Thread";
import { Checklist, Notice, TopBar } from "./TopBar";

/** Live mode: the real agent session resolved server-side from the httpOnly cookie. */
export interface LiveBoot {
  state: AgentState | null;
}

/** Renders whatever the driver's snapshot says. No transition logic lives here. */
export function App({ initialState, capture, live }: { initialState: StateName; capture: boolean; live?: LiveBoot }) {
  const driver: SessionDriver = useMemo(
    () => (live ? new ApiSessionDriver(live.state) : new MockSessionDriver(initialState, { fakeLevels: !capture })),
    // `live` is fixed for the page's lifetime; refresh re-resolves it on the server.
    [initialState, capture, !!live],
  );
  const [snap, setSnap] = useState(() => driver.snapshot());
  const [level, setLevel] = useState<number | undefined>(undefined);
  const [railHidden, setRailHidden] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setSnap(driver.snapshot());
    const off = driver.onPush((p) => setSnap((s) => applyPush(s, p)));
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const offLevel = !reduce && driver.onLevel ? driver.onLevel(setLevel) : undefined;
    return () => {
      off();
      offLevel?.();
    };
  }, [driver]);
  useEffect(() => () => driver.close(), [driver]);

  // "Keep typing" collapses the ended panel (layout only); a new call shows it again.
  const callStatus = snap.call?.status;
  useEffect(() => {
    if (callStatus && callStatus !== "ended" && callStatus !== "elsewhere") setRailHidden(false);
  }, [callStatus]);

  const agent = snap.agentName ?? "your assistant";
  const act = (a: UIAction) => void driver.act(a);
  const send = (t: string) => void driver.sendText(t);
  const call = () => {
    setRailHidden(false); // layout only: a collapsed rail comes back when the user calls
    void driver.startCall();
  };

  if (snap.surface === "landing") {
    return (
      <div className="app" data-surface="landing">
        <TopBar />
        {snap.notice && <Notice notice={snap.notice} onAct={act} />}
        <main className="landing">
          <div className="copy">
            <h1>Meet the assistant that gets things done.</h1>
            <p className="lede">Give it a name, tell it what you need, and connect Gmail. Text or talk, whichever you like.</p>
            <button type="button" className="btn primary lg" onClick={() => act("begin")}>
              Get started
            </button>
            <div className="fine">About two minutes. You can stop anytime.</div>
          </div>
          <div className="art" aria-hidden="true">
            <div className="plate">
              <Ring />
            </div>
          </div>
        </main>
      </div>
    );
  }

  if (snap.surface === "home" && snap.home) {
    return (
      <div className="app" data-surface="home">
        <TopBar right={snap.home.userName} />
        <main className="home">
          <Home home={snap.home} agent={agent} onAct={act} onEdit={driver.editSlot ? (slot, v) => driver.editSlot!(slot, v) : undefined} />
          <Composer ref={composerRef} placeholder={snap.composer.placeholder} onSend={send} />
        </main>
      </div>
    );
  }

  const showRail = !!snap.call && !railHidden;
  const liveCall = !!snap.call && snap.call.status !== "ended" && snap.call.status !== "elsewhere";
  return (
    <div className="app" data-surface="chat">
      <TopBar checklist={snap.checklist} just={snap.justFilled} right="Setting up" />
      <Checklist items={snap.checklist} just={snap.justFilled} variant="mob" />
      {snap.notice && <Notice notice={snap.notice} onAct={act} />}
      <main className={`main ${showRail ? "with-call" : ""}`}>
        <section className="chat" aria-label="Chat">
          <Thread items={snap.thread} agent={agent} inCall={liveCall} onChip={send} onCall={call} onAct={act} />
          <Composer
            ref={composerRef}
            placeholder={snap.composer.placeholder}
            callLabel={snap.composer.callButton && !liveCall ? snap.agentName : null}
            onSend={send}
            onCall={call}
          />
        </section>
        {showRail && snap.call && (
          <CallPanel
            call={snap.call}
            agent={agent}
            level={level}
            onMute={(m) => void driver.setMuted(m)}
            onEnd={() => void driver.endCall()}
            onTypeInstead={() => composerRef.current?.focus()}
            onCallAgain={call}
            onKeepTyping={() => setRailHidden(true)}
            onTakeOver={() => act("call_take_over")}
          />
        )}
      </main>
    </div>
  );
}
