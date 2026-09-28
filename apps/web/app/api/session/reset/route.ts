// "Start over" (RESET-001, RESET-002 fast path): forget this browser's onboarding session. The
// session cookie is httpOnly, so only the server can clear it. Nothing else happens here: no
// agent round trip, so the reset answers in one same-origin hop (< 1 s). The next session is
// created lazily by the landing page on the first message (ApiSessionDriver.begin); the old
// session row is left as-is (resuming it needs the cookie, which is gone). An active call is
// hung up by the page (StartOver dispatches `persona:start-over`; pagehide backs it up).
//   GET  /api/session/reset        -> 303 to "/?fresh=1" (Start over button + `/?reset=1`); the page
//                                     then begins the new session client-side (agent_name step)
//   POST /api/session/reset        -> { ok } (JSON)
import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, cookieOptions, isHttps } from "@/lib/session/server";

export const dynamic = "force-dynamic";

function expire(req: NextRequest, res: NextResponse): NextResponse {
  res.cookies.set(SESSION_COOKIE, "", { ...cookieOptions(isHttps(req)), maxAge: 0 });
  res.headers.set("cache-control", "no-store");
  return res;
}

export async function POST(req: NextRequest) {
  return expire(req, NextResponse.json({ ok: true, state: null }));
}

export async function GET(req: NextRequest) {
  return expire(req, NextResponse.redirect(new URL("/?fresh=1", req.url), 303));
}
