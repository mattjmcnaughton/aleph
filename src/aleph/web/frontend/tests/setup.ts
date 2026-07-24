import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { resetPaths } from "../src/mocks/paths";
import { server } from "../src/mocks/server";

window.scrollTo = vi.fn();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  resetPaths();
  vi.restoreAllMocks();
  cleanup();
  window.history.pushState({}, "", "/");
});
afterAll(() => server.close());
