import { expect, test } from "@playwright/test";
import { gmailItem, oauthVia } from "./gmail-oauth";
import { AT_GMAIL, seedSession } from "./live";

// EC-21 (live driver + mock Google): cancelled consent -> error card with a retry path;
// Gmail stays empty until a successful, verified OAuth.
test("EC-21 cancelled consent shows the error card, and Try again connects", async ({ page, context, baseURL }) => {
  await seedSession(context, baseURL!, AT_GMAIL);
  await page.goto("/");
  const card = page.getByTestId("gmail-card");
  await expect(card).toHaveAttribute("data-state", "idle");

  const popupP = page.waitForEvent("popup");
  await card.getByRole("button", { name: "Continue with Google" }).click();
  const popup = await popupP;
  await expect(card).toHaveAttribute("data-state", "connecting");
  await expect(card.getByRole("button", { name: "Waiting for Google…" })).toHaveAttribute("aria-busy", "true");
  // The authorize request carries the decided testing-mode parameters (ARCHITECTURE §11).
  const req = popup.locator("[data-scope]");
  await expect(req).toHaveAttribute("data-scope", /openid email profile .*gmail\.readonly .*gmail\.modify .*gmail\.send/);
  await expect(req).toHaveAttribute("data-access-type", "offline");
  await expect(req).toHaveAttribute("data-prompt", "consent");
  await expect(req).toHaveAttribute("data-include-granted", "true");
  const closed = popup.waitForEvent("close");
  await popup.getByRole("link", { name: "Cancel" }).click();
  await closed;

  await expect(card).toHaveAttribute("data-state", "error");
  await expect(card.getByRole("alert")).toContainText("Google didn’t finish signing in");
  await expect(gmailItem(page)).toHaveAttribute("aria-label", /Not yet/);

  await oauthVia(page, "Try again", "Allow as maya.r@gmail.com");
  await expect(card).toHaveAttribute("data-state", "connected");
  await expect(card).toContainText("Maya Reyes");
  await expect(card).toContainText("maya.r@gmail.com");
  await expect(gmailItem(page)).toHaveAttribute("aria-label", /maya\.r@gmail\.com/);
  await expect(page.getByTestId("thread")).toContainText("Got it, connected as maya.r@gmail.com.");
});

test("EC-21 closing the Google window without finishing shows the error card", async ({ page, context, baseURL }) => {
  await seedSession(context, baseURL!, AT_GMAIL);
  await page.goto("/");
  const card = page.getByTestId("gmail-card");
  const popup = await oauthVia(page, "Continue with Google", null);
  await expect(card).toHaveAttribute("data-state", "connecting");
  await popup.close();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(card).toHaveAttribute("data-state", "error");
  await expect(gmailItem(page)).toHaveAttribute("aria-label", /Not yet/);
});

test("partial grant connects with the reduced capability stated plainly", async ({ page, context, baseURL }) => {
  await seedSession(context, baseURL!, AT_GMAIL);
  await page.goto("/");
  const card = page.getByTestId("gmail-card");
  await oauthVia(page, "Continue with Google", "Allow as maya.r@gmail.com without send");
  await expect(card).toHaveAttribute("data-state", "connected");
  await expect(card.getByTestId("gmail-limited")).toHaveText(/without permission to send email/);
  await expect(gmailItem(page)).toHaveAttribute("aria-label", /maya\.r@gmail\.com/);

  await oauthVia(page, "Allow full access", "Allow as maya.r@gmail.com");
  await expect(card).toHaveAttribute("data-state", "connected");
  await expect(card.getByTestId("gmail-limited")).toHaveCount(0);
});
