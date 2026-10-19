// The desktop shell (Turn 2, docs/architecture.md — Frontend). Desktop is added
// with CSS only, at Tailwind's `lg` breakpoint (1024px): no `matchMedia`, no JS
// viewport state, no width-conditional rendering. Below `lg` every class here
// falls back to exactly what shipped before this file existed — a phone gets
// the same single `max-w-[480px]` column, unchanged. `lg:` utilities widen
// `main` and add a `sidebar` beside it when the caller has one.
//
// The desktop-only markup (the `<aside>`, and anything a route hides with
// `hidden lg:…`) is present in the DOM at every width — jsdom has no CSS, so
// this is deliberate: it is what lets unit tests exercise the desktop layout
// directly instead of needing a real browser viewport.
//
// The rail `<aside>` (below) is the one exception to "widen and add a
// sidebar": at `lg` it is `lg:sticky` to the viewport rather than a plain flex
// sibling, so its own `main` (a whole lesson, easily taller than one screen)
// cannot stretch it and push its composer off the bottom. Still CSS only —
// the offset it sticks under is `--app-header-h` (`index.css`), not a value
// read from the DOM.

import type { ReactNode } from "react";

/** Per-route content cap at `lg`, straight from the mock (#2a/#2b/#2c). */
const WIDTH_CAP = {
  lesson: "lg:max-w-[680px]",
  path: "lg:max-w-[900px]",
  // Widened from 1100 once home became two real columns: 1100 was sized for a
  // single stack, and a work column plus a 260px rail inside it left the row
  // grid too little for a path title to survive untruncated.
  switcher: "lg:max-w-[1280px]",
} as const;

/**
 * Room to scroll the tail of `main` out from under the open rail.
 *
 * Below `lg` the rail is a sheet `fixed` over the bottom of the viewport, up to
 * `max-h-[75vh]` of it — so without this the last things on the page (on a
 * lesson: the Quick check's submit and "Mark complete") sit under the sheet at
 * maximum scroll, with nothing left to scroll into. Matching the sheet's own cap
 * is what guarantees every control can be brought above its top edge. At `lg`
 * the rail is a docked column beside `main` and covers nothing, so the padding
 * goes straight back to the route's ordinary `py-10`.
 *
 * Still CSS only (D12): this is conditional on the rail being **open**, which is
 * real shared state, and never on the viewport's width.
 */
const RAIL_CLEARANCE = "pb-[75vh] lg:pb-10";

export function Workspace({
  testid,
  width,
  sidebar,
  tutorRail,
  railTestid = "tutor-rail-column",
  children,
}: {
  /** The route's existing `main` testid (`paths-switcher` / `path-view` /
   *  `lesson-view`) — unchanged, so nothing that queries it today has to move. */
  testid: string;
  width: keyof typeof WIDTH_CAP;
  /** Omitted entirely (no `<aside>` at all) on the Switcher route, which has
   *  nothing selected yet to show a sidebar for. */
  sidebar?: ReactNode;
  /**
   * The tutor rail (Phase 2, AL-230 / TDD D12) — the third column, mirroring
   * `sidebar`. It differs from the sidebar in one way only: the sidebar's
   * `<aside>` is always in the DOM and hidden by CSS below `lg`, while this
   * slot is passed only while the rail is **open**, because open/closed is real
   * shared state the learner drives at every width. What is *not* JS is the
   * presentation — the one `<aside>` below is a bottom sheet over the lesson
   * below `lg`, and at `lg` a docked 400px column, `lg:sticky` beneath the app
   * header and viewport-tall in its own right, beside the lesson's 680px. Same
   * tree, two CSS presentations: no `matchMedia`, no width-conditional
   * rendering.
   *
   * Occupying this slot also gives `main` its bottom clearance below `lg`
   * (`RAIL_CLEARANCE`), so the sheet never strands the tail of the page.
   *
   * **The slot is the rail grammar, not one rail** (Phase 2B D14). The path
   * route mounts the *shaping* rail through the very same slot, which is the
   * whole content of "the rail tree's third mount": one `<aside>`, one pair of
   * CSS presentations, two surfaces that fill it.
   */
  tutorRail?: ReactNode;
  /**
   * The rail column's testid. It names *which* rail is docked, so a test can
   * assert the shaping rail's presentation without the two surfaces sharing a
   * selector. Defaults to the in-lesson rail's, leaving 2A's call site (and
   * every test that queries it) unchanged.
   */
  railTestid?: string;
  children: ReactNode;
}) {
  return (
    <div className="lg:flex lg:items-stretch lg:min-h-[calc(100dvh-var(--app-header-h))]">
      {sidebar ? (
        <aside
          data-testid="desktop-sidebar"
          className="hidden shrink-0 border-r border-divider lg:block lg:w-[300px]"
        >
          {sidebar}
        </aside>
      ) : null}

      <main
        data-testid={testid}
        className={`mx-auto w-full max-w-[480px] px-4 py-8 lg:mx-0 lg:max-w-none lg:flex-1 lg:px-10 lg:py-10 ${
          tutorRail ? RAIL_CLEARANCE : ""
        }`}
      >
        <div className={`mx-auto w-full ${WIDTH_CAP[width]}`}>{children}</div>
      </main>

      {tutorRail ? (
        <aside
          data-testid={railTestid}
          // Below `lg`: a sheet anchored to the bottom of the viewport, capped
          // so the lesson stays visible behind it (the PRD's chosen entry).
          //
          // At `lg`: sticky beneath the app header, one viewport tall minus the
          // header — rather than a plain flex sibling stretched to `main`'s full
          // height. A lesson runs to thousands of pixels, and stretching the
          // column to match it is what used to strand the composer off the
          // bottom of the screen.
          //
          // Three of those `lg:` utilities look redundant and are not.
          // `self-start`: the explicit height already defeats the row's
          // `items-stretch`, but a stretched item has no travel range to stick
          // through, so this says the requirement out loud. `bottom-auto`:
          // un-does the sheet's `bottom-0`, which sticky would otherwise honour
          // as a second constraint — it resolves to the same place only while
          // the box exactly fills its constraint rect, and that coincidence
          // should not be load-bearing. `overflow-hidden`: never reached on a
          // real screen (the thread scrolls inside itself), it just keeps a
          // viewport too short to hold the header and composer from spilling
          // the rail's chrome across the lesson.
          className="fixed inset-x-0 bottom-0 z-40 flex max-h-[75vh] flex-col rounded-t-lg border-t border-divider bg-night shadow-lg lg:sticky lg:top-[var(--app-header-h)] lg:bottom-auto lg:z-auto lg:h-[calc(100dvh-var(--app-header-h))] lg:max-h-none lg:w-[400px] lg:shrink-0 lg:self-start lg:overflow-hidden lg:rounded-none lg:border-l lg:border-t-0 lg:shadow-none"
        >
          {tutorRail}
        </aside>
      ) : null}
    </div>
  );
}
