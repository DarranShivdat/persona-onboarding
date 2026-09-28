import type { Locator, Page } from "@playwright/test";
import { startBot } from "../call";
import { oauthVia } from "../gmail-oauth";
import { AGENT_URL, AT_GMAIL, seed, useSession, type Seed } from "../live";
import { composerField, expect, expectFocused, expectSent, thread, typeAndSend, userBubble, type Ctx, type Expectation, type Screen } from "./audit";

// AUDIT-001 screen catalogue: how to reach each screen (LOCAL: real ApiSessionDriver against the
// stub agent; LIVE: the real deploy, or a `?state=` fixture where reaching the state for real
// would need a Google sign-in or a second real call) and what every control on it must do.

const GRAD = "owned by GRAD-001 (home composer rebuild): typed message must get a visible reply on home";

// ---- setups ------------------------------------------------------------------------------

async function fresh(ctx: Ctx, path = "/") {
  await ctx.context.clearCookies();
  const r = await ctx.page.goto(path);
  ctx.data.status = r?.status();
}

async function seeded(ctx: Ctx, s: Seed) {
  await ctx.context.clearCookies();
  const ref = await seed(s);
  await useSession(ctx.context, ctx.baseURL, ref);
  ctx.data.id = ref.id;
  await ctx.page.goto("/");
  await expect(thread(ctx.page).or(ctx.page.locator("main.home"))).toBeVisible();
}

const fixture = (name: string) => (ctx: Ctx) => fresh(ctx, `/?state=${name}`);

async function liveStart(ctx: Ctx) {
  await fresh(ctx);
  await ctx.page.getByRole("button", { name: "Get started" }).click();
  await expect(thread(ctx.page).locator('[data-from="agent"]').first()).toBeVisible({ timeout: 30_000 });
}

async function liveNamed(ctx: Ctx) {
  await liveStart(ctx);
  const f = composerField(ctx.page);
  await f.fill("Juno");
  await f.press("Enter");
  await expect(ctx.page.getByTestId("call-offer")).toBeVisible({ timeout: 45_000 });
}

const OFFER: Seed = {
  node: "call_offer",
  slots: { agent_name: "Juno" },
  transcript: [
    ["assistant", "Hi! First things first: what would you like to call me?"],
    ["user", "Juno"],
    ["assistant", "Got it: Juno. Want to hop on a quick call for the rest, or keep typing?"],
  ],
};

async function atOffer(ctx: Ctx) {
  if (ctx.target === "live") return liveNamed(ctx);
  await seeded(ctx, OFFER);
  await expect(ctx.page.getByTestId("call-offer")).toBeVisible();
}

/** LOCAL call: a bot page answers the offer (e2e/call.ts), so the real WebRTC leg connects. */
async function connected(ctx: Ctx) {
  if (ctx.target === "live") return fixture("call-connected")(ctx);
  await atOffer(ctx);
  const bot = await startBot(ctx.browser, ctx.data.id as string);
  ctx.cleanup(() => bot.close());
  await ctx.page.getByTestId("call-offer").getByRole("button", { name: "Call Juno" }).click();
  await expect(ctx.page.getByTestId("call-panel")).toHaveAttribute("data-status", "connected", { timeout: 20_000 });
}

/** LOCAL ringing: arm the stub's bot slot without answering, so the dial rings (~10s) then fails. */
async function ringing(ctx: Ctx) {
  if (ctx.target === "live") return fixture("call-ringing")(ctx);
  await atOffer(ctx);
  const ac = new AbortController();
  void fetch(`${AGENT_URL}/__test/sessions/${ctx.data.id}/bot/offer`, { signal: ac.signal }).catch(() => null);
  await new Promise((r) => setTimeout(r, 150));
  ac.abort();
  await ctx.page.getByTestId("call-offer").getByRole("button", { name: "Call Juno" }).click();
  await expect(ctx.page.getByTestId("call-panel")).toHaveAttribute("data-status", "ringing");
}

async function atGmail(ctx: Ctx) {
  if (ctx.target === "live") {
    // One utterance fills name + need (extraction runs for all slots), then decline the call.
    await liveNamed(ctx);
    await ctx.page.getByTestId("call-offer").getByRole("button", { name: "Keep texting" }).click();
    const f = composerField(ctx.page);
    await expect(f).toBeEnabled();
    await ctx.page.waitForTimeout(1500);
    await f.fill("I'm Maya, and I need help getting my inbox under control.");
    await f.press("Enter");
    await expect(ctx.page.getByTestId("gmail-card")).toBeVisible({ timeout: 60_000 });
    return;
  }
  await seeded(ctx, AT_GMAIL);
  await expect(ctx.page.getByTestId("gmail-card")).toHaveAttribute("data-state", "idle");
}

