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
  children,
}: {
  /** The route's existing `main` testid (`paths-switcher` / `path-view` /
   *  `lesson-view`) — unchanged, so nothing that queries it today has to move. */
  testid: string;
  width: keyof typeof WIDTH_CAP;
  /** Omitted entirely (no `<aside>` at all) on the Switcher route, which has
   *  nothing selected yet to show a sidebar for. */
  sidebar?: ReactNode;
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
    </div>
  );
}
