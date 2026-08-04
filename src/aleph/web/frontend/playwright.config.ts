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

// The user journeys (tests/e2e/journeys/): Phase 1's @w1..@w8 and the tutor's
// @w9 + @w11..@w16. They need the harness's own stub backend — the sentinel
// topics and prompts that force the refusal (W7/W15), failure (W8/W14) and
// correction (W16) branches only mean something to the stub model — and §12 puts
// them on the phone viewport, so they run in the mobile project alone and not at
// all against a BASE_URL deployment (where only the @smoke specs make sense).
const journeys = "journeys/**";

// The flashcards journeys (W24-W28, Phase 3 TDD D15, §11; W28 added by
// AL-410) get their **own** Playwright project below rather than running
// inside "mobile-390x844" with everything else: `FlashcardRepository.
// due_candidates` admits a card via `due_on <= today` **or** `a review exists
// today`, and `select_daily_queue` is blind to `satisfied` (TDD §5.1/§5.3) —
// so every card any earlier spec in the *same run* graded stays in the
// candidate pool, competing for the day's ten slots, for the rest of the
// calendar day. With `workers: 1, fullyParallel: false` running every spec
// alphabetically on one shared `DEV_USER`, W1-W23's ~30 completions (each
// drafting cards once `flashcard_drafts_per_day` stops capping them,
// `scripts/e2e_backend.py`) would sit in that pool by the time W24 runs, and
// W24-W28 would go on to pollute each other the same way. This project runs
// them as different, otherwise-idle accounts instead (`fixtures/auth.ts`'s
// `ADMIN_USER` / `UNVERIFIED_USER`), which is what the specs' own headers now
// describe — W28 shares `UNVERIFIED_USER` with W27 rather than the busier
// `ADMIN_USER` (see `w28.spec.ts`'s own header for why).
const flashcardsJourneys = "journeys/w2[4-8].spec.ts";
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
      // and the only one that runs the journeys. The flashcards journeys
      // (W24-W28) run in their own project below instead, on their own
      // accounts — excluded here so each spec runs exactly once.
      name: "mobile-390x844",
      testIgnore: bootsOwnServers ? flashcardsJourneys : journeys,
      dependencies: bootsOwnServers ? ["setup"] : [],
      use: {
        ...devices["Pixel 5"],
        viewport: { width: 390, height: 844 },
        ...chromiumLaunch,
      },
    },
    // W24-W28 (Phase 3 TDD D15, §11 — see the `flashcardsJourneys` note
    // above): the same phone viewport and the same stub backend, but split
    // into their own project so a residue-inducing shared account never
    // needs to include everything else the suite does. Each spec file still
    // selects its own account via `test.use({ storageState })` (W24-W26 on
    // `ADMIN_STORAGE_STATE`, W27 and W28 together on `UNVERIFIED_STORAGE_STATE`)
    // — same pattern W1's admin sub-test already uses inside "mobile-390x844",
    // just scoped to a dedicated project rather than a nested `describe`. Only
    // meaningful against the harness's own stub backend, so it does not
    // exist at all in the BASE_URL/prod-smoke case (mirroring "setup" above).
    ...(bootsOwnServers
      ? [
          {
            name: "mobile-390x844-flashcards",
            testMatch: flashcardsJourneys,
            dependencies: ["setup"],
            use: {
              ...devices["Pixel 5"],
              viewport: { width: 390, height: 844 },
              ...chromiumLaunch,
            },
          },
        ]
      : []),
  ],
});
