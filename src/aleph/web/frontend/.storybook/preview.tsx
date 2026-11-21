import type { Preview } from "@storybook/react-vite";
import { setupWorker } from "msw/browser";
import { mswLoader } from "msw-storybook-addon/csf3";
import "../src/index.css";
import { resetFlowCelebrations } from "../src/lib/flow-celebration";
import { handlers } from "../src/mocks/handlers";
import { resetBeats } from "../src/mocks/beats";
import { resetLessons } from "../src/mocks/lessons";
import { resetPaths } from "../src/mocks/paths";
import { resetProgress } from "../src/mocks/progress";
import { resetShaping } from "../src/mocks/shaping";
import { resetTutor } from "../src/mocks/tutor";
import { sessionHandler, worldSession } from "../src/stories/world";

const preview: Preview = {
  loaders: [
    // The app's fakes are the worker's *initial* handlers, so they survive the
    // addon's reset between stories; a story overrides one with `msw.use(...)`
    // in its `beforeEach`. The world's session goes first so it wins over the
    // plain learner in `handlers`. Unhandled requests are fonts, icons and Vite
    // modules, never API calls.
    mswLoader(async () => {
      const worker = setupWorker(sessionHandler(worldSession), ...handlers);
      await worker.start({ onUnhandledRequest: "bypass", quiet: true });
      return worker;
    }),
  ],
  // Every story starts from empty fakes, exactly like a unit test
  // (tests/setup.ts); a story's own `beforeEach` then seeds the data it shows.
  beforeEach: () => {
    resetPaths();
    resetLessons();
    resetTutor();
    resetShaping();
    resetProgress();
    resetBeats();
    resetFlowCelebrations();
    window.sessionStorage.clear();
  },
  parameters: {
    layout: "fullscreen",
    backgrounds: { disable: true },
    controls: { expanded: true },
    viewport: {
      options: {
        iphone: {
          name: "iPhone 15 (393×852)",
          styles: { width: "393px", height: "852px" },
          type: "mobile",
        },
        iphoneSe: {
          name: "iPhone SE (375×667)",
          styles: { width: "375px", height: "667px" },
          type: "mobile",
        },
        ipad: {
          name: "iPad (820×1180)",
          styles: { width: "820px", height: "1180px" },
          type: "tablet",
        },
        laptop: {
          name: "Laptop (1280×800)",
          styles: { width: "1280px", height: "800px" },
          type: "desktop",
        },
        desktop: {
          name: "Desktop (1440×900)",
          styles: { width: "1440px", height: "900px" },
          type: "desktop",
        },
      },
    },
    options: {
      storySort: {
        order: ["Foundations", "Screens", "Components"],
      },
    },
  },
  // Phone first: Aleph's primary surface is the Home Screen app.
  initialGlobals: { viewport: { value: "iphone", isRotated: false } },
};

export default preview;
