import path from "node:path";
import { expect, test, type Browser, type Page } from "@playwright/test";
import {
  analyzeButton,
  expectNoSecrets,
  expectReport,
  resetServices,
  setProviderUsage,
  signIn,
  userEmail,
} from "./helpers";

// Requires a freshly prepared test database (python e2e/prepare_database.py, as `bun run test:e2e` does): the cost tests
// assert what the current month holds, so analyses left by an earlier run would change those expectations.
//
// Two kinds of sessions. Tests that sign in as the owner or the viewer start without any cookie: the login page sends a
// signed-in visitor straight to the application, so a preloaded analyst session would hide the sign-in form.
// Analyses that feed the usage report are created by the analyst in a separate browser context.

const ADMIN_PATH = "/administracion";
const ANALYST_STATE = path.resolve("e2e/.auth/analyst.json");
const FREE_TEXT = [
  "Campaña: Lanzamiento RCKT Growth",
  "Objetivo: Ventas",
  "Inversión: $4.850.000 COP",
  "Impresiones: 410.000",
  "Clics: 8.610",
  "Ventas: 94",
  "Ingresos atribuidos: $11.960.000 COP",
].join("\n");

const NO_SESSION = { storageState: { cookies: [], origins: [] } };

test.beforeEach(async ({ request }) => {
  await resetServices(request);
});

function consultButton(page: Page) {
  return page.getByTestId("consult-usage");
}

function reportSection(page: Page) {
  return page.getByTestId("usage-report");
}

function auditRows(page: Page) {
  return page.getByRole("list", { name: "Eventos de auditoría" }).getByRole("listitem");
}

/** The audit trail finished loading (its status line is gone). */
async function waitForTrail(page: Page) {
  await expect(page.getByText("Cargando eventos…")).toHaveCount(0);
}

async function selectOrganization(page: Page, slug: string) {
  const select = page.getByTestId("admin-organization");
  const value = await select.locator("option", { hasText: `(${slug})` }).getAttribute("value");
  expect(value, `organization ${slug} is offered to the owner`).not.toBeNull();
  await select.selectOption(value as string);
  await waitForTrail(page);
}

/** One analysis created by the analyst, in its own context, so the owner's page is not touched. */
async function analyzeAsAnalyst(browser: Browser, baseURL: string | undefined) {
  const context = await browser.newContext({ baseURL, storageState: ANALYST_STATE });
  const page = await context.newPage();
  await page.goto("/");
  await page.getByLabel("Métricas de campaña").fill(FREE_TEXT);
  await analyzeButton(page).click();
  await expectReport(page);
  await context.close();
}

async function openAdministrationAs(page: Page, key: string) {
  await signIn(page, userEmail(key));
  await page.goto(ADMIN_PATH);
  await expect(page.getByRole("heading", { name: "Administración", level: 1 })).toBeVisible();
}

test.describe("acceso del analista (sesión precargada)", () => {
  test("el analista no ve el enlace y, si entra por URL, no tiene acceso ni consultas", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByRole("link", { name: "Administración" })).toHaveCount(0);

    await page.goto(ADMIN_PATH);
    await expect(page.getByText("Sin acceso de administración")).toBeVisible();
    await expect(consultButton(page)).toHaveCount(0);
    await expect(reportSection(page)).toHaveCount(0);
    await expect(page.getByRole("list", { name: "Eventos de auditoría" })).toHaveCount(0);
  });
});

test.describe("acceso sin sesión previa", () => {
  test.use(NO_SESSION);

  test("sin sesión, la página de administración redirige a iniciar sesión", async ({ page }) => {
    await page.goto(ADMIN_PATH);
    await expect(page).toHaveURL(/\/login/);
    await expect(consultButton(page)).toHaveCount(0);
  });

  test("el owner ve el enlace discreto a Administración y llega al panel", async ({ page }) => {
    await signIn(page, userEmail("owner"));
    await page.goto("/");

    const link = page.getByRole("link", { name: "Administración" });
    await expect(link).toBeVisible();
    await link.click();

    await expect(page).toHaveURL(/\/administracion$/);
    await expect(page.getByRole("heading", { name: "Administración", level: 1 })).toBeVisible();
    await expect(page.getByText("Consumo y auditoría de campañas")).toBeVisible();
  });

  test("el client_viewer no tiene acceso por URL", async ({ page }) => {
    await signIn(page, userEmail("viewer"));
    // Wait for the session to exist before going to the protected page; otherwise the route sends the visitor to login.
    await expect(page).toHaveURL(/\/$/);
    await page.goto(ADMIN_PATH);

    await expect(page.getByText("Sin acceso de administración")).toBeVisible();
    await expect(consultButton(page)).toHaveCount(0);
  });
});

