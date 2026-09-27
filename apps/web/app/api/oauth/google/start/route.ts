// OAuth start (popup): binds a fresh state/PKCE/nonce to the current session and redirects
// to Google (or the e2e mock double). `?switch=1` asks Google to show the account chooser.
import { type NextRequest, NextResponse } from "next/server";
import { OAUTH_COOKIE, authorizeUrl, encodeFlow, flowCookieOptions, googleConfig, newFlow, redirectUri, resultPage } from "@/lib/oauth/google";
import { SESSION_COOKIE, decodeSession, isHttps } from "@/lib/session/server";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const cfg = googleConfig();
  if (!cfg) return resultPage({ status: "error", reason: "oauth_unconfigured" });
  const s = decodeSession(req.cookies.get(SESSION_COOKIE)?.value);
  if (!s) return resultPage({ status: "error", reason: "no_session" });
  const flow = newFlow(s.id);
  const res = NextResponse.redirect(authorizeUrl(cfg, flow, redirectUri(req), req.nextUrl.searchParams.get("switch") === "1"), 302);
  res.headers.set("cache-control", "no-store");
  res.cookies.set(OAUTH_COOKIE, encodeFlow(flow), flowCookieOptions(isHttps(req)));
  return res;
}
