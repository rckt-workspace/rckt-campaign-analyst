import { expect, test } from "@playwright/test";
import { analyzeButton, providerCalls, resetServices } from "./helpers";

// Numbers that could mean two different amounts are never converted. They block analysis and saving, and the
// notice names the field and keeps the text the user wrote. Runs as the analyst (chromium project).

const SAVE = "Guardar campaña";
const AMBIGUOUS_NOTICE = "Separador ambiguo";

function saveButton(page: import("@playwright/test").Page) {
  return page.getByRole("button", { name: SAVE });
}

test.beforeEach(async ({ request }) => {
  await resetServices(request);
});

test("1234,567 en texto pegado bloquea análisis y guardado, sin llamar al proveedor", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page
    .getByLabel("Métricas de campaña")
    .fill("Campaña: Prueba numérica\nInversión: 1234,567\nClics: 8.610\nVentas: 94");

  await expect(page.getByText("Inversión: «1234,567».", { exact: false })).toBeVisible();
  await expect(page.getByText(AMBIGUOUS_NOTICE, { exact: false })).toBeVisible();
  await expect(analyzeButton(page)).toBeDisabled();
  await expect(saveButton(page)).toBeDisabled();
  expect(await providerCalls(request)).toBe(0);
});

test("corregir el importe vuelve a habilitar el análisis y el aviso desaparece", async ({
  page,
}) => {
  await page.goto("/");
  const input = page.getByLabel("Métricas de campaña");
  await input.fill("Campaña: Prueba numérica\nInversión: 1234,567\nClics: 8.610\nVentas: 94");
  await expect(analyzeButton(page)).toBeDisabled();

  await input.fill("Campaña: Prueba numérica\nInversión: 1.234.567\nClics: 8.610\nVentas: 94");

  await expect(page.getByText(AMBIGUOUS_NOTICE, { exact: false })).toHaveCount(0);
  await expect(analyzeButton(page)).toBeEnabled();
  await expect(saveButton(page)).toBeEnabled();
});

test("formulario: un importe ambiguo se señala en su campo y bloquea la acción", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Formulario rápido" }).click();
  await page.getByLabel("Inversión", { exact: true }).fill("1234,567");
  await page.getByLabel("Clics", { exact: true }).fill("8.610");

  await expect(page.getByText("Inversión: «1234,567».", { exact: false })).toBeVisible();
  await expect(analyzeButton(page)).toBeDisabled();
});

test("periodo anterior: un valor ambiguo se señala con su etiqueta", async ({ page }) => {
  await page.goto("/");
  await page
    .getByLabel("Métricas de campaña")
    .fill("Campaña: Prueba numérica\nClics: 8.610\nVentas: 94\nPeriodo anterior:\nCTR: 1,234");

  await expect(page.getByText("CTR (periodo anterior): «1,234».", { exact: false })).toBeVisible();
  await expect(analyzeButton(page)).toBeDisabled();
});

test("el ejemplo de RCKT Growth no muestra incidencias", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).click();

  await expect(page.getByText(AMBIGUOUS_NOTICE, { exact: false })).toHaveCount(0);
  await expect(analyzeButton(page)).toBeEnabled();
});
