import { expect, test } from "@playwright/test";
import { fakeMic, pcStates } from "./call";
import { seed, useSession } from "./live";

// Agent without voice configured answers `answer: null`: the browser hands the lease back and
// stays in text instead of ringing forever. A second attempt must not hit a stale lease (409).
test("lease-only agent: no answer -> friendly note, lease released, no publisher left", async ({ page, context, baseURL }) => {
  const s = await seed({ node: "call_offer", slots: { agent_name: "Juno" }, transcript: [["assistant", "Want to hop on a quick call?"]] });
  await useSession(context, baseURL!, s);
  await fakeMic(context);
  const statuses: number[] = [];
  page.on("response", (r) => r.url().endsWith("/api/session/call") && statuses.push(r.status()));

  await page.goto("/");
  await page.getByTestId("call-offer").getByRole("button", { name: "Call Juno" }).click();
  const note = page.getByTestId("thread").getByText("Voice calls aren’t available right now.", { exact: false });
  await expect(note).toBeVisible();
  await expect(page.getByTestId("call-panel")).toHaveCount(0);
  await expect.poll(() => pcStates(page)).toEqual(["closed"]);

  await page.getByTestId("composer-call").click();
  await expect(note).toHaveCount(2);
  expect(statuses).toEqual([201, 201]);
});
