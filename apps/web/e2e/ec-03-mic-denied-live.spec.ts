import { expect, test } from "@playwright/test";
import { denyMic } from "./call";
import { seedSession } from "./live";

// EC-03 on the real driver: a blocked mic is explained in text, the brain's node doesn't move,
// no call lease is taken and the rail never opens.
test("EC-03 live: mic denied keeps texting at the same node without taking the lease", async ({ page, context, baseURL }) => {
  await seedSession(context, baseURL!, {
    node: "call_offer",
    slots: { agent_name: "Juno" },
    transcript: [["assistant", "Got it: Juno. Want to hop on a quick call for the rest, or keep typing?"]],
  });
  await denyMic(context);
  const calls: string[] = [];
  page.on("request", (r) => r.url().includes("/api/session/call") && calls.push(r.method()));
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto("/");
  await page.getByTestId("call-offer").getByRole("button", { name: "Call Juno" }).click();

  const thread = page.getByTestId("thread");
  await expect(thread.getByText("Your browser’s blocking the mic", { exact: false })).toBeVisible();
  await expect(thread.getByText("Allow microphone access for this site", { exact: false })).toBeVisible();
  await expect(thread.getByText("Or we can just keep texting. So, what should I call you?")).toBeVisible();
  await expect(page.getByTestId("call-panel")).toHaveCount(0);
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(page.getByPlaceholder("Message Juno…")).toBeEditable();
  expect(calls).toEqual([]);
  expect(errors).toEqual([]);

  // Same node: the next typed answer is the brain's user_name turn.
  await page.getByPlaceholder("Message Juno…").fill("Keep texting");
  await page.keyboard.press("Enter");
  await expect(thread.getByText("What should I call you?", { exact: true })).toBeVisible();
});
