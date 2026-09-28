import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { fakeMic, startBot, type Bot } from "./call";
import { gmailItem, oauthVia } from "./gmail-oauth";
import { AGENT_URL, AT_GMAIL, seed, useSession } from "./live";

// VOICE-004-lite: Gmail on a live browser call. The same card (same OAuth, read+write scopes)
// sits in the thread next to the call (spec §4.5); the rail and composer point at it and at the
// "type it" escape, which goes through the shared text turn path. No spoken NATO capture.
// EC-19/20/21 voice variants at the UI level; the spoken side is services/agent/tests/test_voice_gmail.py.

const TYPE_IT = "Or type your email here…";

async function onCall(browser: Browser, context: BrowserContext, page: Page, baseURL: string): Promise<{ id: string; bot: Bot }> {
  const s = await seed(AT_GMAIL);
  const bot = await startBot(browser, s.id);
  await useSession(context, baseURL, s);
  await fakeMic(context);
  await page.goto("/");
  await expect(page.getByTestId("gmail-card")).toHaveAttribute("data-state", "idle");
  await page.getByTestId("composer-call").click();
  await expect(page.getByTestId("call-panel")).toHaveAttribute("data-status", "connected", { timeout: 15_000 });
  return { id: s.id, bot };
}

const agentLog = async (id: string) => (await (await fetch(`${AGENT_URL}/__test/sessions/${id}/log`)).json()) as { gmail_failed: string[]; call_live: boolean; node: string };

test("card is on screen during the call and 'type it' goes through the chat", async ({ browser, context, page, baseURL }) => {
  const { bot } = await onCall(browser, context, page, baseURL!);
  try {
    const card = page.getByTestId("gmail-card");
    const rail = page.getByTestId("call-panel");
    await expect(card).toBeVisible();
    await expect(card.getByRole("button", { name: "Continue with Google" })).toBeVisible();
    await expect(rail).toContainText("Tap Continue with Google on the card in the chat, or type your email there.");

    // "Type instead" focuses the composer, which now invites the email.
    await rail.getByRole("button", { name: "Type instead" }).click();
    const composer = page.getByRole("textbox", { name: TYPE_IT.replace(/…$/, "") });
    await expect(composer).toBeFocused();
    await composer.fill("maya.r@gmail.com");
    await composer.press("Enter");

    await expect(page.getByTestId("thread")).toContainText("tap Continue with Google on the card");
    // A typed email is never "connected": the card waits for Google; the call stays up.
    await expect(card).toHaveAttribute("data-state", "idle");
    await expect(gmailItem(page)).toHaveAttribute("aria-label", /Not yet/);
    await expect(rail).toHaveAttribute("data-status", "connected");
  } finally {
    await bot.close();
  }
});

test("EC-21 voice: cancelled consent on a call surfaces on the card, the rail, and the brain", async ({ browser, context, page, baseURL }) => {
  const { id, bot } = await onCall(browser, context, page, baseURL!);
  try {
    const card = page.getByTestId("gmail-card");
    const rail = page.getByTestId("call-panel");
    await oauthVia(page, "Continue with Google", "Cancel");
    await expect(card).toHaveAttribute("data-state", "error");
    await expect(rail).toContainText("Google didn’t finish. Try again on the card in the chat, or type your email there.");
    await expect(rail).toHaveAttribute("data-status", "connected");
    // The live call is told (the agent speaks retry / type-it / skip); state is untouched.
    await expect.poll(async () => (await agentLog(id)).gmail_failed.length).toBe(1);
    await expect(gmailItem(page)).toHaveAttribute("aria-label", /Not yet/);

    // Retry from the call with a partial grant: connected, reduced capability stated plainly.
    await oauthVia(page, "Try again", "Allow as maya.r@gmail.com without send");
    await expect(card).toHaveAttribute("data-state", "connected");
    await expect(card.getByTestId("gmail-limited")).toHaveText(/without permission to send email/);
    await expect(rail).not.toContainText("Continue with Google");
  } finally {
    await bot.close();
  }
});

test("wrong account on a call shows the address and a way to switch", async ({ browser, context, page, baseURL }) => {
  const { bot } = await onCall(browser, context, page, baseURL!);
  try {
    const card = page.getByTestId("gmail-card");
    await oauthVia(page, "Continue with Google", "Allow as maya@work.co");
    await expect(card).toHaveAttribute("data-state", "wrong_account");
    await expect(card).toContainText("maya@work.co");
    await expect(card.getByRole("button", { name: "Use a different account" })).toBeVisible();
  } finally {
    await bot.close();
  }
});

test("EC-20 voice: 'Not now' on the card during a call defers Gmail, graduates and ends the call", async ({ browser, context, page, baseURL }) => {
  const { id, bot } = await onCall(browser, context, page, baseURL!);
  try {
    await page.getByTestId("gmail-card").getByRole("button", { name: "Not now" }).click();
    await expect(page.locator("main.home")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Connect Gmail when you’re ready")).toBeVisible();
    // The call says the short line + closing, then hangs up politely.
    await expect.poll(async () => (await agentLog(id)).call_live, { timeout: 30_000 }).toBe(false);
  } finally {
    await bot.close();
  }
});

test("hanging up at the Gmail step keeps it: the card stays and connects after the call", async ({ browser, context, page, baseURL }) => {
  const { id, bot } = await onCall(browser, context, page, baseURL!);
  try {
    const rail = page.getByTestId("call-panel");
    await rail.getByRole("button", { name: "End" }).click();
    await expect(rail).toHaveAttribute("data-status", "ended");
    await expect.poll(async () => (await agentLog(id)).call_live).toBe(false);

    const card = page.getByTestId("gmail-card");
    await expect(card).toHaveAttribute("data-state", "idle");
    await expect(gmailItem(page)).toHaveAttribute("aria-label", /Not yet/);
    await expect(page.getByRole("textbox", { name: TYPE_IT.replace(/…$/, "") })).toHaveCount(0);

    // Failures off the call are not reported as call events.
    await oauthVia(page, "Continue with Google", "Allow as maya.r@gmail.com");
    await expect(card).toHaveAttribute("data-state", "connected");
    await expect(gmailItem(page)).toHaveAttribute("aria-label", /maya\.r@gmail\.com/);
    expect((await agentLog(id)).gmail_failed).toEqual([]);
  } finally {
    await bot.close();
  }
});
