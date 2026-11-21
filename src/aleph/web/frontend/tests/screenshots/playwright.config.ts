import { defineConfig, devices } from "@playwright/test";

// README screenshots (`just screenshots`): the real UI on a dev server, every API
// call answered in-process by the MSW fakes seeded in `demo-data.ts`. No backend,
// database, account or model is involved.
const port = 5199;
const executablePath = process.env.PW_CHROMIUM_PATH || undefined;

export default defineConfig({
  testDir: ".",
  workers: 1,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    ...devices["Desktop Chrome"],
    deviceScaleFactor: 2,
    colorScheme: "dark",
    serviceWorkers: "block",
    ...(executablePath ? { launchOptions: { executablePath } } : { channel: "chromium" }),
  },
  webServer: {
    command: `pnpm exec vite --host 127.0.0.1 --port ${port} --strictPort`,
    cwd: "../..",
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
