// "Start over" (RESET-001): forget this browser's onboarding session and begin a fresh one.
// The session cookie is httpOnly, so only the server can clear it. The old session row is left
// as-is (resuming it needs the cookie, which is gone); a fresh session starts at agent_name.
//   POST /api/session/reset        -> { ok, state } (JSON; used by the Start over button)
//   GET  /api/session/reset        -> 303 to "/" (used by `/?reset=1`)
import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, agentBaseUrl, agentFetch, cookieOptions, encodeSession, isHttps } from "@/lib/session/server";

export const dynamic = "force-dynamic";

type Fresh = { id: string; token: string; state: unknown } | null;

async function freshSession(): Promise<Fresh> {
  if (!agentBaseUrl()) return null;
  try {
    const r = await agentFetch("/v1/sessions", { method: "POST" });
    return r.ok ? ((await r.json()) as Fresh) : null;
  } catch {
    return null;
  }
}

function apply(req: NextRequest, res: NextResponse, fresh: Fresh): NextResponse {
  const secure = isHttps(req);
  if (fresh) res.cookies.set(SESSION_COOKIE, encodeSession({ id: fresh.id, token: fresh.token }), cookieOptions(secure));
  else res.cookies.set(SESSION_COOKIE, "", { ...cookieOptions(secure), maxAge: 0 });
  res.headers.set("cache-control", "no-store");
  return res;
}

export async function POST(req: NextRequest) {
  const fresh = await freshSession();
  return apply(req, NextResponse.json({ ok: true, state: fresh?.state ?? null }), fresh);
}

export async function GET(req: NextRequest) {
  const fresh = await freshSession();
  return apply(req, NextResponse.redirect(new URL("/", req.url), 303), fresh);
}
