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

import type { ReactNode } from "react";

/** Per-route content cap at `lg`, straight from the mock (#2a/#2b/#2c). */
const WIDTH_CAP = {
  lesson: "lg:max-w-[680px]",
  path: "lg:max-w-[900px]",
  switcher: "lg:max-w-[1100px]",
} as const;

export function Workspace({
  testid,
  width,
  sidebar,
  tutorRail,
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
   * presentation — the one `<aside>` below is a bottom sheet over the lesson,
   * and at `lg` a docked 400px column beside the lesson's 680px. Same tree, two
   * CSS presentations: no `matchMedia`, no width-conditional rendering.
   */
  tutorRail?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="lg:flex lg:items-stretch">
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
        className="mx-auto w-full max-w-[480px] px-4 py-8 lg:mx-0 lg:max-w-none lg:flex-1 lg:px-10 lg:py-10"
      >
        <div className={`mx-auto w-full ${WIDTH_CAP[width]}`}>{children}</div>
      </main>

      {tutorRail ? (
        <aside
          data-testid="tutor-rail-column"
          // Below `lg`: a sheet anchored to the bottom of the viewport, capped
          // so the lesson stays visible behind it (the PRD's chosen entry).
          // At `lg`: an ordinary flex sibling — the docked right column.
          className="fixed inset-x-0 bottom-0 z-40 flex max-h-[75vh] flex-col rounded-t-lg border-t border-divider bg-night shadow-lg lg:static lg:z-auto lg:max-h-none lg:w-[400px] lg:shrink-0 lg:rounded-none lg:border-l lg:border-t-0 lg:shadow-none"
        >
          {tutorRail}
        </aside>
      ) : null}
    </div>
  );
}
