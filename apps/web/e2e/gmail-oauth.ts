import { expect, type Page } from "@playwright/test";

// Drives the mock Google consent screen (stub agent `/__oauth/authorize`) from a card button.
export async function oauthVia(page: Page, button: string, choice: string | null) {
  const popupP = page.waitForEvent("popup");
  await page.getByTestId("gmail-card").getByRole("button", { name: button }).click();
  const popup = await popupP;
  await expect(popup.getByRole("heading")).toBeVisible();
  if (choice === null) return popup;
  const closed = popup.waitForEvent("close");
  await popup.getByRole("link", { name: choice, exact: true }).click();
  await closed;
  return popup;
}

export const gmailItem = (page: Page) => page.locator('[data-testid^="checklist-"]:visible [data-slot="gmail"]');
