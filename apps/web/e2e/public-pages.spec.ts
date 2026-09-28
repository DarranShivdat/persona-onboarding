import { expect, test } from "@playwright/test";

// Google OAuth branding needs a public homepage + privacy policy on the authorized domain.
test("homepage (/about) is public, describes the app, and links the privacy policy", async ({ page, context }) => {
  await context.clearCookies();
  const res = await page.goto("/about");
  expect(res?.status()).toBe(200);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("assistant");
  await expect(page.getByRole("link", { name: "Privacy Policy" }).first()).toHaveAttribute("href", "/privacy");
  await expect(page.getByRole("link", { name: "Set up your assistant" })).toHaveAttribute("href", "/");
});

test("privacy policy (/privacy) is public and covers Google user data + Limited Use", async ({ page, context }) => {
  await context.clearCookies();
  const res = await page.goto("/privacy");
  expect(res?.status()).toBe(200);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Privacy Policy");
  for (const scope of ["gmail.readonly", "gmail.modify", "gmail.send"]) await expect(page.getByText(scope)).toBeVisible();
  await expect(page.getByText(/Limited Use requirements/)).toBeVisible();
  await expect(page.getByText(/no longer than 30 days after your last activity/)).toBeVisible();
  await expect(page.getByText(/90 days/)).toHaveCount(0);
  await expect(page.getByRole("link", { name: "darranshivdat1@gmail.com" })).toHaveAttribute("href", "mailto:darranshivdat1@gmail.com");
  await expect(page.getByRole("link", { name: "Google API Services User Data Policy" })).toHaveAttribute(
    "href", "https://developers.google.com/terms/api-services-user-data-policy");
});
