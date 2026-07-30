import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { resetLessons } from "../src/mocks/lessons";
import { resetPaths } from "../src/mocks/paths";
import { server } from "../src/mocks/server";
import { resetTutor } from "../src/mocks/tutor";

window.scrollTo = vi.fn();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  resetPaths();
  resetLessons();
  resetTutor();
  vi.restoreAllMocks();
  cleanup();
  window.history.pushState({}, "", "/");
});
afterAll(() => server.close());
