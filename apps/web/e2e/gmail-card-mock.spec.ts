import { expect, test } from "@playwright/test";

// `?state=` Gmail fixtures (MockSessionDriver) still render every card state; the mock's
// canned replies stand in for the brain + OAuth (no popup, no network).
const STATES: [string, string][] = [
  ["gmail-card-idle", "idle"],
  ["gmail-card-connecting", "connecting"],
  ["gmail-card-connected", "connected"],
  ["gmail-card-error", "error"],
  ["gmail-card-wrong-account", "wrong_account"],
  ["gmail-card-partial", "connected"],
];

for (const [state, dataState] of STATES) {
  test(`mock ${state} renders the ${dataState} card`, async ({ page }) => {
    await page.goto(`/?state=${state}&capture=1`);
    await expect(page.getByTestId("gmail-card")).toHaveAttribute("data-state", dataState);
  });
}

test("mock partial grant states the reduced capability", async ({ page }) => {
  await page.goto("/?state=gmail-card-partial&capture=1");
  await expect(page.getByTestId("gmail-limited")).toHaveText(/without permission to send email/);
  await expect(page.getByTestId("gmail-card")).toContainText("maya.r@gmail.com");
});

test("mock error -> Try again -> connecting", async ({ page }) => {
  await page.goto("/?state=gmail-card-error&capture=1");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByTestId("gmail-card")).toHaveAttribute("data-state", "connecting");
});
