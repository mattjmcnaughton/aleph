import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { resetBeats } from "../src/mocks/beats";
import { resetFlowCelebrations } from "../src/lib/flow-celebration";
import { resetFlashcards } from "../src/mocks/flashcards";
import { resetLessons } from "../src/mocks/lessons";
import { resetPaths } from "../src/mocks/paths";
import { resetProgress } from "../src/mocks/progress";
import { server } from "../src/mocks/server";
import { resetSettings } from "../src/mocks/settings";
import { resetShaping } from "../src/mocks/shaping";
import { resetTutor } from "../src/mocks/tutor";

window.scrollTo = vi.fn();

// jsdom implements no media queries at all, so any component that asks about
// `prefers-reduced-motion` (`components/path-complete.tsx`) would throw rather
// than read a preference. Stubbed as "no preference" — the browser default, and
// the branch the celebration's motion actually renders under. A test that wants
// the reduced-motion branch overrides `window.matchMedia` for its own case.
window.matchMedia = ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: vi.fn(),
  removeListener: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  dispatchEvent: vi.fn(),
})) as unknown as typeof window.matchMedia;

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  resetPaths();
  resetLessons();
  resetTutor();
  resetShaping();
  resetProgress();
  resetFlashcards();
  resetBeats();
  resetSettings();
  // A Flow record (lib/flow.ts) lives in sessionStorage, not the MSW store —
  // nothing else here resets it, so a flow written by one test would
  // otherwise still be sitting there for the next.
  window.sessionStorage.clear();
  // ...and the receipt remembers which flow it last celebrated in module
  // memory (lib/flow-celebration.ts), which every test seeds with the same
  // `startedAt`.
  resetFlowCelebrations();
  vi.restoreAllMocks();
  cleanup();
  window.history.pushState({}, "", "/");
});
afterAll(() => server.close());
