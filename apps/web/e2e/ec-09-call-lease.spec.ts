import { expect, test } from "@playwright/test";
import { fakeMic, pcStates, startBot } from "./call";
import { seed, useSession } from "./live";

// EC-02 / EC-09: one live call per session. A second device sees "in another tab" with an
// explicit take-over; taking over ends the first leg before this one publishes.
const AT_OFFER = {
  node: "call_offer",
  slots: { agent_name: "Juno" },
  transcript: [["assistant", "Got it: Juno. Want to hop on a quick call for the rest, or keep typing?"]] as [ "assistant", string][],
};

test("EC-09 second device sees the live call and takes it over", async ({ browser, baseURL }) => {
  const s = await seed(AT_OFFER);
  const bot = await startBot(browser, s.id);
  const devA = await browser.newContext();
  const devB = await browser.newContext();
  try {
    for (const c of [devA, devB]) {
      await useSession(c, baseURL!, s);
      await fakeMic(c);
    }
    // Device A places the call: real offer -> stub signaling -> bot answer -> connected.
    const a = await devA.newPage();
    await a.goto("/");
    await a.getByTestId("call-offer").getByRole("button", { name: "Call Juno" }).click();
    await expect(a.getByTestId("call-panel")).toHaveAttribute("data-status", "connected", { timeout: 15_000 });
    await expect.poll(() => bot.states()).toEqual(["connected"]);

    // Device B opens the same session: in-progress surface, no second publisher.
    const b = await devB.newPage();
    await b.goto("/");
    const railB = b.getByTestId("call-panel");
    await expect(railB).toHaveAttribute("data-status", "elsewhere");
    await expect(b.getByTestId("call-status")).toHaveText("On a call in another tab");
    await expect(b.getByTestId("call-offer")).toHaveCount(0);
    expect(await pcStates(b)).toEqual([]);

    // Explicit take-over: A's leg ends and closes, B connects.
    await railB.getByRole("button", { name: "Take over here" }).click();
    await expect(railB).toHaveAttribute("data-status", "connected", { timeout: 15_000 });
    await expect(a.getByTestId("call-panel")).toHaveAttribute("data-status", "ended");
    await expect.poll(() => pcStates(a)).toEqual(["closed"]);
    expect(await pcStates(b)).toEqual(["connected"]);

    // Hanging up releases the lease: A can call again without a 409.
    await railB.getByRole("button", { name: "End" }).click();
    await expect(railB).toHaveAttribute("data-status", "ended");
    await expect.poll(() => pcStates(b)).toEqual(["closed"]);
  } finally {
    await devA.close();
    await devB.close();
    await bot.close();
  }
});

test("EC-09 opening while a call is live elsewhere shows it; keep typing stays in text", async ({ page, context, baseURL }) => {
  const s = await seed({ ...AT_OFFER, call: true });
  await useSession(context, baseURL!, s);
  await fakeMic(context);
  await page.goto("/");
  const rail = page.getByTestId("call-panel");
  await expect(rail).toHaveAttribute("data-status", "elsewhere");
  await expect(rail.getByRole("button", { name: "Take over here" })).toBeVisible();
  await rail.getByRole("button", { name: "Keep typing" }).click();
  await expect(page.getByTestId("call-panel")).toHaveCount(0);
  await expect(page.getByPlaceholder("Message Juno…")).toBeEditable();
  expect(await pcStates(page)).toEqual([]);
});
