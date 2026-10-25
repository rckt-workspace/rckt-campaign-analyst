import { expect, test } from "@playwright/test";
import {
  analyzeButton,
  expectNoSecrets,
  expectNoSessionInScriptStorage,
  expectReport,
  expectSessionCookiesAreHttpOnly,
  providerCalls,
  resetServices,
  setProviderMode,
  userEmail,
} from "./helpers";

// Every test here runs as the analyst, whose session is created once by auth.setup.ts.

const FREE_TEXT = [
  "Campaña: Lanzamiento RCKT Growth",
  "Objetivo: Ventas",
  "Inversión: $4.850.000 COP",
  "Impresiones: 410.000",
  "Clics: 8.610",
  "Ventas: 94",
  "Ingresos atribuidos: $11.960.000 COP",
].join("\n");

test.beforeEach(async ({ request }) => {
  await resetServices(request);
});

test("carga inicial: la aplicación muestra la sesión y el botón está deshabilitado sin datos", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle(/RCKT Campaign Analyst/);
  await expect(page.getByText(userEmail("analyst"))).toBeVisible();
  await expect(page.getByRole("tab", { name: "Pegar métricas" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Formulario rápido" })).toBeVisible();
  await expect(analyzeButton(page)).toBeDisabled();
});

test("texto libre: el informe llega desde FastAPI con sus secciones ejecutivas", async ({ page, request }) => {
  await page.goto("/");
  await page.getByLabel("Métricas de campaña").fill(FREE_TEXT);

  const serverResponse = page.waitForResponse((response) => response.request().method() === "POST" && response.url().startsWith("http://127.0.0.1:3100"));
  await analyzeButton(page).click();
  const response = await serverResponse;

  expect(response.status()).toBe(200);
  await expectReport(page);
  expect(await providerCalls(request)).toBe(1);
});

test("formulario rápido: el ejemplo se analiza con un único informe", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Formulario rápido" }).click();
  await page.getByRole("button", { name: "Cargar ejemplo" }).click();
  await expect(analyzeButton(page)).toBeEnabled();

  await analyzeButton(page).click();

  await expectReport(page);
  expect(await providerCalls(request)).toBe(1);
});

test("estado loading mientras FastAPI consulta al proveedor", async ({ page, request }) => {
  await setProviderMode(request, "slow");
  await page.goto("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).first().click();

  await analyzeButton(page).click();

  await expect(page.getByText("Analizando rendimiento y oportunidades…")).toBeVisible();
  await expectReport(page);
});

test("error del proveedor: estado de error sin detalles internos y sin credenciales en el navegador", async ({ page, request }) => {
  await setProviderMode(request, "error");
  await page.goto("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).first().click();

  const serverResponse = page.waitForResponse((response) => response.request().method() === "POST" && response.url().startsWith("http://127.0.0.1:3100"));
  await analyzeButton(page).click();
  const body = await (await serverResponse).text();

  await expect(page.getByRole("button", { name: "Reintentar" })).toBeVisible();
  await expect(page.getByText("No pudimos completar el análisis", { exact: false })).toBeVisible();
  // The primary model and the fallback are each tried once.
  expect(await providerCalls(request)).toBe(2);

  expect(body).not.toContain("upstream secret detail");
  expect(body).not.toContain("AI_500");
  expectNoSecrets(body);
  expectNoSecrets(await page.content());
});

test("reintento tras error: una sola solicitud y luego informe", async ({ page, request }) => {
  await setProviderMode(request, "error");
  await page.goto("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).first().click();
  await analyzeButton(page).click();
  await expect(page.getByRole("button", { name: "Reintentar" })).toBeVisible();

  const callsBeforeRetry = await providerCalls(request);
  await setProviderMode(request, "ok");
  await page.getByRole("button", { name: "Reintentar" }).click();

  await expectReport(page);
  expect((await providerCalls(request)) - callsBeforeRetry).toBe(1);
});

test("doble clic en Analizar no genera solicitudes duplicadas", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).first().click();

  await analyzeButton(page).dblclick();

  await expectReport(page);
  expect(await providerCalls(request)).toBe(1);
});

test("el informe exitoso no expone credenciales, tokens ni direcciones internas", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Métricas de campaña").fill(FREE_TEXT);

  const serverResponse = page.waitForResponse((response) => response.request().method() === "POST" && response.url().startsWith("http://127.0.0.1:3100"));
  await analyzeButton(page).click();
  const body = await (await serverResponse).text();

  await expectReport(page);
  expectNoSecrets(body);
  expectNoSecrets(await page.content());
  await expectNoSessionInScriptStorage(page);
  await expectSessionCookiesAreHttpOnly(page);
});
