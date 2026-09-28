import { expect, test } from "@playwright/test";
import { AT_GMAIL, seedSession } from "./live";

// EC-20 (live driver): refusing Gmail is respected; the brain graduates with Gmail deferred.
test("EC-20 refusing Gmail graduates with a deferred Gmail prompt", async ({ page, context, baseURL }) => {
  await seedSession(context, baseURL!, AT_GMAIL);
  await page.goto("/");
  const card = page.getByTestId("gmail-card");
  await expect(card).toHaveAttribute("data-state", "idle");
  await expect(card.getByRole("button", { name: "Not now" })).toBeVisible();

  const composer = page.getByRole("textbox");
  await composer.fill("no, I don't want to connect Gmail");
  await composer.press("Enter");
  await expect(page.getByText("Connect Gmail when you’re ready")).toBeVisible();
  await expect(page.getByRole("button", { name: "Connect" })).toBeVisible();
});

test("EC-20 'Not now' on the card defers Gmail at once and graduates to home (no re-ask)", async ({ page, context, baseURL }) => {
  await seedSession(context, baseURL!, AT_GMAIL);
  await page.goto("/");
  await page.getByTestId("gmail-card").getByRole("button", { name: "Not now" }).click();
  // GMAIL-NOTNOW: straight to "You're all set" with Gmail as the deferred prompt.
  await expect(page.locator("main.home")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { level: 1, name: /all set/ })).toBeVisible();
  await expect(page.getByText("Connect Gmail when you’re ready")).toBeVisible();
  await expect(page.getByTestId("gmail-card")).toHaveCount(0);
});