test.describe("consumo y auditoría como owner", () => {
  test.use(NO_SESSION);

  test("consulta mensual: el owner ve el informe y los costes desconocidos nunca aparecen como cero", async ({
    page,
    browser,
    baseURL,
  }) => {
    await analyzeAsAnalyst(browser, baseURL);
    await openAdministrationAs(page, "owner");
    await selectOrganization(page, "cliente-a");

    await expect(consultButton(page)).toHaveText("Consultar consumo");
    await consultButton(page).click();

    const report = reportSection(page);
    await expect(report).toBeVisible();
    await expect(report.getByText("Análisis solicitados")).toBeVisible();
    await expect(report.getByText("Llamadas al proveedor de IA")).toBeVisible();
    await expect(page.getByText("Ningún coste reportado")).toBeVisible();
    await expect(report.getByText("Desconocido").first()).toBeVisible();
    await expect(report.getByText("0.00000000")).toHaveCount(0);
    await expect(consultButton(page)).toHaveText("Actualizar consumo");
    expectNoSecrets(await page.content());
  });

  test("costes parciales: el total es un mínimo, marcado como parcial, con las llamadas sin coste contadas", async ({
    page,
    browser,
    baseURL,
    request,
  }) => {
    await setProviderUsage(request, "partial");
    await analyzeAsAnalyst(browser, baseURL);
    await analyzeAsAnalyst(browser, baseURL);
    await openAdministrationAs(page, "owner");
    await selectOrganization(page, "cliente-a");
    await consultButton(page).click();

    const report = reportSection(page);
    await expect(report).toBeVisible();
    await expect(page.getByTestId("cost-completeness")).toHaveText(
      "Coste parcial: el total no incluye todas las llamadas",
    );
    // The two analyses are calls 0 and 1 of the simulated provider: only call 0 reports a cost. The total is that one
    // exact value, and the other call is counted as without cost, never as zero.
    await expect(report.getByText("0.00012345", { exact: true })).toBeVisible();
    await expect(report.getByText("Llamadas con coste desconocido")).toBeVisible();
    await expect(report.getByText("Parcial").first()).toBeVisible();
    await expect(report.getByText("No se cuentan como cero")).toBeVisible();
  });

  test("un mes sin actividad muestra un aviso explícito, no un fallo", async ({ page }) => {
    await openAdministrationAs(page, "owner");
    await selectOrganization(page, "cliente-a");
    await page.getByTestId("admin-period").selectOption("2025-11");
    await consultButton(page).click();

    await expect(page.getByText("Sin actividad registrada en 2025-11 (UTC)")).toBeVisible();
    await expect(reportSection(page)).toBeVisible();
    await expect(page.getByText("No se pudo", { exact: false })).toHaveCount(0);
  });

  test("cambiar de organización no consulta el consumo ni registra accesos", async ({ page }) => {
    await openAdministrationAs(page, "owner");
    await selectOrganization(page, "cliente-a");
    await consultButton(page).click();
    await expect(reportSection(page)).toBeVisible();
    await waitForTrail(page);
    const before = await page.getByText("Informe de consumo consultado").count();

    await selectOrganization(page, "cliente-b");
    await expect(reportSection(page)).toHaveCount(0);
    await expect(consultButton(page)).toHaveText("Consultar consumo");
    await selectOrganization(page, "cliente-a");

    await expect(reportSection(page)).toHaveCount(0);
    expect(await page.getByText("Informe de consumo consultado").count()).toBe(before);
  });

  test("un doble clic en Consultar consumo genera una sola solicitud auditada", async ({ page }) => {
    await openAdministrationAs(page, "owner");
    await selectOrganization(page, "cliente-a");
    await waitForTrail(page);
    const before = await page.getByText("Informe de consumo consultado").count();

    // Two presses in quick succession: the second one must not start a second request.
    await consultButton(page).dblclick();
    await expect(reportSection(page)).toBeVisible();
    await waitForTrail(page);

    expect(await page.getByText("Informe de consumo consultado").count()).toBe(before + 1);
  });

  test("una sola pulsación registra exactamente un acceso en la auditoría", async ({ page }) => {
    await openAdministrationAs(page, "owner");
    await selectOrganization(page, "cliente-a");
    await waitForTrail(page);
    const before = await page.getByText("Informe de consumo consultado").count();

    await consultButton(page).click();
    await expect(reportSection(page)).toBeVisible();
    await waitForTrail(page);

    expect(await page.getByText("Informe de consumo consultado").count()).toBe(before + 1);
  });

  test("paginación de auditoría: orden descendente por id, cursor y sin repeticiones", async ({
    page,
    browser,
    baseURL,
  }) => {
    // Four analyses by the analyst in cliente-a: with the membership events, the trail spans more than one page.
    for (let index = 0; index < 4; index += 1) {
      await analyzeAsAnalyst(browser, baseURL);
    }
    await openAdministrationAs(page, "owner");
    await selectOrganization(page, "cliente-a");
    await waitForTrail(page);

    const loadMore = page.getByRole("button", { name: "Cargar eventos anteriores" });
    await expect(loadMore).toBeVisible();
    await expect(auditRows(page)).toHaveCount(10);
    await loadMore.click();
    // Loading more keeps the page visible: wait for the eleventh row to arrive, not for the loading text.
    await expect(auditRows(page).nth(10)).toBeVisible();

    const ids = await page
      .getByRole("list", { name: "Eventos de auditoría" })
      .getByText(/^Evento \d+$/)
      .allTextContents();
    const numbers = ids.map((text) => Number(text.replace("Evento ", "")));
    expect(numbers.length).toBeGreaterThan(10);
    expect(new Set(numbers).size).toBe(numbers.length);
    expect([...numbers].sort((a, b) => b - a)).toEqual(numbers);
    // The shared test database keeps the events of earlier runs, so more pages may still exist. The button is allowed
    // to stay; what this test checks is that each page continues from the cursor without repeats or reordering.
  });

  test("el enlace Volver al análisis lleva al módulo principal", async ({ page }) => {
    await openAdministrationAs(page, "owner");
    await page.getByRole("link", { name: "Volver al análisis" }).click();

    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("tab", { name: "Pegar métricas" })).toBeVisible();
  });

  test("en un viewport de teléfono no hay desplazamiento horizontal", async ({
    page,
    browser,
    baseURL,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await analyzeAsAnalyst(browser, baseURL);
    await openAdministrationAs(page, "owner");
    await selectOrganization(page, "cliente-a");
    await consultButton(page).click();
    await expect(reportSection(page)).toBeVisible();
    await waitForTrail(page);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
