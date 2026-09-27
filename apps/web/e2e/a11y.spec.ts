import { expect, test } from "@playwright/test";

// Spec §8 "checks that need more than screenshots" + build identity.
test("build-sha meta is present", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator('meta[name="build-sha"]')).toHaveAttribute("content", /\S+/);
});

test("thread and captions are polite live regions; call status is announced", async ({ page }) => {
  await page.goto("/?state=call-connected");
  await expect(page.getByRole("log")).toHaveAttribute("aria-live", "polite");
  await expect(page.getByLabel("Live captions")).toHaveAttribute("aria-live", "polite");
  await expect(page.getByTestId("call-panel").getByRole("status")).toHaveText("Call connected");
  await expect(page.getByRole("listitem", { name: "What you need: Inbox triage" }).first()).toBeAttached();
  await expect(page.getByRole("listitem", { name: "Your name: Not yet" }).first()).toBeAttached();
});

test("Gmail error box is role=alert", async ({ page }) => {
  await page.goto("/?state=gmail-card-error");
  await expect(page.getByTestId("gmail-card").getByRole("alert")).toContainText("Google didn’t finish signing in");
});

test("mute is a toggle button with aria-pressed", async ({ page }) => {
  await page.goto("/?state=call-connected");
  const mute = page.getByRole("button", { name: "Mute" });
  await expect(mute).toHaveAttribute("aria-pressed", "false");
  await mute.click();
  await expect(page.getByRole("button", { name: "Unmute" })).toHaveAttribute("aria-pressed", "true");
});

test("focus ring is visible when tabbing", async ({ page }) => {
  await page.goto("/?state=call-offer");
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement;
    return getComputedStyle(el).outlineStyle + " " + getComputedStyle(el).outlineWidth;
  });
  expect(outline).toBe("solid 3px");
});

test("Enter sends; the typed turn appears in the thread", async ({ page }) => {
  await page.goto("/?state=call-offer");
  const field = page.getByPlaceholder("Message Juno…");
  await field.fill("hello there");
  await field.press("Enter");
  await expect(page.getByTestId("thread").getByText("hello there")).toBeVisible();
  await expect(field).toHaveValue("");
});
