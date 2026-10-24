import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test as setup } from "@playwright/test";
import { resetServices, signIn, userEmail } from "./helpers";

const STORAGE = path.resolve("e2e/.auth/analyst.json");

setup("sign in as the analyst once and keep the session cookies", async ({ page, request }) => {
  await resetServices(request);
  await signIn(page, userEmail("analyst"));
  await expect(page).toHaveURL("/");
  await expect(page.getByText(userEmail("analyst"))).toBeVisible();

  mkdirSync(path.dirname(STORAGE), { recursive: true });
  await page.context().storageState({ path: STORAGE });
});