const viaOAuth = (choice: string, state: string, fixtureName: string) => async (ctx: Ctx) => {
  if (ctx.target === "live") return fixture(fixtureName)(ctx);
  await atGmail(ctx);
  await oauthVia(ctx.page, "Continue with Google", choice);
  await expect(ctx.page.getByTestId("gmail-card")).toHaveAttribute("data-state", state);
};

// ---- actions -----------------------------------------------------------------------------

const panel = (p: Page) => p.getByTestId("call-panel");
const card = (p: Page) => p.getByTestId("gmail-card");

/** Clicking opens the OAuth popup. LOCAL: the mock consent page with our callback. LIVE: the
 * redirect reaches accounts.google.com with our redirect_uri, then we close it (no sign-in). */
async function opensGoogle(ctl: Locator, ctx: Ctx, heading = /Sign in with Google|Choose an account/) {
  const popupP = ctx.page.waitForEvent("popup");
  const google = ctx.context.waitForEvent("request", { predicate: (r) => r.url().startsWith("https://accounts.google.com/"), timeout: 20_000 }).catch(() => null);
  await ctl.click();
  const popup = await popupP;
  ctx.cleanup(() => popup.close().catch(() => null));
  if (ctx.target === "live") {
    const r = await google;
    expect(r, "popup must reach accounts.google.com").not.toBeNull();
    const u = new URL(r!.url());
    expect(u.searchParams.get("redirect_uri")).toBe(`${ctx.baseURL}/api/oauth/google/callback`);
    await popup.close();
    return;
  }
  await expect(popup.getByRole("heading", { name: heading })).toBeVisible();
  const q = popup.locator("[data-scope]");
  await expect(q).toBeAttached();
  expect(new URL(popup.url()).searchParams.get("redirect_uri")).toBe(`${ctx.baseURL}/api/oauth/google/callback`);
}

const sends = (text?: string) => async (ctl: Locator, ctx: Ctx) => {
  const t = text ?? ((await ctl.innerText()).trim() || "?");
  await expectSent(ctx.page, t, () => ctl.click());
};

const focusesComposer = async (ctl: Locator, ctx: Ctx) => {
  await ctl.click();
  await expectFocused(ctx.page, composerField(ctx.page));
};

const nav = (path: string) => async (ctl: Locator, ctx: Ctx) => {
  await ctl.click();
  await expect(ctx.page).toHaveURL(new URL(path, ctx.baseURL).toString());
};

/** A call starts from this button: LOCAL connects via a bot; the rail opens. */
const dials = async (ctl: Locator, ctx: Ctx) => {
  if (ctx.target === "local" && ctx.data.id && !ctx.data.bot) {
    const bot = await startBot(ctx.browser, ctx.data.id as string);
    ctx.data.bot = bot;
    ctx.cleanup(() => bot.close());
  }
  await ctl.click();
  await expect(panel(ctx.page)).toHaveAttribute("data-status", /ringing|connected/, { timeout: 20_000 });
};

// `email`: at the Gmail step the stub brain treats any non-email text as "skip Gmail" (graduates),
// so the composer is exercised with the VOICE-004-lite "type your email" path instead.
const composerControls = (opts: { fixme?: string; call?: boolean; email?: boolean } = {}): Expectation[] => [
  { match: /^textbox:.+ @composer$/, expected: "type + Enter sends the turn (user bubble), field clears", fixme: opts.fixme, run: (_c, ctx) => typeAndSend(ctx.page, opts.email ? "maya.r@gmail.com" : "audit ping one", "enter") },
  { match: "button:Send @composer", expected: "Send posts the typed turn (user bubble), field clears", fixme: opts.fixme, run: (_c, ctx) => typeAndSend(ctx.page, opts.email ? "maya.r@gmail.com" : "audit ping two", "button") },
  ...(opts.call === false
    ? []
    : [{ match: /^button:Call .+ @composer$/, optional: true, expected: "composer call button dials (rail opens)", skipLive: "real calls only on the call-live screen (<= 2 per LIVE run)", run: dials } as Expectation]),
];

