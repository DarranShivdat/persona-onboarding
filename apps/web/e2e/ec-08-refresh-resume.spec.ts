import { expect, test } from "@playwright/test";

// EC-08 (live driver): create a session, chat through the proxy, refresh mid-flow; the
// transcript and checklist come back from the server, with no re-greeting, at `need`.
test("EC-08 refresh mid-onboarding restores transcript and progress", async ({ page, context }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Get started" }).click();
  const thread = page.getByTestId("thread");
  await expect(thread).toContainText("what would you like to call me?");

  const composer = page.getByRole("textbox");
  await composer.fill("Juno");
  await composer.press("Enter");
  await expect(thread).toContainText("Got it: Juno.");
  await composer.fill("no");
  await composer.press("Enter");
  await expect(thread).toContainText("What should I call you?");
  await composer.fill("Maya");
  await composer.press("Enter");
  await expect(thread).toContainText("What's one thing you'd love a hand with this week?");

  const cookie = (await context.cookies()).find((c) => c.name === "persona_session");
  expect(cookie?.httpOnly).toBe(true);
  expect(await page.evaluate(() => document.cookie)).not.toContain("persona_session");

  await page.reload();
  await expect(thread).toContainText("What's one thing you'd love a hand with this week?");
  await expect(thread.getByText("what would you like to call me?")).toHaveCount(1); // no re-greeting
  await expect(thread.getByText("Maya", { exact: true })).toHaveCount(1); // de-duped replay
  const checklist = page.locator('[data-testid^="checklist-"]:visible');
  await expect(checklist.locator('[data-slot="agent_name"]')).toHaveAttribute("aria-label", /Juno/);
  await expect(checklist.locator('[data-slot="user_name"]')).toHaveAttribute("aria-label", /Maya/);
  await expect(page.getByPlaceholder("Message Juno…")).toBeEditable();

  // The same session keeps going after the refresh.
  await composer.fill("Inbox triage");
  await composer.press("Enter");
  await expect(thread).toContainText("Got it: Inbox triage.");
  await expect(page.getByTestId("gmail-card")).toBeVisible();
});
