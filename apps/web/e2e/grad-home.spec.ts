import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { AGENT_URL, seedSession, type Seed } from "./live";

// GRAD-001: every control on the graduation (home) screen does something. Live driver +
// /api/session proxy + stub agent (a canned mirror of brain/home.py). With a real agent behind
// PERSONA_E2E_AGENT_URL (no `/__test` seeding) only the real-agent variant runs.
const GRADUATED: Seed = {
  node: "graduated",
  graduated: true,
  deferred: ["gmail"],
  slots: { agent_name: "Juno", user_name: "Maya", need: "Getting your inbox under control", gmail: "skipped" },
  transcript: [["assistant", "You're all set, Maya. Juno has noted what you'd like help with: getting your inbox under control."]],
};

async function isStub(): Promise<boolean> {
  const r = await fetch(`${AGENT_URL}/health`).catch(() => null);
  return !!r?.ok && !!((await r.json()) as { stub?: boolean }).stub;
}

async function home(page: Page, context: BrowserContext, baseURL: string) {
  test.skip(!(await isStub()), "seeding needs the stub agent");
  await seedSession(context, baseURL, GRADUATED);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "You’re all set, Maya." })).toBeVisible();
}

async function send(page: Page, text: string, agent = "Juno") {
  const box = page.getByPlaceholder(`Message ${agent}…`);
  await box.fill(text);
  await box.press("Enter");
}

test("home composer: a message gets a scoped reply in the home thread", async ({ page, context, baseURL }) => {
  await home(page, context, baseURL!);
  await expect(page.getByTestId("home-thread")).toHaveCount(0); // the graduation summary is the headline, not a bubble
  await send(page, "Can you check my inbox?");
  const thread = page.getByTestId("home-thread");
  await expect(thread.locator(".msg.user")).toHaveText("Can you check my inbox?");
  await expect(thread.locator(".msg.agent").last()).toContainText("doesn't carry out tasks yet");
  await expect(thread).not.toContainText("all set");
});

test("home chat edit: 'call me Darran' updates the greeting", async ({ page, context, baseURL }) => {
  await home(page, context, baseURL!);
  await send(page, "call me Darran");
  await expect(page.getByRole("heading", { level: 1, name: "You’re all set, Darran." })).toBeVisible();
  await expect(page.getByTestId("home-thread").locator(".msg.agent")).toHaveText("Thanks, Darran it is.");
});

test("tap-to-edit: Enter saves, Esc cancels, validator errors show inline", async ({ page, context, baseURL }) => {
  await home(page, context, baseURL!);
  // Esc cancels without saving.
  await page.getByTestId("edit-user_name").click();
  const input = page.getByTestId("edit-user_name-input");
  await input.fill("Nobody");
  await input.press("Escape");
  await expect(input).toHaveCount(0);
  await expect(page.getByTestId("edit-user_name")).toBeFocused();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("You’re all set, Maya.");

  // The agent's validator refuses: inline error, nothing saved.
  await page.getByTestId("edit-user_name").click();
  await input.fill("12345");
  await input.press("Enter");
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveText("That doesn't look like a name. Try letters only.");
  await expect(input).toHaveAttribute("aria-invalid", "true");

  // Enter saves; the greeting re-renders from the brain's new state.
  await input.fill("Darran");
  await input.press("Enter");
  await expect(page.getByRole("heading", { level: 1, name: "You’re all set, Darran." })).toBeVisible();
  await expect(page.getByTestId("edit-user_name")).toContainText("Darran");

  // Assistant name via keyboard only; need via the Save button.
  await page.getByTestId("edit-agent_name").focus();
  await page.keyboard.press("Enter");
  await page.getByTestId("edit-agent_name-input").fill("Nova");
  await page.keyboard.press("Enter");
  await expect(page.getByPlaceholder("Message Nova…")).toBeVisible();
  await expect(page.getByText("Nova is ready.")).toBeVisible();
  await page.getByTestId("edit-need").click();
  await page.getByTestId("edit-need-input").fill("Planning my week");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId("edit-need")).toContainText("Planning my week");
});

test("dismiss hides the deferred prompt by keyboard and stays hidden for the visit", async ({ page, context, baseURL }) => {
  await home(page, context, baseURL!);
  const prompt = page.getByTestId("deferred-prompt");
  await prompt.getByRole("button", { name: "Dismiss" }).focus();
  await page.keyboard.press("Enter");
  await expect(prompt).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name: "You’re all set, Maya." })).toBeVisible();
  await expect(page.getByTestId("deferred-prompt")).toHaveCount(0);
  // Gmail stays reachable from its tile after the prompt is dismissed.
  await expect(page.getByTestId("gmail-tile").getByRole("button", { name: "Connect Gmail" })).toBeVisible();
});

test("Connect Gmail on home runs OAuth and the tile shows connected", async ({ page, context, baseURL }) => {
  await home(page, context, baseURL!);
  const tile = page.getByTestId("gmail-tile");
  await expect(tile).toHaveCount(0); // the deferred prompt holds the only Connect CTA
  await page.getByTestId("deferred-prompt").getByRole("button", { name: "Dismiss" }).click();
  await expect(tile).toHaveAttribute("data-state", "idle");
  const popupP = page.waitForEvent("popup");
  await tile.getByRole("button", { name: "Connect Gmail" }).click();
  const popup = await popupP;
  await expect(popup).toHaveURL(/\/__oauth\/authorize\?/);
  await expect(tile).toHaveAttribute("data-state", "connecting");
  const closed = popup.waitForEvent("close");
  await popup.getByRole("link", { name: "Allow as maya.r@gmail.com", exact: true }).click();
  await closed;
  await expect(tile).toHaveAttribute("data-state", "connected");
  await expect(tile).toContainText("maya.r@gmail.com");
  await expect(page.getByTestId("deferred-prompt")).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("You’re all set, Maya.");
});

test("Connect Gmail cancelled on home shows Try again", async ({ page, context, baseURL }) => {
  await home(page, context, baseURL!);
  const tile = page.getByTestId("gmail-tile");
  const popupP = page.waitForEvent("popup");
  await page.getByTestId("deferred-prompt").getByRole("button", { name: "Connect" }).click();
  const popup = await popupP;
  const closed = popup.waitForEvent("close");
  await popup.getByRole("link", { name: "Cancel" }).click();
  await closed;
  await expect(tile).toHaveAttribute("data-state", "error");
  await expect(tile.getByRole("button", { name: "Try again" })).toBeVisible();
});

test("real agent: graduate by chat, then home replies and tap-to-edit work", async ({ page }) => {
  test.skip(await isStub(), "runs only against a real agent (PERSONA_E2E_AGENT_URL)");
  test.setTimeout(60_000);
  await page.goto("/");
  await page.getByRole("button", { name: "Get started" }).click();
  const box = page.locator(".composer textarea");
  const done = page.getByRole("heading", { level: 1, name: /all set/ });
  for (const t of ["Nova", "no thanks", "Ada", "Triage my inbox every morning", "not now", "not now", "not now"]) {
    if (await done.isVisible()) break;
    await box.fill(t);
    await box.press("Enter");
    await page.waitForTimeout(700);
  }
  await expect(page.getByRole("heading", { level: 1, name: "You’re all set, Ada." })).toBeVisible();
  await box.fill("can you check my inbox?");
  await box.press("Enter");
  await expect(page.getByTestId("home-thread").locator(".msg.agent").last()).toContainText("can't");
  await page.getByTestId("edit-user_name").click();
  await page.getByTestId("edit-user_name-input").fill("Darran");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { level: 1, name: "You’re all set, Darran." })).toBeVisible();
});
