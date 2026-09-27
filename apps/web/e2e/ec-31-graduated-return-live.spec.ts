import { expect, test } from "@playwright/test";
import { seedSession } from "./live";

// EC-31 (live driver): a graduated session with Gmail deferred lands on home, not
// onboarding, with a dismissible deferred prompt. Mock-path twin: ec-31-graduated-landing.
test("EC-31 graduated return (live) lands on home with the deferred Gmail prompt", async ({ page, context, baseURL }) => {
  await seedSession(context, baseURL!, {
    node: "graduated",
    graduated: true,
    deferred: ["gmail"],
    slots: { agent_name: "Juno", user_name: "Maya", need: "Getting your inbox under control", gmail: "skipped" },
    transcript: [["assistant", "You're all set — let's get to work."]],
  });
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1, name: "You’re all set, Maya." })).toBeVisible();
  await expect(page.getByRole("list", { name: "Setup progress" })).toHaveCount(0);
  await expect(page.getByTestId("thread")).toHaveCount(0);
  const prompt = page.getByTestId("deferred-prompt");
  await expect(prompt).toContainText("Connect Gmail when you’re ready");
  await prompt.getByRole("button", { name: "Dismiss" }).click();
  await expect(prompt).toHaveCount(0);
  await expect(page.getByPlaceholder("Message Juno…")).toBeEditable();
});

test("a stale session cookie falls back to the landing page", async ({ page, context, baseURL }) => {
  await context.addCookies([{ name: "persona_session", value: "00000000-0000-0000-0000-000000000000.bogusbogusbogusbogus", url: baseURL!, httpOnly: true }]);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Get started" })).toBeVisible();
});