const offerControls: Expectation[] = [
  { match: "button:Call Juno @call-offer", expected: "dials: rail opens ringing -> connected", skipLive: "real calls only on the call-live screen (<= 2 per LIVE run)", run: dials },
  { match: "button:Keep texting @call-offer", expected: "sends 'Keep texting' as a turn", run: sends("Keep texting") },
  ...composerControls(),
];

// Home: GRAD-001 is rebuilding it; these are the NEW-behaviour expectations.
const homeReply = (via: "enter" | "button") => async (_c: Locator, ctx: Ctx) => {
  const p = ctx.page;
  const text = via === "enter" ? "what can you do first?" : "thanks!";
  const before = await p.locator('[data-from="agent"]').count();
  const f = composerField(p);
  await f.fill(text);
  if (via === "enter") await f.press("Enter");
  else await p.locator("form.composer").getByRole("button", { name: "Send" }).click();
  await expect(p.locator('[data-from="user"]').filter({ hasText: text }).last()).toBeVisible({ timeout: 10_000 });
  await expect.poll(() => p.locator('[data-from="agent"]').count(), { timeout: 30_000 }).toBeGreaterThan(before);
};

// ---- catalogue -----------------------------------------------------------------------------

export const SCREENS: Screen[] = [
  {
    name: "landing",
    title: "Landing",
    setup: (ctx) => fresh(ctx),
    controls: [
      {
        match: "button:Get started @main",
        expected: "POST /api/session, chat opens with the agent's first message",
        run: async (ctl, ctx) => {
          const created = ctx.page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/session");
          await ctl.click();
          expect([200, 201]).toContain((await created).status());
          await expect(thread(ctx.page).locator('[data-from="agent"]').first()).toBeVisible({ timeout: 30_000 });
        },
      },
    ],
  },
  {
    name: "chat-agent-name",
    title: "Text chat: agent name (chips)",
    setup: (ctx) => (ctx.target === "live" ? liveStart(ctx) : seeded(ctx, { node: "agent_name" })),
    check: async (ctx) => {
      if (ctx.target === "local") await expect(thread(ctx.page).locator(".chips").getByRole("button")).toHaveText(["Juno", "Atlas", "Surprise me"]);
    },
    controls: [
      { match: /^chip:.+ @thread$/, expected: "chip sends its text as the user's turn", run: sends() },
      ...composerControls({ call: false }),
    ],
  },
  { name: "call-offer", title: "Text chat: call offer", setup: atOffer, controls: offerControls },
  {
    name: "resume",
    title: "Refresh / resume (thread + checklist restored)",
    setup: async (ctx) => {
      await atOffer(ctx);
      await ctx.page.reload();
      await expect(ctx.page.getByTestId("call-offer")).toBeVisible({ timeout: 20_000 });
    },
    check: async (ctx) => {
      await expect(userBubble(ctx.page, "Juno").first()).toBeVisible();
      await expect(ctx.page.locator('[data-testid^="checklist-"]:visible [data-slot="agent_name"]')).toHaveAttribute("aria-label", "Assistant name: Juno");
    },
    controls: offerControls,
  },
  {
    name: "mic-denied",
    title: "Mic denied (EC-03)",
    setup: async (ctx) => {
      await ctx.page.addInitScript(() => {
        const deny = () => Promise.reject(new DOMException("Permission denied", "NotAllowedError"));
        Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: deny }, configurable: true });
      });
      await atOffer(ctx);
      await ctx.page.getByTestId("call-offer").getByRole("button", { name: "Call Juno" }).click();
      await expect(thread(ctx.page)).toContainText(/microphone|mic/i);
    },
    check: async (ctx) => expect(panel(ctx.page)).toHaveCount(0),
    controls: [
      {
        match: "button:Call Juno @call-offer",
        expected: "retries the mic: explains the block again in text, rail stays closed (no call placed)",
        run: async (ctl, ctx) => {
          const n = await thread(ctx.page).getByText(/microphone|mic/i).count();
          await ctl.click();
          await expect.poll(() => thread(ctx.page).getByText(/microphone|mic/i).count()).toBeGreaterThan(n);
          await expect(panel(ctx.page)).toHaveCount(0);
        },
      },
      { match: "button:Keep texting @call-offer", expected: "sends 'Keep texting' as a turn", run: sends("Keep texting") },
      ...composerControls({ call: false }),
      {
        match: /^button:Call .+ @composer$/,
        optional: true,
        expected: "composer call retries the mic (text explanation, no rail)",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(panel(ctx.page)).toHaveCount(0);
        },
      },
    ],
  },
  {
    name: "call-ringing",
    title: "Call: ringing",
    setup: ringing,
    controls: [
      { match: "button:Mute @call-panel", expected: "disabled while ringing (native disabled, no pointer)", disabled: true },
      { match: "button:Type instead @call-panel", expected: "focuses the composer", run: focusesComposer },
      {
        match: "button:Cancel @call-panel",
        expected: "cancels the dial: rail closes, chat resumes",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(panel(ctx.page)).toHaveCount(0);
        },
      },
      ...composerControls({ call: false }),
    ],
  },
  {
    name: "call-connected",
    title: "Call: live + captions",
    setup: connected,
    check: async (ctx) => expect(ctx.page.getByLabel("Live captions")).toBeVisible(),
    controls: [
      {
        match: "button:Mute @call-panel",
        expected: "mutes: aria-pressed=true, label Unmute",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(panel(ctx.page).getByRole("button", { name: "Unmute" })).toHaveAttribute("aria-pressed", "true");
        },
      },
      { match: "button:Type instead @call-panel", expected: "focuses the composer", run: focusesComposer },
      {
        match: "button:End @call-panel",
        expected: "hangs up: status 'Call ended', lease released",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(panel(ctx.page)).toHaveAttribute("data-status", "ended");
          if (ctx.target === "local") await expect.poll(async () => (await (await fetch(`${AGENT_URL}/__test/sessions/${ctx.data.id}/log`)).json()).call_live).toBe(false);
        },
      },
      ...composerControls({ call: false }),
    ],
  },
  {
    name: "call-muted",
    title: "Call: muted",
    setup: async (ctx) => {
      if (ctx.target === "live") return fixture("call-muted")(ctx);
      await connected(ctx);
      await panel(ctx.page).getByRole("button", { name: "Mute" }).click();
      await expect(panel(ctx.page)).toHaveAttribute("data-status", "muted");
    },
    controls: [
      {
        match: "button:Unmute @call-panel",
        expected: "unmutes: aria-pressed=false, label Mute",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(panel(ctx.page).getByRole("button", { name: "Mute" })).toHaveAttribute("aria-pressed", "false");
        },
      },
      { match: "button:Type instead @call-panel", expected: "focuses the composer", run: focusesComposer },
      {
        match: "button:End @call-panel",
        expected: "hangs up: status 'Call ended'",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(panel(ctx.page)).toHaveAttribute("data-status", "ended");
        },
      },
      ...composerControls({ call: false }),
    ],
  },
  {
    name: "call-ended",
    title: "Call: ended (hangup)",
    setup: async (ctx) => {
      if (ctx.target === "live") return fixture("call-ended")(ctx);
      await connected(ctx);
      await panel(ctx.page).getByRole("button", { name: "End" }).click();
      await expect(panel(ctx.page)).toHaveAttribute("data-status", "ended");
    },
    controls: [
      {
        match: "button:Call again @call-panel",
        expected: "redials: rail back to ringing/connected",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(panel(ctx.page)).toHaveAttribute("data-status", /ringing|connected/, { timeout: 20_000 });
        },
      },
      {
        match: "button:Keep typing @call-panel",
        expected: "collapses the ended rail",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(panel(ctx.page)).toHaveCount(0);
        },
      },
      ...composerControls(),
    ],
  },
  {
    name: "call-live",
    title: "Call: real call on the deploy (LIVE, 1 call)",
    targets: ["live"],
    viewports: ["desktop"],
    sequential: true,
    setup: async (ctx) => {
      await liveNamed(ctx);
      await ctx.page.getByTestId("call-offer").getByRole("button", { name: "Call Juno" }).click();
      await expect(panel(ctx.page)).toHaveAttribute("data-status", "connected", { timeout: 45_000 });
      await expect(ctx.page.getByLabel("Live captions")).toContainText(/\S/, { timeout: 20_000 });
    },
    controls: [
      {
        match: "button:Mute @call-panel",
        expected: "mute -> Unmute (aria-pressed) -> unmute",
        run: async (ctl, ctx) => {
          await ctl.click();
          const un = panel(ctx.page).getByRole("button", { name: "Unmute" });
          await expect(un).toHaveAttribute("aria-pressed", "true");
          await un.click();
          await expect(panel(ctx.page).getByRole("button", { name: "Mute" })).toHaveAttribute("aria-pressed", "false");
        },
      },
      { match: "button:Type instead @call-panel", expected: "focuses the composer", run: focusesComposer },
      { match: /^textbox:.+ @composer$/, expected: "typing during the call is accepted (focus + value)", run: async (ctl) => { await ctl.fill("x"); await expect(ctl).toHaveValue("x"); await ctl.fill(""); } },
      { match: "button:Send @composer", expected: "empty Send is a no-op (no bubble, no error)", run: async (ctl, ctx) => { const n = await thread(ctx.page).locator('[data-from="user"]').count(); await ctl.click(); await expect(thread(ctx.page).locator('[data-from="user"]')).toHaveCount(n); } },
      {
        match: "button:End @call-panel",
        expected: "hangs up: 'Call ended', lease released",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(panel(ctx.page)).toHaveAttribute("data-status", "ended");
          await expect.poll(async () => ((await (await ctx.page.request.get("/api/session")).json()) as { state?: { call?: { live?: boolean } } }).state?.call?.live, { timeout: 15_000 }).toBe(false);
        },
      },
    ],
  },
  {
    name: "gmail-idle",
    title: "Gmail card: connect",
    setup: atGmail,
    sequential: TARGET_LIVE(),
    controls: [
      { match: "button:Continue with Google @gmail-card", expected: "opens Google OAuth popup with our redirect_uri (LIVE: stop at accounts.google.com)", run: (c, ctx) => opensGoogle(c, ctx) },
      { match: "button:Not now @gmail-card", expected: "sends 'Not now' (brain defers Gmail)", skipLive: "LOCAL covers it; LIVE keeps one session per viewport", run: sends("Not now") },
      ...composerControls({ email: true }).map((e) => ({ ...e, skipLive: e.skipLive ?? "LOCAL covers it; LIVE keeps one session per viewport" })),
    ],
  },
  {
    name: "gmail-connecting",
    title: "Gmail card: waiting for Google",
    setup: async (ctx) => {
      if (ctx.target === "live") return fixture("gmail-card-connecting")(ctx);
      await atGmail(ctx);
      ctx.data.popup = await oauthVia(ctx.page, "Continue with Google", null);
      await expect(card(ctx.page)).toHaveAttribute("data-state", "connecting");
    },
    controls: [
      { match: "button:Waiting for Google… @gmail-card", expected: "disabled + aria-busy while the popup is open", disabled: true },
      {
        match: "button:Open the Google window again @gmail-card",
        expected: "re-opens the Google popup if it was closed",
        skipLive: "fixture (mock driver) on LIVE; LOCAL covers the real popup",
        run: async (ctl, ctx) => {
          await (ctx.data.popup as Page).close();
          await opensGoogle(ctl, ctx);
        },
      },
      ...composerControls({ email: true }).map((e) => ({ ...e, skipLive: e.skipLive ?? undefined })),
    ],
  },
  {
    name: "gmail-connected",
    title: "Gmail card: connected",
    setup: viaOAuth("Allow as maya.r@gmail.com", "connected", "gmail-card-connected"),
    controls: [
      { match: "button:Not you? Use a different account @gmail-card", expected: "re-runs OAuth with the account chooser", skipLive: "needs a completed Google sign-in", run: (c, ctx) => opensGoogle(c, ctx, /Choose an account/) },
      ...composerControls({ email: true }),
    ],
  },
  {
    name: "gmail-partial",
    title: "Gmail card: partial grant",
    targets: ["local"],
    setup: viaOAuth("Allow as maya.r@gmail.com without send", "connected", "gmail-card-partial"),
    controls: [
      { match: "button:Allow full access @gmail-card", expected: "re-opens consent to grant send", run: (c, ctx) => opensGoogle(c, ctx) },
      { match: "button:Not you? Use a different account @gmail-card", expected: "re-runs OAuth with the account chooser", run: (c, ctx) => opensGoogle(c, ctx, /Choose an account/) },
      ...composerControls({ email: true }),
    ],
  },
  {
    name: "gmail-error",
    title: "Gmail card: cancelled / error",
    setup: viaOAuth("Cancel", "error", "gmail-card-error"),
    controls: [
      { match: "button:Try again @gmail-card", expected: "re-opens Google OAuth", skipLive: "fixture (mock driver) on LIVE", run: (c, ctx) => opensGoogle(c, ctx) },
      { match: "button:Skip for now @gmail-card", expected: "sends 'Skip for now' (brain defers Gmail)", skipLive: "fixture (mock driver) on LIVE", run: sends("Skip for now") },
      ...composerControls({ email: true }),
    ],
  },
  {
    name: "gmail-wrong-account",
    title: "Gmail card: wrong account",
    setup: viaOAuth("Allow as maya@work.co", "wrong_account", "gmail-card-wrong-account"),
    controls: [
      { match: "button:Use a different account @gmail-card", expected: "re-runs OAuth with the account chooser", skipLive: "fixture (mock driver) on LIVE", run: (c, ctx) => opensGoogle(c, ctx, /Choose an account/) },
      {
        match: "button:Keep this one @gmail-card",
        expected: "keeps the account: card -> connected",
        skipLive: "fixture (mock driver) on LIVE",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(card(ctx.page)).toHaveAttribute("data-state", "connected");
        },
      },
      ...composerControls({ email: true }),
    ],
  },
  {
    name: "home",
    title: "Graduation / home",
    setup: async (ctx) => {
      if (ctx.target === "live") return fixture("graduation")(ctx);
      await seeded(ctx, {
        node: "graduated",
        graduated: true,
        slots: { agent_name: "Juno", user_name: "Maya", need: "Inbox triage", gmail: "skipped" },
        deferred: ["gmail"],
        transcript: [["assistant", "You're all set — let's get to work."]],
      });
      await expect(ctx.page.getByRole("heading", { name: /You’re all set/ })).toBeVisible();
    },
    controls: [
      { match: /^button:Connect( Gmail)? @deferred-prompt$/, expected: "deferred prompt opens Google OAuth", skipLive: "fixture (mock driver) on LIVE", run: (c, ctx) => opensGoogle(c, ctx) },
      {
        match: "button:Dismiss @deferred-prompt",
        expected: "hides the deferred prompt for this visit",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(ctx.page.getByTestId("deferred-prompt")).toHaveCount(0);
        },
      },
      { match: /^textbox:.+ @(main|composer)$/, expected: "message -> user bubble + agent reply visible on home", fixme: GRAD, run: homeReply("enter") },
      { match: /^button:Send @(main|composer)$/, expected: "Send -> user bubble + agent reply visible on home", fixme: GRAD, run: homeReply("button") },
      {
        match: /^button:(Edit|Change) .+/,
        optional: true,
        expected: "tap-to-edit: opens an editable field for that item (GRAD-001)",
        run: async (ctl, ctx) => {
          const n = await ctx.page.getByRole("textbox").count();
          await ctl.click();
          await expect.poll(() => ctx.page.getByRole("textbox").count()).toBeGreaterThan(n);
        },
      },
    ],
  },
  {
    name: "error-agent-down",
    title: "Error: agent down at start (banner + retry)",
    setup: async (ctx) => {
      await fresh(ctx);
      await ctx.page.route("**/api/session", (r) => (r.request().method() === "POST" ? r.fulfill({ status: 502, contentType: "application/json", body: '{"error":"agent_unreachable"}' }) : r.fallback()));
      await ctx.page.getByRole("button", { name: "Get started" }).click();
      await expect(ctx.page.getByTestId("notice")).toBeVisible();
    },
    check: async (ctx) => expect(ctx.page.getByTestId("notice")).toHaveAttribute("role", "alert").then(() => expect(ctx.page.getByTestId("notice")).toContainText("Couldn’t reach")),
    controls: [
      {
        match: "button:Get started @main",
        expected: "still down: the banner stays (no dead click, no crash)",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(ctx.page.getByTestId("notice")).toBeVisible();
          await expect(ctx.page.locator('[data-surface="landing"]')).toBeVisible();
        },
      },
      {
        match: "button:Try again @notice",
        expected: "agent back: retry starts the session, banner clears, chat opens",
        run: async (ctl, ctx) => {
          await ctx.page.unroute("**/api/session");
          await ctl.click();
          await expect(thread(ctx.page).locator('[data-from="agent"]').first()).toBeVisible({ timeout: 30_000 });
          await expect(ctx.page.getByTestId("notice")).toHaveCount(0);
        },
      },
    ],
  },
  {
    name: "error-turn",
    title: "Error: agent down mid-chat (banner + retry)",
    setup: async (ctx) => {
      if (ctx.target === "live") await liveStart(ctx);
      else await seeded(ctx, { node: "agent_name" });
      await ctx.page.route("**/api/session/turns", (r) => r.fulfill({ status: 502, contentType: "application/json", body: '{"error":"agent_unreachable"}' }));
      const f = composerField(ctx.page);
      await f.fill("Juno");
      await f.press("Enter");
      await expect(ctx.page.getByTestId("notice")).toBeVisible();
    },
    check: async (ctx) => expect(ctx.page.getByTestId("notice")).toHaveAttribute("role", "alert").then(() => expect(ctx.page.getByTestId("notice")).toContainText("Couldn’t send that")),
    controls: [
      {
        match: "button:Try again @notice",
        expected: "agent back: re-sends the failed turn, banner clears",
        run: async (ctl, ctx) => {
          await ctx.page.unroute("**/api/session/turns");
          await ctl.click();
          await expect(userBubble(ctx.page, "Juno").first()).toBeVisible({ timeout: 30_000 });
          await expect(ctx.page.getByTestId("notice")).toHaveCount(0);
        },
      },
      {
        match: /^chip:.+ @thread$/,
        optional: true,
        expected: "still down: chip send re-shows the banner",
        run: async (ctl, ctx) => {
          await ctl.click();
          await expect(ctx.page.getByTestId("notice")).toBeVisible();
        },
      },
      {
        match: /^textbox:.+ @composer$/,
        expected: "still down: typed send keeps the banner up",
        run: async (ctl, ctx) => {
          await ctl.fill("Atlas");
          await ctl.press("Enter");
          await expect(ctx.page.getByTestId("notice")).toBeVisible();
        },
      },
      {
        match: "button:Send @composer",
        expected: "still down: Send keeps the banner up",
        run: async (ctl, ctx) => {
          await composerField(ctx.page).fill("Atlas");
          await ctl.click();
          await expect(ctx.page.getByTestId("notice")).toBeVisible();
        },
      },
    ],
  },
  {
    name: "not-found",
    title: "404 route",
    setup: (ctx) => fresh(ctx, "/this-page-does-not-exist-audit"),
    check: async (ctx) => {
      expect(ctx.data.status).toBe(404);
      await expect(ctx.page.getByRole("heading", { level: 1 })).toContainText("doesn’t exist");
    },
    controls: [
      { match: "link:Persona @main", expected: "brand -> /", run: nav("/") },
      { match: "link:Set up your assistant @main", expected: "-> / (setup)", run: nav("/") },
      { match: "link:About Persona @footer", expected: "-> /about", run: nav("/about") },
      { match: "link:Privacy Policy @footer", expected: "-> /privacy", run: nav("/privacy") },
    ],
  },
  {
    name: "about",
    title: "/about",
    setup: (ctx) => fresh(ctx, "/about"),
    controls: [
      { match: "link:Persona @main", expected: "brand -> /", run: nav("/") },
      { match: "link:Privacy Policy @main", expected: "inline -> /privacy", run: nav("/privacy") },
      { match: "link:Set up your assistant @main", expected: "CTA -> /", run: nav("/") },
      { match: "link:yourpersona.com @footer", expected: "external href resolves (no click-away)" },
      { match: "link:Privacy Policy @footer", expected: "-> /privacy", run: nav("/privacy") },
    ],
  },
  {
    name: "privacy",
    title: "/privacy",
    setup: (ctx) => fresh(ctx, "/privacy"),
    controls: [
      { match: "link:Persona @main", expected: "brand -> /about", run: nav("/about") },
      { match: "link:Google API Services User Data Policy @main", expected: "external href resolves" },
      { match: "link:myaccount.google.com/permissions @main", expected: "external href resolves" },
      { match: "link:darranshivdat1@gmail.com @main", expected: "valid mailto" },
      { match: "link:About Persona @footer", expected: "-> /about", run: nav("/about") },
      { match: "link:Set up your assistant @footer", expected: "-> /", run: nav("/") },
    ],
  },
];

function TARGET_LIVE() {
  return process.env.PERSONA_AUDIT_TARGET === "live";
}
