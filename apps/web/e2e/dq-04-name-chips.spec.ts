import { expect, test } from "@playwright/test";

// DQ-04 (live driver): the brain's `suggestions` on the agent_name ask render as chips; a chip
// sends its text as an ordinary turn, and the chips go away once the brain moves on.
test("agent-name suggestions render as chips and send text", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Get started" }).click();
  const thread = page.getByTestId("thread");
  await expect(thread).toContainText("what would you like to call me?");
  const chips = thread.locator(".chips");
  await expect(chips.getByRole("button")).toHaveText(["Juno", "Atlas", "Surprise me"]);

  await chips.getByRole("button", { name: "Atlas" }).click();
  await expect(thread.locator(".msg.user")).toHaveText(["Atlas"]); // sent as the user's own bubble
  await expect(thread).toContainText("Got it: Atlas.");
  await expect(chips).toHaveCount(0);
});
