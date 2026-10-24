import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const PROVIDER = "http://127.0.0.1:18080";
const FRONTEND = "http://127.0.0.1:3100";
const TEST_API_KEY = "e2e-test-key-not-real";
// Internal addresses and credentials must never reach the browser.
const FORBIDDEN_IN_BROWSER = [TEST_API_KEY, "127.0.0.1:18000", "127.0.0.1:18080", "backend:8000", "host.docker.internal"];

const ANALYZE = "Analizar campaña";
const REPORT_SECTIONS = ["Estado general", "Resumen ejecutivo", "Principales hallazgos", "Qué haría ahora"];
const FREE_TEXT = [
  "Campaña: Lanzamiento RCKT Growth",
  "Objetivo: Ventas",
  "Inversión: $4.850.000 COP",
  "Impresiones: 410.000",
  "Clics: 8.610",
  "Ventas: 94",
  "Ingresos atribuidos: $11.960.000 COP",
].join("\n");

type ProviderState = { calls: { model: string; hasAuthorization: boolean }[]; mode: string };

async function setProviderMode(request: APIRequestContext, mode: "ok" | "error" | "slow") {
  const response = await request.post(`${PROVIDER}/__control`, { data: { mode } });
  expect(response.ok()).toBeTruthy();
}

async function providerState(request: APIRequestContext): Promise<ProviderState> {
  return (await request.get(`${PROVIDER}/__calls`)).json();
}

function analyzeButton(page: Page) {
  return page.getByRole("button", { name: ANALYZE });
}

async function expectReport(page: Page) {
  for (const section of REPORT_SECTIONS) {
    await expect(page.getByText(section, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByText("Dato clave", { exact: false }).first()).toBeVisible();
}

// Captures the body of the browser's call to the TanStack server function, which is the only
// request that carries campaign data to the server.
function waitForServerFunctionResponse(page: Page) {
  return page.waitForResponse(
    (response) => response.request().method() === "POST" && response.url().startsWith(FRONTEND),
  );
}

function expectNoSecrets(text: string) {
  for (const marker of FORBIDDEN_IN_BROWSER) {
    expect(text, `browser-visible content must not contain ${marker}`).not.toContain(marker);
  }
}

test.beforeEach(async ({ request }) => {
  const response = await request.post(`${PROVIDER}/__reset`);
  expect(response.ok()).toBeTruthy();
});

test("carga inicial de la interfaz React", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle(/RCKT Campaign Analyst/);
  await expect(page.getByRole("tab", { name: "Pegar métricas" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Formulario rápido" })).toBeVisible();
  // No data yet, so the analysis cannot start.
  await expect(analyzeButton(page)).toBeDisabled();
});

test("texto libre: el informe llega desde FastAPI con las secciones ejecutivas", async ({ page, request }) => {
  await page.goto("/");
  await page.getByLabel("Métricas de campaña").fill(FREE_TEXT);

  const serverResponse = waitForServerFunctionResponse(page);
  await analyzeButton(page).click();
  const response = await serverResponse;

  expect(response.status()).toBe(200);
  await expectReport(page);
  const state = await providerState(request);
  expect(state.calls).toHaveLength(1);
  expect(state.calls[0]?.hasAuthorization).toBe(true);
});

test("formulario rápido: el ejemplo se analiza con un único informe", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Formulario rápido" }).click();
  await page.getByRole("button", { name: "Cargar ejemplo" }).click();
  await expect(analyzeButton(page)).toBeEnabled();

  await analyzeButton(page).click();

  await expectReport(page);
  expect((await providerState(request)).calls).toHaveLength(1);
});

test("estado loading mientras FastAPI consulta al proveedor", async ({ page, request }) => {
  await setProviderMode(request, "slow");
  await page.goto("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).first().click();

  await analyzeButton(page).click();

  await expect(page.getByText("Analizando rendimiento y oportunidades…")).toBeVisible();
  await expectReport(page);
});

test("error del proveedor: estado de error sin detalles internos", async ({ page, request }) => {
  await setProviderMode(request, "error");
  await page.goto("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).first().click();

  const serverResponse = waitForServerFunctionResponse(page);
  await analyzeButton(page).click();
  const response = await serverResponse;

  await expect(page.getByRole("button", { name: "Reintentar" })).toBeVisible();
  await expect(page.getByText("No pudimos completar el análisis", { exact: false })).toBeVisible();
  // The primary model and the fallback are each tried once, and only once.
  const state = await providerState(request);
  expect(state.calls.map((call) => call.model)).toHaveLength(2);
  expect(new Set(state.calls.map((call) => call.model)).size).toBe(2);

  const body = await response.text();
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

  const callsBeforeRetry = (await providerState(request)).calls.length;
  await setProviderMode(request, "ok");
  await page.getByRole("button", { name: "Reintentar" }).click();

  await expectReport(page);
  expect((await providerState(request)).calls.length - callsBeforeRetry).toBe(1);
});

test("doble clic en Analizar no genera solicitudes duplicadas", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).first().click();

  await analyzeButton(page).dblclick();

  await expectReport(page);
  expect((await providerState(request)).calls).toHaveLength(1);
});

test("el informe exitoso no expone credenciales ni direcciones internas", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Métricas de campaña").fill(FREE_TEXT);

  const serverResponse = waitForServerFunctionResponse(page);
  await analyzeButton(page).click();
  const body = await (await serverResponse).text();

  await expectReport(page);
  expectNoSecrets(body);
  expectNoSecrets(await page.content());
});
