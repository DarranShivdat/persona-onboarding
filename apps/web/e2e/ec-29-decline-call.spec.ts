import { expect, test } from "@playwright/test";

// EC-29: declining the call continues in text and keeps the call button available.
test("EC-29 declining the call keeps the composer call button", async ({ page }) => {
  await page.goto("/?state=call-offer");
  await expect(page.getByTestId("composer-call")).toBeVisible();
  await page.getByTestId("call-offer").getByRole("button", { name: "Keep texting" }).click();

  await expect(page.getByTestId("thread").getByText("Texting it is. What should I call you?")).toBeVisible();
  await expect(page.getByTestId("call-panel")).toHaveCount(0);
  const callBtn = page.getByTestId("composer-call");
  await expect(callBtn).toBeVisible();
  await expect(callBtn).toHaveAccessibleName("Call Juno");
  await expect(page.getByPlaceholder("Message Juno…")).toBeEditable();
});
