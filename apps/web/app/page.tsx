import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { App } from "@/components/App";
import { isStateName } from "@/lib/session/fixtures";
import { SESSION_COOKIE, agentBaseUrl, decodeSession, loadState } from "@/lib/session/server";

export const dynamic = "force-dynamic";

// `?state=<name>` replays a fixed spec state through MockSessionDriver (qa:visual, e2e).
// Otherwise, with PERSONA_AGENT_BASE_URL set, the real session is resolved server-side from
// the httpOnly cookie so a refresh / return visit renders the right surface first paint
// (EC-08, EC-30, EC-31). Without an agent configured the page falls back to the mock.
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  // `?reset=1` (RESET-001): forget this browser's session and start fresh (the route clears the
  // httpOnly cookie, creates a new session and redirects back to "/").
  if (sp.reset === "1") redirect("/api/session/reset");
  const s = typeof sp.state === "string" ? sp.state : undefined;
  if (isStateName(s) || !agentBaseUrl()) {
    const initial = isStateName(s) ? s : "landing";
    return <App key={initial} initialState={initial} capture={sp.capture === "1"} />;
  }
  const state = await loadState(decodeSession((await cookies()).get(SESSION_COOKIE)?.value));
  return <App key="live" initialState="landing" capture={false} live={{ state, begin: sp.fresh === "1" }} />;
}
