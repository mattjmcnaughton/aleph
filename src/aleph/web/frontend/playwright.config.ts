import { defineConfig, devices } from "@playwright/test";

// Ports the harness boots the ephemeral backend + frontend on. Fixed: the vite
// dev proxy targets http://localhost:8000, so the backend cannot move without
// moving the proxy target with it.
const backendPort = 8000;
const frontendPort = 5300;
const frontendURL = `http://127.0.0.1:${frontendPort}`;
const backendURL = `http://127.0.0.1:${backendPort}`;
// BASE_URL points the suite at an already-running deployment (prod smoke);
// unset, the harness boots its own stub backend + dev frontend below.
const baseURL = process.env.BASE_URL ?? frontendURL;

// Local runs use the machine's preinstalled Playwright chromium via
// PW_CHROMIUM_PATH (set by `just test-e2e`) to dodge a browser/library version
// mismatch; CI installs its own matching browser, so the env is unset there and
// Playwright uses its managed download.
const executablePath = process.env.PW_CHROMIUM_PATH || undefined;
const chromiumLaunch = executablePath ? { launchOptions: { executablePath } } : {};

// The stub backend (scripts/e2e_backend.py): the real API + orchestrator with
// the deterministic stub model wired into every slot, so the browser suite runs
// offline. `alembic upgrade head` migrates the target database first. ENV=test
// keeps the production stub-guard satisfied. The DATABASE_URL default points at
// a local `aleph_e2e` database on the conventional postgres/postgres dev role.
const databaseUrl =
  process.env.E2E_DATABASE_URL ?? "postgresql+asyncpg://postgres:postgres@localhost:5432/aleph_e2e";

const backendCommand = [
  "cd ../../../..",
  `DATABASE_URL='${databaseUrl}' uv run alembic upgrade head`,
  `ENV=test DATABASE_URL='${databaseUrl}' uv run uvicorn scripts.e2e_backend:create_stub_app` +
    ` --factory --host 127.0.0.1 --port ${backendPort}`,
].join(" && ");

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  reporter: [
    ["list"],
    ["json", { outputFile: "../../../../.artifacts/test-results/playwright.json" }],
  ],
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  webServer: process.env.BASE_URL
    ? undefined
    : [
        {
          command: `bash -lc "${backendCommand}"`,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
          url: `${backendURL}/readyz`,
        },
        {
          command: `bash -lc "VITE_API_URL='' pnpm exec vite --host 127.0.0.1 --port ${frontendPort}"`,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
          url: frontendURL,
        },
      ],
  projects: [
    {
      name: "desktop",
      use: {
        ...devices["Desktop Chrome"],
        ...chromiumLaunch,
      },
    },
    {
      // The phone viewport §12 mandates (390x844) — the primary target surface.
      name: "mobile-390x844",
      use: {
        ...devices["Pixel 5"],
        viewport: { width: 390, height: 844 },
        ...chromiumLaunch,
      },
    },
  ],
});
