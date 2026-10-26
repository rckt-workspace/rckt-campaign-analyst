import { expect, test } from "@playwright/test";
import {
  expectNoSecrets,
  expectNoSessionInScriptStorage,
  resetServices,
  signIn,
  userEmail,
} from "./helpers";

// Saving and listing campaigns through FastAPI, with simulated identities. The analyst and the viewer both belong
// to cliente-a; the outsider belongs to no organization. The analyst test runs first and seeds the list.

const SAVE = "Guardar campaña";
const EXAMPLE_NAME = "Lanzamiento RCKT Growth";
const SAVED_MESSAGE = "Campaña guardada en tu organización.";
const VIEWER_REFUSAL = "Tu rol permite consultar campañas guardadas, pero no registrar nuevas.";
const LOAD_ERROR = "No pudimos cargar las campañas guardadas.";

function savedList(page: import("@playwright/test").Page) {
  return page.getByRole("heading", { name: "Campañas guardadas" }).locator("xpath=ancestor::section[1]");
}

test.beforeEach(async ({ request }) => {
  await resetServices(request);
});

// The chromium project starts signed in as the analyst.
test("analista: guarda la campaña de ejemplo y aparece en la lista de su organización", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("button", { name: "Cargar ejemplo" }).click();
  const save = page.waitForResponse((response) => response.request().method() === "POST" && response.url().startsWith("http://127.0.0.1:3100"));
  await page.getByRole("button", { name: SAVE }).click();
  expect((await save).status()).toBe(200);

  await expect(page.getByText(SAVED_MESSAGE)).toBeVisible();
  await expect(savedList(page).locator("li", { hasText: EXAMPLE_NAME }).first()).toBeVisible();

  const html = await page.content();
  expectNoSecrets(html);
  await expectNoSessionInScriptStorage(page);
});

test("analista: el texto libre guardado se distingue por nombre en la misma lista", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Métricas de campaña").fill(
    ["Campaña: Retargeting Otoño", "Canal: Google Ads", "Inversión: $1.200.000 COP", "Clics: 3.400", "Ventas: 41"].join("\n"),
  );

  await page.getByRole("button", { name: SAVE }).click();

  await expect(page.getByText(SAVED_MESSAGE)).toBeVisible();
  await expect(savedList(page).locator("li", { hasText: "Retargeting Otoño" })).toBeVisible();
  await expect(savedList(page).locator("li", { hasText: EXAMPLE_NAME }).first()).toBeVisible();
});

test("sin datos, guardar está deshabilitado y no llama al servidor", async ({ page }) => {
  await page.goto("/");
  let serverCalled = false;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().startsWith("http://127.0.0.1:3100")) serverCalled = true;
  });

  await expect(page.getByRole("button", { name: SAVE })).toBeDisabled();
  await page.waitForTimeout(500);
  expect(serverCalled).toBe(false);
});

test.describe("visor de cliente", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("client_viewer ve las campañas guardadas pero no puede registrar nuevas", async ({ page }) => {
    await signIn(page, userEmail("viewer"));
    await page.waitForURL((url) => url.pathname === "/");
    await expect(savedList(page).locator("li", { hasText: EXAMPLE_NAME }).first()).toBeVisible();

    await page.getByRole("button", { name: "Cargar ejemplo" }).click();
    await page.getByRole("button", { name: SAVE }).click();

    await expect(page.getByText(VIEWER_REFUSAL)).toBeVisible();
    await expect(page.getByText(SAVED_MESSAGE)).toHaveCount(0);
    expectNoSecrets(await page.content());
  });
});

test.describe("usuario sin organización", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("no ve campañas de ninguna organización", async ({ page }) => {
    await signIn(page, userEmail("outsider"));
    await page.waitForURL((url) => url.pathname === "/");

    await expect(savedList(page).getByText(LOAD_ERROR)).toBeVisible();
    await expect(savedList(page).locator("li")).toHaveCount(0);
    await expect(page.getByText(EXAMPLE_NAME)).toHaveCount(0);
  });
});
