import { readFileSync, existsSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

// Test-only ports. They avoid 3000 and 8000, which are common local development ports.
const FRONTEND_PORT = 3100;
const BACKEND_PORT = 18000;
const PROVIDER_PORT = 18080; // simulated OpenRouter
const AUTH_PORT = 18090; // simulated Supabase Auth
const TEST_API_KEY = "e2e-test-key-not-real";
const TEST_PUBLISHABLE_KEY = "e2e-publishable-key-not-real";
const AUTH_URL = `http://127.0.0.1:${AUTH_PORT}`;

// Environment first; otherwise the repository .env (bun run does not pass .env values to child processes).
function configured(name: string): string {
  const fromEnvironment = process.env[name]?.trim();
  if (fromEnvironment) return fromEnvironment;
  if (!existsSync(".env")) return "";
  const line = readFileSync(".env", "utf-8")
    .split("\n")
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith(`${name}=`));
  return line ? line.slice(name.length + 1).trim() : "";
}

// The backend runs with the application login (rckt_app), never with the administrative or migration login.
const databaseUrl = configured("E2E_APP_DATABASE_URL");
if (!databaseUrl) {
  throw new Error("E2E_APP_DATABASE_URL is required: a postgresql+asyncpg:// rckt_app URL of a database whose name ends in _test");
}

export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.spec.ts",
  // The simulated services keep shared state, so the suite runs one test at a time.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    // Signs in once as the analyst and stores the session cookies for the other tests.
    { name: "setup", testMatch: "**/auth.setup.ts" },
    {
      name: "chromium",
      testIgnore: "**/auth.setup.ts",
      use: { ...devices["Desktop Chrome"], storageState: "e2e/.auth/analyst.json" },
      dependencies: ["setup"],
    },
  ],
  // Run `bun run test:e2e`: it builds .output/ and prepares the database before these services start.
  webServer: [
    {
      command: "python -u e2e/fake-openrouter.py",
      url: `http://127.0.0.1:${PROVIDER_PORT}/__health`,
      reuseExistingServer: false,
      stdout: "ignore",
    },
    {
      command: "python -u e2e/fake-supabase.py",
      url: `${AUTH_URL}/__health`,
      reuseExistingServer: false,
      stdout: "ignore",
    },
    {
      command: `python -m uvicorn app.main:app --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: "backend",
      url: `http://127.0.0.1:${BACKEND_PORT}/healthz`,
      reuseExistingServer: false,
      stdout: "ignore",
      env: {
        OPENROUTER_API_KEY: TEST_API_KEY,
        OPENROUTER_BASE_URL: `http://127.0.0.1:${PROVIDER_PORT}/api/v1`,
        SUPABASE_URL: AUTH_URL,
        DATABASE_URL: databaseUrl,
      },
    },
    {
      // The frontend log goes to a git-ignored file, so server-function failures can be read after a run.
      command: "node .output/server/index.mjs > e2e/.frontend.log 2>&1",
      url: `http://127.0.0.1:${FRONTEND_PORT}/login`,
      reuseExistingServer: false,
      stdout: "ignore",
      env: {
        HOST: "127.0.0.1",
        PORT: String(FRONTEND_PORT),
        FASTAPI_INTERNAL_URL: `http://127.0.0.1:${BACKEND_PORT}`,
        SUPABASE_URL: AUTH_URL,
        SUPABASE_PUBLISHABLE_KEY: TEST_PUBLISHABLE_KEY,
      },
    },
  ],
});
