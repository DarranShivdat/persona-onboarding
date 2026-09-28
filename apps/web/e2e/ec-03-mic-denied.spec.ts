import { expect, test } from "@playwright/test";

// EC-03: browser denies the microphone -> friendly text explanation, same node, no rail.
test("EC-03 mic denied falls back to text at the same node", async ({ page }) => {
  await page.addInitScript(() => {
    const deny = () => Promise.reject(new DOMException("Permission denied", "NotAllowedError"));
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: deny }, configurable: true });
  });
  await page.goto("/?state=call-offer");
  await page.getByTestId("call-offer").getByRole("button", { name: "Call Juno" }).click();

  const thread = page.getByTestId("thread");
  await expect(thread.getByText("Your browser’s blocking the mic", { exact: false })).toBeVisible();
  await expect(thread.getByText("Allow microphone access for this site", { exact: false })).toBeVisible();
  await expect(thread.getByText("Or we can just keep texting. So, what should I call you?")).toBeVisible();
  await expect(page.getByTestId("call-panel")).toHaveCount(0);
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(page.getByTestId("composer-call")).toBeVisible();
  await expect(page.getByPlaceholder("Message Juno…")).toBeEditable();
});
