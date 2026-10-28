import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, type APIRequestContext, type Page } from "@playwright/test";

const identities: { password: string; users: { key: string; email: string }[] } = JSON.parse(
  readFileSync(path.resolve("e2e/identities.json"), "utf-8"),
);

export const PROVIDER = "http://127.0.0.1:18080"; // simulated OpenRouter
export const AUTH = "http://127.0.0.1:18090"; // simulated Supabase Auth
export const TEST_API_KEY = "e2e-test-key-not-real";
export const TEST_PUBLISHABLE_KEY = "e2e-publishable-key-not-real";
export const PASSWORD: string = identities.password;

export function userEmail(key: string): string {
  const user = identities.users.find((candidate) => candidate.key === key);
  if (!user) throw new Error(`unknown test identity ${key}`);
  return user.email;
}

// Internal addresses, keys and secrets must never reach the browser.
export const FORBIDDEN_IN_BROWSER = [
  TEST_API_KEY,
  TEST_PUBLISHABLE_KEY,
  PASSWORD,
  "127.0.0.1:18000",
  "127.0.0.1:18080",
  "127.0.0.1:18090",
  "backend:8000",
  "host.docker.internal",
  "SUPABASE_SERVICE",
];

// A raw access or refresh token looks like a JWT (three base64url parts) or a refresh string in a body.
const JWT_PATTERN = /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/;

export const ANALYZE = "Analizar campaña";
export const REPORT_SECTIONS = [
  "Estado general",
  "Resumen ejecutivo",
  "Principales hallazgos",
  "Qué haría ahora",
];

export async function setProviderMode(request: APIRequestContext, mode: "ok" | "error" | "slow") {
  const response = await request.post(`${PROVIDER}/__control`, { data: { mode } });
  expect(response.ok()).toBeTruthy();
}

export async function providerCalls(request: APIRequestContext): Promise<number> {
  const state = await (await request.get(`${PROVIDER}/__calls`)).json();
  return state.calls.length;
}

/** Which usage the simulated provider reports: none (default), partial (every other call) or complete. */
export async function setProviderUsage(
  request: APIRequestContext,
  usage: "none" | "partial" | "complete",
) {
  const response = await request.post(`${PROVIDER}/__control`, { data: { usage } });
  expect(response.ok()).toBeTruthy();
}

export async function resetServices(request: APIRequestContext) {
  expect((await request.post(`${PROVIDER}/__reset`)).ok()).toBeTruthy();
  expect((await request.post(`${AUTH}/__reset`)).ok()).toBeTruthy();
}

export async function setRefreshMode(request: APIRequestContext, refresh: "ok" | "revoked") {
  expect((await request.post(`${AUTH}/__control`, { data: { refresh } })).ok()).toBeTruthy();
}

export async function signIn(page: Page, email: string, password = PASSWORD) {
  await page.goto("/login");
  await page.getByLabel("Correo electrónico").fill(email);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: "Iniciar sesión" }).click();
}

export function analyzeButton(page: Page) {
  return page.getByRole("button", { name: ANALYZE });
}

export async function expectReport(page: Page) {
  for (const section of REPORT_SECTIONS) {
    await expect(page.getByText(section, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByText("Dato clave", { exact: false }).first()).toBeVisible();
}

export function expectNoSecrets(text: string) {
  for (const marker of FORBIDDEN_IN_BROWSER) {
    expect(text, `browser-visible content must not contain ${marker}`).not.toContain(marker);
  }
  expect(
    JWT_PATTERN.test(text),
    "no access token may appear in browser-visible content",
  ).toBeFalsy();
}

/** Storage visible to page scripts must not contain Supabase session material. Cookies are checked separately. */
export async function expectNoSessionInScriptStorage(page: Page) {
  const keys = await page.evaluate(() => [
    ...Object.keys(window.localStorage),
    ...Object.keys(window.sessionStorage),
  ]);
  expect(keys.filter((key) => key.startsWith("sb-"))).toEqual([]);
}

/** The session cookies set by the server must be HttpOnly, so page scripts cannot read them. */
export async function expectSessionCookiesAreHttpOnly(page: Page) {
  const cookies = await page.context().cookies();
  const session = cookies.filter((cookie) => cookie.name.startsWith("sb-"));
  expect(session.length, "a signed-in session must set cookies").toBeGreaterThan(0);
  for (const cookie of session) {
    expect(cookie.httpOnly, `${cookie.name} must be HttpOnly`).toBeTruthy();
    expect(cookie.secure, `${cookie.name} must be Secure`).toBeTruthy();
    expect(cookie.sameSite, `${cookie.name} must be SameSite=Lax`).toBe("Lax");
  }
}

export const SERVER_FUNCTION_POST = (url: string) =>
  url.includes("/_serverFn") || url.includes("/_server");
