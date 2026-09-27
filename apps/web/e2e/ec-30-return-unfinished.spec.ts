import { expect, test } from "@playwright/test";
import { seedSession } from "./live";

// EC-30 (live driver): a new visit to an unfinished session (need filled, gmail empty)
// resumes at the first missing item without repeating completed steps.
test("EC-30 return visit resumes at the first missing slot", async ({ page, context, baseURL }) => {
  await seedSession(context, baseURL!, {
    node: "gmail",
    slots: { agent_name: "Juno", user_name: "Maya", need: "Inbox triage" },
    transcript: [
      ["assistant", "Hi! I'm your new Persona assistant. What would you like to call your assistant?"],
      ["user", "Juno"],
      ["assistant", "Got it: Juno. And what should I call you?"],
      ["user", "Maya"],
      ["assistant", "Got it: Maya. What's one thing you'd love a hand with this week?"],
      ["user", "Inbox triage"],
      ["assistant", "Got it: Inbox triage. Last step: connect your Gmail with the button below."],
    ],
  });
  await page.goto("/");

  await expect(page.getByRole("button", { name: "Get started" })).toHaveCount(0);
  const thread = page.getByTestId("thread");
  await expect(thread).toContainText("Last step: connect your Gmail");
  await expect(thread.getByText("What would you like to call your assistant?")).toHaveCount(1);
  const checklist = page.locator('[data-testid^="checklist-"]:visible');
  for (const [slot, v] of [["agent_name", "Juno"], ["user_name", "Maya"], ["need", "Inbox triage"]] as const) {
    await expect(checklist.locator(`[data-slot="${slot}"]`)).toHaveAttribute("aria-label", new RegExp(v));
  }
  await expect(checklist.locator('[data-slot="gmail"]')).toHaveAttribute("aria-label", /Not yet/);
  await expect(page.getByPlaceholder("Message Juno…")).toBeEditable();
});
