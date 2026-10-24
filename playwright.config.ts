import { defineConfig, devices } from "@playwright/test";

// Test-only ports. They avoid 3000 and 8000, which are common local development ports.
const FRONTEND_PORT = 3100;
const BACKEND_PORT = 18000;
const PROVIDER_PORT = 18080;
const TEST_API_KEY = "e2e-test-key-not-real";

export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.spec.ts",
  // The simulated provider keeps shared state, so the suite runs one test at a time.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  // Run `bun run test:e2e`, which builds .output/ first. The three services are started
  // in dependency order: simulated provider, then FastAPI, then the frontend server.
  webServer: [
    {
      command: "python -u e2e/fake-openrouter.py",
      url: `http://127.0.0.1:${PROVIDER_PORT}/__health`,
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
      },
    },
    {
      command: "node .output/server/index.mjs",
      url: `http://127.0.0.1:${FRONTEND_PORT}/`,
      reuseExistingServer: false,
      stdout: "ignore",
      env: {
        HOST: "127.0.0.1",
        PORT: String(FRONTEND_PORT),
        FASTAPI_INTERNAL_URL: `http://127.0.0.1:${BACKEND_PORT}`,
      },
    },
  ],
});
