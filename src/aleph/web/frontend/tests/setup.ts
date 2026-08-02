import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { resetLessons } from "../src/mocks/lessons";
import { resetPaths } from "../src/mocks/paths";
import { resetProgress } from "../src/mocks/progress";
import { server } from "../src/mocks/server";
import { resetShaping } from "../src/mocks/shaping";
import { resetTutor } from "../src/mocks/tutor";

window.scrollTo = vi.fn();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  resetPaths();
  resetLessons();
  resetTutor();
  resetShaping();
  resetProgress();
  vi.restoreAllMocks();
  cleanup();
  window.history.pushState({}, "", "/");
});
afterAll(() => server.close());
