// Re-pinning `position: fixed` chrome after the on-screen keyboard leaves.
//
// iOS Safari does not shrink the layout viewport for the keyboard. It shrinks
// the *visual* viewport, and lets the layout viewport — the box every `fixed`
// element is anchored to — drift upward so the focused field stays in view.
// When the keyboard goes, the visual viewport grows back, but the layout
// viewport is left where the keyboard pushed it until the next scroll. Until
// then, a `fixed bottom-5` box (`TutorMark`, `ShapingMark`) and the rail sheet
// itself sit a keyboard's height up the screen, "unpinned".
//
// The lesson route hits this whenever the rail closes with its composer
// focused: the textarea unmounts, the keyboard dismisses, and the mark mounts
// against the drifted viewport. A scroll is what re-syncs the two viewports, so
// `useRepinAfterKeyboard` performs one — a one-pixel round trip inside a single
// task, which paints nothing — the moment the visual viewport grows back from
// keyboard height to full height. Every other resize (the address bar
// collapsing, a desktop window being dragged) is left alone.

import { useEffect } from "react";

/**
 * A visual viewport shorter than this fraction of the tallest one seen is
 * behind a keyboard. An iPhone keyboard takes over a third of the screen;
 * Safari's collapsing chrome takes under a tenth, so the two never meet.
 */
const KEYBOARD_FRACTION = 0.75;

/** ...and one back above this fraction has its keyboard gone. */
const SETTLED_FRACTION = 0.9;

/** What the tracker remembers between resizes. */
export interface ViewportTrack {
  /** Rotation and pinch-zoom change the width; either restarts the record. */
  width: number;
  /** The tallest height seen at this width — full height, chrome collapsed. */
  tallest: number;
  /**
   * Latched the moment a height reads as keyboard-short, and held through
   * any part-way heights a keyboard reports on its way out, until the
   * viewport settles back to full height.
   */
  behindKeyboard: boolean;
}

export function startViewportTrack(width: number, height: number): ViewportTrack {
  return { width, tallest: height, behindKeyboard: false };
}

/**
 * Fold one resize into the record, and say whether it was the keyboard
 * leaving: a keyboard-short height was seen, and this height is full again.
 */
export function trackViewport(
  previous: ViewportTrack,
  width: number,
  height: number,
): { track: ViewportTrack; keyboardLeft: boolean } {
  if (width !== previous.width) {
    return { track: startViewportTrack(width, height), keyboardLeft: false };
  }
  const tallest = Math.max(previous.tallest, height);
  const settled = height >= tallest * SETTLED_FRACTION;
  const keyboardLeft = previous.behindKeyboard && settled;
  const behindKeyboard =
    height < tallest * KEYBOARD_FRACTION || (previous.behindKeyboard && !settled);
  return { track: { width, tallest, behindKeyboard }, keyboardLeft };
}

/**
 * The re-sync: scroll one pixel away and straight back. Both moves land in the
 * same task, so nothing is painted in between — the scroll's only effect is to
 * make the browser recompute the layout viewport against the visual one.
 * Away means *up* wherever possible, so the round trip is never clamped away
 * to nothing at the bottom of a page.
 */
export function nudgeScroll(): void {
  const x = window.scrollX;
  const y = window.scrollY;
  window.scrollTo(x, y > 0 ? y - 1 : y + 1);
  window.scrollTo(x, y);
}

/**
 * Mounted once, in the app shell: every `fixed` surface on every route is
 * anchored to the same layout viewport, so one listener re-pins them all.
 * Browsers without `visualViewport` are browsers without the drift.
 */
export function useRepinAfterKeyboard(): void {
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    let track = startViewportTrack(viewport.width, viewport.height);
    const onResize = () => {
      const next = trackViewport(track, viewport.width, viewport.height);
      track = next.track;
      if (next.keyboardLeft) nudgeScroll();
    };
    viewport.addEventListener("resize", onResize);
    return () => viewport.removeEventListener("resize", onResize);
  }, []);
}
