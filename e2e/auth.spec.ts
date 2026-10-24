import { expect, test } from "@playwright/test";
import {
  analyzeButton,
  expectNoSecrets,
  expectNoSessionInScriptStorage,
  providerCalls,
  resetServices,
  setRefreshMode,
  signIn,
  userEmail,
} from "./helpers";

// These scenarios start without any session, like a first-time visitor.
test.use({ storageState: { cookies: [], origins: [] } });

test.beforeEach(async ({ request }) => {
  await resetServices(request);
});

test("visitante anónimo: la aplicación redirige a iniciar sesión", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login(\?.*)?$/);
  await expect(page.getByRole("button", { name: "Iniciar sesión" })).toBeVisible();
});

test("contraseña incorrecta: mensaje claro, sin sesión y sin revelar más detalles", async ({ page, context }) => {
  await signIn(page, userEmail("analyst"), "wrong-password-not-real");

  await expect(page.getByRole("alert")).toHaveText("Correo o contraseña incorrectos.");
  await expect(page).toHaveURL(/\/login(\?.*)?$/);
  const cookies = await context.cookies();
  expect(cookies.filter((cookie) => cookie.name.startsWith("sb-"))).toEqual([]);
  expectNoSecrets(await page.content());
});

test("inicio y cierre de sesión: tras cerrar sesión la aplicación vuelve a pedir credenciales", async ({ page, context }) => {
  await signIn(page, userEmail("analyst"));
  await expect(page).toHaveURL("/");
  await expect(page.getByText(userEmail("analyst"))).toBeVisible();

  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page).toHaveURL(/\/login(\?.*)?$/);

  await page.goto("/");
  await expect(page).toHaveURL(/\/login(\?.*)?$/);
  const cookies = await context.cookies();
  expect(cookies.filter((cookie) => cookie.name.startsWith("sb-") && cookie.value)).toEqual([]);
});

test("usuario con sesión que visita /login es enviado a la aplicación", async ({ page }) => {
  await signIn(page, userEmail("analyst"));
  await expect(page).toHaveURL("/");
  await page.goto("/login");
  await expect(page).toHaveURL("/");
});

test("client_viewer puede entrar pero no genera análisis con costo", async ({ page, request }) => {
  await signIn(page, userEmail("viewer"));
  await expect(page).toHaveURL("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).first().click();

  await analyzeButton(page).click();

  await expect(page.getByRole("alert")).toContainText("Tu rol permite consultar los informes");
  expect(await providerCalls(request)).toBe(0);
  await expectNoSessionInScriptStorage(page);
});

test("usuario sin membresía: se le informa que no tiene organización y no se consume IA", async ({ page, request }) => {
  await signIn(page, userEmail("outsider"));
  await expect(page).toHaveURL("/");
  await page.getByRole("button", { name: "Cargar ejemplo" }).first().click();

  await analyzeButton(page).click();

  await expect(page.getByRole("alert")).toContainText("no tiene acceso a ninguna organización");
  expect(await providerCalls(request)).toBe(0);
});

test("sesión expirada y revocada: no se accede a la aplicación y se pide iniciar sesión de nuevo", async ({ page, request }) => {
  // The expired identity receives an access token that is already expired at sign-in.
  // With refresh revoked, the server cannot renew it, so the application is not opened.
  await setRefreshMode(request, "revoked");
  await signIn(page, userEmail("expired"));

  await expect(page).toHaveURL(/\/login(\?.*)?$/);
  expect(await providerCalls(request)).toBe(0);
});

test("aviso de sesión expirada en la pantalla de acceso", async ({ page }) => {
  await page.goto("/login?expired=1");
  await expect(page.getByRole("status")).toContainText("Tu sesión expiró");
  await expect(page.getByLabel("Contraseña")).toBeEmpty();
});
