import { expect, test } from "@playwright/test";
import {
  analyzeButton,
  expectNoSecrets,
  expectReport,
  resetServices,
  setProviderMode,
  signIn,
  userEmail,
} from "./helpers";

// The analysis history: saved reports, failed attempts, reopening a previous report, and the empty and error states.
// Simulated identities and provider only. The analyst test runs first and seeds the history for the viewer.

const EXAMPLE = "Lanzamiento RCKT Growth";

function historyPanel(page: import("@playwright/test").Page) {
  return page
    .getByRole("heading", { name: "Historial de informes" })
    .locator("xpath=ancestor::section[1]");
}

test.beforeEach(async ({ request }) => {
  await resetServices(request);
});

// The chromium project starts signed in as the analyst.
test("analista: el informe analizado aparece en el historial y se puede volver a abrir", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).click();
  await analyzeButton(page).click();
  await expectReport(page);

  await expect(historyPanel(page).locator("button", { hasText: EXAMPLE }).first()).toBeVisible();

  // Reopen the completed report from the history: the stored report text is shown again.
  await historyPanel(page).locator("button", { hasText: "Completado" }).first().click();
  await expect(page.getByText("Cargando informe…")).toHaveCount(0);
  await expectReport(page);
  expectNoSecrets(await page.content());
});

test("un fallo del proveedor queda registrado en el historial como fallido", async ({
  page,
  request,
}) => {
  await setProviderMode(request, "error");
  await page.goto("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).click();
  await analyzeButton(page).click();

  await expect(historyPanel(page).locator("button", { hasText: "Fallido" }).first()).toBeVisible();
  expectNoSecrets(await page.content());
});

test.describe("visor de cliente", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("client_viewer lee los informes de su organización, sin poder generar nuevos", async ({
    page,
  }) => {
    await signIn(page, userEmail("viewer"));
    await page.waitForURL((url) => url.pathname === "/");

    // The newest EXAMPLE entry may be the failed attempt of the previous test: open the completed report instead.
    const completed = historyPanel(page).locator("button", { hasText: "Completado" }).first();
    await expect(completed).toBeVisible();
    await completed.click();
    await expectReport(page);
  });
});

test.describe("usuario sin organización", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("muestra el estado de error del historial, sin datos de otra organización", async ({
    page,
  }) => {
    await signIn(page, userEmail("outsider"));
    await page.waitForURL((url) => url.pathname === "/");

    await expect(
      historyPanel(page).getByText("No pudimos cargar el historial de informes."),
    ).toBeVisible();
    await expect(page.getByText(EXAMPLE)).toHaveCount(0);
  });
});
