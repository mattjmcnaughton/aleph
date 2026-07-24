import { defineConfig, devices } from "@playwright/test";
import { BACKEND_PORT, BACKEND_URL, FRONTEND_PORT, FRONTEND_URL } from "./tests/e2e/servers";

// Ports the harness boots the ephemeral backend + frontend on (tests/e2e/servers.ts
// — the specs drive the backend origin directly for the OIDC flow). Fixed: the
// vite dev proxy targets http://localhost:8000, so the backend cannot move
// without moving the proxy target with it.
const backendPort = BACKEND_PORT;
const frontendPort = FRONTEND_PORT;
const frontendURL = FRONTEND_URL;
const backendURL = BACKEND_URL;
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

// The W1-W8 user journeys (tests/e2e/journeys/, tagged @w1..@w8). They need the
// harness's own stub backend — the sentinel topics that force the refusal (W7)
// and failure (W8) branches only mean something to the stub model — and §12 puts
// them on the phone viewport, so they run in the mobile project alone and not at
// all against a BASE_URL deployment (where only the @smoke specs make sense).
const journeys = "journeys/**";
const bootsOwnServers = !process.env.BASE_URL;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  // Generous budgets: a journey walks several generate/poll cycles and the app
  // polls on the §14 2s->5s backoff, so wall time is dominated by waits the stub
  // model itself does not cause — and every one of those cycles stretches on a
  // CI runner or a developer machine that is busy with something else. The
  // journeys are deterministic, so a wait that is too tight buys nothing but
  // flakes; only a real hang should ever reach these.
  timeout: 180_000,
  expect: { timeout: 15_000 },
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
    // Signs in through the real OIDC code flow once and saves the session as
    // storage state the journeys replay (tests/e2e/auth.setup.ts). Not a project
    // the @smoke specs may depend on — those assert the signed-out gate.
    ...(bootsOwnServers
      ? [
          {
            name: "setup",
            testMatch: /auth\.setup\.ts/,
            use: { ...devices["Desktop Chrome"], ...chromiumLaunch },
          },
        ]
      : []),
    {
      name: "desktop",
      testIgnore: journeys,
      use: {
        ...devices["Desktop Chrome"],
        ...chromiumLaunch,
      },
    },
    {
      // The phone viewport §12 mandates (390x844) — the primary target surface,
      // and the only one that runs the W1-W8 journeys.
      name: "mobile-390x844",
      testIgnore: bootsOwnServers ? undefined : journeys,
      dependencies: bootsOwnServers ? ["setup"] : [],
      use: {
        ...devices["Pixel 5"],
        viewport: { width: 390, height: 844 },
        ...chromiumLaunch,
      },
    },
  ],
});
