import { expect, test } from "@playwright/test";

// EC-31: returning after graduating lands in the main experience (not onboarding) with a
// dismissible deferred Gmail prompt.
test("EC-31 graduated return shows home with dismissible deferred prompt", async ({ page }) => {
  await page.goto("/?state=graduation");
  await expect(page.getByRole("heading", { level: 1, name: "You’re all set, Maya." })).toBeVisible();
  await expect(page.getByRole("list", { name: "Setup progress" })).toHaveCount(0);
  await expect(page.getByTestId("thread")).toHaveCount(0);

  const prompt = page.getByTestId("deferred-prompt");
  await expect(prompt).toContainText("Connect Gmail when you’re ready");
  await expect(prompt.getByRole("button", { name: "Connect" })).toBeVisible();
  await prompt.getByRole("button", { name: "Dismiss" }).click();
  await expect(prompt).toHaveCount(0);

  await expect(page.getByPlaceholder("Message Juno…")).toBeEditable();
});
