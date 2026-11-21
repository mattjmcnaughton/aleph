import type { StorybookConfig } from "@storybook/react-vite";

// Aleph's design sandbox (docs/storybook.md). Stories render the real routes and
// components against the same in-memory MSW fakes the unit tests use
// (`src/mocks/`), seeded with a realistic learner's data (`src/stories/world.ts`),
// so a design change can be judged on every screen and state without a backend.
const config: StorybookConfig = {
  stories: ["../src/**/*.stories.@(ts|tsx)"],
  addons: ["msw-storybook-addon"],
  framework: { name: "@storybook/react-vite", options: {} },
  // `./public` holds only MSW's `mockServiceWorker.js`; it lives here, not in the
  // app's `public/`, so the production build never ships it.
  staticDirs: ["./public", "../public"],
  core: { disableTelemetry: true },
};

export default config;
