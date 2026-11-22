import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LessonVisual as Visual } from "../lib/api";
import { buildKitDocument, LessonVisual, VISUAL_READY_TIMEOUT_MS } from "./lesson-visual";

// The host half of the lesson-visual sandbox (docs/tdds/lesson-visuals.md).
// jsdom does not run srcdoc scripts, so the frame's messages are simulated with a
// MessageEvent whose `source` is (or is not) the frame's own contentWindow —
// exactly the check the host makes. Real-browser containment is proven by the
// Playwright probe journey.

const VISUAL: Visual = {
  id: "0b6c1f6e-3d1a-4a8e-9a8b-2f6d3c1e9a10",
  kind: "interactive",
  title: "Simple and compound interest",
  alt: "Two bars compare simple and compound growth of $1,000.",
  source: "<p>hi</p><script>kit.ready()</script>",
  kit_version: 1,
};

function frame(): HTMLIFrameElement {
  return screen.getByTitle(VISUAL.title) as HTMLIFrameElement;
}

function post(data: unknown, source: MessageEventSource | null) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data, source }));
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("LessonVisual", () => {
  it("runs in an iframe sandboxed with allow-scripts only", () => {
    render(<LessonVisual visual={VISUAL} />);

    const iframe = frame();
    // Regression guard: allow-same-origin would give the frame our origin.
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    expect(iframe.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(iframe.getAttribute("srcdoc")).toContain(VISUAL.source);
    expect(iframe.getAttribute("srcdoc")).toContain("default-src 'none'");
    expect(screen.getByTestId("lesson-visual").getAttribute("data-visual-status")).toBe("loading");
  });

  it("becomes ready on an ok status from its own frame and sizes to height", () => {
    render(<LessonVisual visual={VISUAL} />);
    const source = frame().contentWindow;

    post({ type: "status", status: "ok" }, source);
    post({ type: "height", height: 10_000 }, source);

    expect(screen.getByTestId("lesson-visual").getAttribute("data-visual-status")).toBe("ready");
    expect(frame().style.height).toBe("640px");
    act(() => {
      vi.advanceTimersByTime(VISUAL_READY_TIMEOUT_MS + 1);
    });
    expect(screen.queryByTitle(VISUAL.title)).not.toBeNull();
  });

  it("keeps its placeholder height when an offscreen frame measures 0", () => {
    // Chrome throttles an offscreen cross-origin frame: kit.ready() then
    // measures an unlaid-out body as 0. The real height follows on scroll.
    render(<LessonVisual visual={VISUAL} />);
    const source = frame().contentWindow;

    post({ type: "height", height: 0 }, source);
    expect(frame().style.height).toBe("");

    post({ type: "height", height: 250 }, source);
    expect(frame().style.height).toBe("250px");
  });

  it("falls back to the alt text on an error status", () => {
    render(<LessonVisual visual={VISUAL} />);

    post({ type: "status", status: "error" }, frame().contentWindow);

    expect(screen.queryByTitle(VISUAL.title)).toBeNull();
    expect(screen.getByTestId("lesson-visual-fallback").textContent).toContain(VISUAL.alt);
  });

  it("falls back to the alt text when no ok arrives in time", () => {
    render(<LessonVisual visual={VISUAL} />);

    act(() => {
      vi.advanceTimersByTime(VISUAL_READY_TIMEOUT_MS + 1);
    });

    expect(screen.queryByTitle(VISUAL.title)).toBeNull();
    expect(screen.getByTestId("lesson-visual-fallback").textContent).toContain(VISUAL.alt);
  });

  it("ignores messages from any other source, and malformed ones", () => {
    render(<LessonVisual visual={VISUAL} />);
    const source = frame().contentWindow;

    post({ type: "status", status: "ok" }, window);
    post({ type: "status", status: "error" }, null);
    post({ type: "height", height: 300 }, window);
    post({ type: "height", height: "300" }, source);
    post("ok", source);

    expect(screen.getByTestId("lesson-visual").getAttribute("data-visual-status")).toBe("loading");
    expect(frame().style.height).toBe("");
  });

  it("never puts the source into the parent document", () => {
    render(<LessonVisual visual={{ ...VISUAL, source: '<b id="leak">x</b>' }} />);

    expect(document.getElementById("leak")).toBeNull();
  });
});

describe("buildKitDocument", () => {
  it("wraps the body with the CSP, the current Nocturne tokens and the kit API", () => {
    const doc = buildKitDocument("<p>body</p>");

    expect(doc).toContain(
      "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'",
    );
    for (const token of ["#11161b", "#ebe6da", "#56b3ab", "#9a92d8", "#ef8f82"]) {
      expect(doc).toContain(token);
    }
    expect(doc).toContain("window.kit");
    expect(doc).toContain("observe(document.body)");
    expect(doc.indexOf("<p>body</p>")).toBeGreaterThan(doc.indexOf("window.kit"));
  });
});
