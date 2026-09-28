import { expect, test } from "@playwright/test";
import { AGENT_URL, seed, useSession } from "./live";

// RESET-001: "Start over" (header, with confirm) and `/?reset=1` both drop the cookie session and
// land on a fresh agent_name step; a stray tap + Cancel changes nothing.
const GRAD = {
  node: "graduated",
  graduated: true,
  slots: { agent_name: "Juno", user_name: "Maya", need: "Inbox triage", gmail: "skipped" },
  deferred: ["gmail"],
  transcript: [["assistant", "You're all set, Maya. Juno's first job: inbox triage."]] as ["assistant", string][],
};

async function isStub(): Promise<boolean> {
  const r = await fetch(`${AGENT_URL}/health`).catch(() => null);
  return !!r?.ok && !!((await r.json()) as { stub?: boolean }).stub;
}
test.beforeEach(async () => test.skip(!(await isStub()), "seeding needs the stub agent"));

async function cookieOf(page: import("@playwright/test").Page) {
  return (await page.context().cookies()).find((c) => c.name === "persona_session")?.value;
}

async function expectFresh(page: import("@playwright/test").Page) {
  const thread = page.getByTestId("thread");
  await expect(thread.locator('[data-from="agent"]').first()).toBeVisible({ timeout: 20_000 });
  await expect(thread.locator('[data-from="user"]')).toHaveCount(0);
  await expect(page.locator("main.home")).toHaveCount(0);
  await expect(page.locator('[data-testid^="checklist-"]:visible [data-slot="agent_name"]')).toHaveAttribute("aria-label", "Assistant name: Not yet");
}

test("Start over from the graduation screen: Cancel keeps it, confirm starts fresh", async ({ page, context, baseURL }) => {
  const ref = await seed(GRAD);
  await useSession(context, baseURL!, ref);
  await page.goto("/");
  await expect(page.locator("main.home")).toBeVisible();
  const before = await cookieOf(page);

  await page.getByRole("button", { name: "Start over" }).click();
  const dlg = page.getByTestId("start-over-confirm");
  await expect(dlg).toBeVisible();
  await expect(dlg).toContainText("Start over?");
  await dlg.getByRole("button", { name: "Cancel" }).click();
  await expect(dlg).toHaveCount(0);
  await page.reload();
  await expect(page.locator("main.home")).toBeVisible();          // nothing was wiped

  await page.getByRole("button", { name: "Start over" }).click();
  await page.getByTestId("start-over-confirm").getByRole("button", { name: "Yes, start over" }).click();
  await expectFresh(page);
  expect(await cookieOf(page)).not.toBe(before);
  await page.reload();
  await expectFresh(page);                                          // the reset sticks across reloads
});

test("Start over mid-flow (chat) works too", async ({ page, context, baseURL }) => {
  const ref = await seed({ node: "need", slots: { agent_name: "Juno", user_name: "Maya" }, transcript: [["user", "Maya"], ["assistant", "Nice to meet you, Maya."]] });
  await useSession(context, baseURL!, ref);
  await page.goto("/");
  await page.getByRole("button", { name: "Start over" }).click();
  await page.getByTestId("start-over-confirm").getByRole("button", { name: "Yes, start over" }).click();
  await expectFresh(page);
});

test("/?reset=1 drops the session and lands on a fresh agent_name step", async ({ page, context, baseURL }) => {
  const ref = await seed(GRAD);
  await useSession(context, baseURL!, ref);
  const before = `${ref.id}.${ref.token}`;
  await page.goto("/?reset=1");
  await expect(page).toHaveURL(/\/$/);
  await expectFresh(page);
  expect(await cookieOf(page)).not.toBe(before);
});
