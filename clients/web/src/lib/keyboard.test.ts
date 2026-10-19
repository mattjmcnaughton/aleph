import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startViewportTrack, trackViewport, useRepinAfterKeyboard } from "./keyboard";

// An iPhone 15 Pro in portrait: 393×852, with a 336pt keyboard.
const WIDTH = 393;
const FULL = 852;
const BEHIND_KEYBOARD = FULL - 336;

describe("trackViewport — telling the keyboard leaving from every other resize", () => {
  it("fires once the height grows back from keyboard height to full height", () => {
    const down = trackViewport(startViewportTrack(WIDTH, FULL), WIDTH, BEHIND_KEYBOARD);
    expect(down.keyboardLeft).toBe(false);

    const up = trackViewport(down.track, WIDTH, FULL);
    expect(up.keyboardLeft).toBe(true);
  });

  it("stays quiet for Safari's chrome collapsing and expanding", () => {
    // The address bar hides on scroll (+~60pt) and comes back on the way up.
    const taller = trackViewport(startViewportTrack(WIDTH, FULL - 60), WIDTH, FULL);
    expect(taller.keyboardLeft).toBe(false);
    const shorter = trackViewport(taller.track, WIDTH, FULL - 60);
    expect(shorter.keyboardLeft).toBe(false);
    const backAgain = trackViewport(shorter.track, WIDTH, FULL);
    expect(backAgain.keyboardLeft).toBe(false);
  });

  it("learns the full height from a later resize, not only the first one", () => {
    // Mounted with the chrome expanded; the chrome then collapses, giving a
    // taller viewport than the record started with. The keyboard's height is
    // judged against that taller one from then on.
    const collapsed = trackViewport(startViewportTrack(WIDTH, FULL - 60), WIDTH, FULL);
    expect(collapsed.track.tallest).toBe(FULL);
    const down = trackViewport(collapsed.track, WIDTH, BEHIND_KEYBOARD);
    const up = trackViewport(down.track, WIDTH, FULL);
    expect(up.keyboardLeft).toBe(true);
  });

  it("restarts the record on a width change, so a rotation is never a keyboard", () => {
    // Portrait to landscape: the height halves, then rotating back doubles it
    // — the same shape as a keyboard coming and going, at a different width.
    const landscape = trackViewport(startViewportTrack(WIDTH, FULL), FULL, WIDTH);
    expect(landscape.keyboardLeft).toBe(false);
    expect(landscape.track).toEqual({ width: FULL, tallest: WIDTH, behindKeyboard: false });

    const portrait = trackViewport(landscape.track, WIDTH, FULL);
    expect(portrait.keyboardLeft).toBe(false);
  });

  it("does not fire while the keyboard is only part-way gone", () => {
    const down = trackViewport(startViewportTrack(WIDTH, FULL), WIDTH, BEHIND_KEYBOARD);
    const halfway = trackViewport(down.track, WIDTH, FULL - 168);
    expect(halfway.keyboardLeft).toBe(false);
    // ...and does, exactly once, when it is.
    const gone = trackViewport(halfway.track, WIDTH, FULL);
    expect(gone.keyboardLeft).toBe(true);
    const still = trackViewport(gone.track, WIDTH, FULL);
    expect(still.keyboardLeft).toBe(false);
  });
});

/** jsdom has no `visualViewport`; this is the two fields and the event the hook reads. */
class FakeVisualViewport extends EventTarget {
  width = WIDTH;
  height = FULL;

  resizeTo(height: number): void {
    this.height = height;
    this.dispatchEvent(new Event("resize"));
  }
}

describe("useRepinAfterKeyboard — the scroll that re-syncs the layout viewport", () => {
  let viewport: FakeVisualViewport;
  let scrollTo: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    viewport = new FakeVisualViewport();
    Object.defineProperty(window, "visualViewport", { value: viewport, configurable: true });
    Object.defineProperty(window, "scrollY", { value: 120, configurable: true });
    scrollTo = vi.fn();
    window.scrollTo = scrollTo as unknown as typeof window.scrollTo;
  });

  afterEach(() => {
    Object.defineProperty(window, "visualViewport", { value: undefined, configurable: true });
    Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
  });

  it("scrolls one pixel away and straight back when the keyboard leaves", () => {
    renderHook(() => useRepinAfterKeyboard());

    viewport.resizeTo(BEHIND_KEYBOARD);
    expect(scrollTo).not.toHaveBeenCalled();

    viewport.resizeTo(FULL);
    expect(scrollTo.mock.calls).toEqual([
      [0, 119],
      [0, 120],
    ]);
  });

  it("nudges downward from the top of the page, where up would clamp to nothing", () => {
    Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
    renderHook(() => useRepinAfterKeyboard());

    viewport.resizeTo(BEHIND_KEYBOARD);
    viewport.resizeTo(FULL);
    expect(scrollTo.mock.calls).toEqual([
      [0, 1],
      [0, 0],
    ]);
  });

  it("leaves every other resize alone", () => {
    renderHook(() => useRepinAfterKeyboard());

    viewport.resizeTo(FULL - 60);
    viewport.resizeTo(FULL);
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("stops listening on unmount", () => {
    const { unmount } = renderHook(() => useRepinAfterKeyboard());
    viewport.resizeTo(BEHIND_KEYBOARD);
    unmount();

    viewport.resizeTo(FULL);
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("is inert where there is no visualViewport to read", () => {
    Object.defineProperty(window, "visualViewport", { value: undefined, configurable: true });
    expect(() => renderHook(() => useRepinAfterKeyboard())).not.toThrow();
  });
});
