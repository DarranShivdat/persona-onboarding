// Same-origin proxy: create / resume the onboarding session. The httpOnly cookie carries
// `<id>.<token>`; the browser only ever sees the brain's snapshot.
import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, agentBaseUrl, agentFetch, cookieOptions, decodeSession, encodeSession, isHttps, jsonError, loadState } from "@/lib/session/server";

export const dynamic = "force-dynamic";

/** Resume: the current session's snapshot, or 404 when there is none. */
export async function GET(req: NextRequest) {
  if (!agentBaseUrl()) return jsonError(503, "agent_unconfigured");
  const state = await loadState(decodeSession(req.cookies.get(SESSION_COOKIE)?.value));
  return state ? NextResponse.json({ state }, { headers: { "cache-control": "no-store" } }) : jsonError(404, "no_session");
}

/** Start: reuse a live session (EC-30/31: never fork a second one), else create it. */
export async function POST(req: NextRequest) {
  if (!agentBaseUrl()) return jsonError(503, "agent_unconfigured");
  const existing = await loadState(decodeSession(req.cookies.get(SESSION_COOKIE)?.value));
  if (existing) return NextResponse.json({ state: existing, resumed: true }, { headers: { "cache-control": "no-store" } });
  let r: Response;
  try {
    r = await agentFetch("/v1/sessions", { method: "POST" });
  } catch {
    return jsonError(502, "agent_unreachable");
  }
  if (!r.ok) return jsonError(r.status === 429 ? 429 : 502, r.status === 429 ? "rate_limited" : "agent_error");
  const body = (await r.json()) as { id: string; token: string; state: unknown };
  const res = NextResponse.json({ state: body.state, resumed: false }, { status: 201, headers: { "cache-control": "no-store" } });
  res.cookies.set(SESSION_COOKIE, encodeSession({ id: body.id, token: body.token }), cookieOptions(isHttps(req)));
  return res;
}
