import { expect, test } from "@playwright/test";
import { gmailItem, oauthVia } from "./gmail-oauth";
import { AT_GMAIL, seedSession } from "./live";

// EC-22 (live driver + mock Google): a Workspace/other account is shown with its address;
// the user switches to another account through Google's account chooser.
test("EC-22 wrong account shows the address and switching re-runs OAuth", async ({ page, context, baseURL }) => {
  await seedSession(context, baseURL!, AT_GMAIL);
  await page.goto("/");
  const card = page.getByTestId("gmail-card");
  await oauthVia(page, "Continue with Google", "Allow as maya@work.co");
  await expect(card).toHaveAttribute("data-state", "wrong_account");
  await expect(card).toContainText("maya@work.co");
  await expect(card).toContainText("Wrong account?");
  await expect(gmailItem(page)).toHaveAttribute("aria-label", /maya@work\.co/);

  const popupP = page.waitForEvent("popup");
  await card.getByRole("button", { name: "Use a different account" }).click();
  const popup = await popupP;
  await expect(card).toHaveAttribute("data-state", "connecting");
  await expect(popup.getByRole("heading", { name: "Choose an account" })).toBeVisible();
  const closed = popup.waitForEvent("close");
  await popup.getByRole("link", { name: "Allow as maya.r@gmail.com", exact: true }).click();
  await closed;

  await expect(card).toHaveAttribute("data-state", "connected");
  await expect(card).toContainText("maya.r@gmail.com");
  await expect(gmailItem(page)).toHaveAttribute("aria-label", /maya\.r@gmail\.com/);
});

test("EC-22 'Keep this one' keeps the connected account", async ({ page, context, baseURL }) => {
  await seedSession(context, baseURL!, AT_GMAIL);
  await page.goto("/");
  const card = page.getByTestId("gmail-card");
  await oauthVia(page, "Continue with Google", "Allow as maya@work.co");
  await expect(card).toHaveAttribute("data-state", "wrong_account");
  await card.getByRole("button", { name: "Keep this one" }).click();
  await expect(card).toHaveAttribute("data-state", "connected");
  await expect(gmailItem(page)).toHaveAttribute("aria-label", /maya@work\.co/);
});
