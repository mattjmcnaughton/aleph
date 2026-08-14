// One section header for every list on the home screen (design critique,
// themes 1 and 2).
//
// Home used to spell this three different ways: "Your paths" was a `kicker`
// stranded up in the hero beside a filled primary button, "Your beats" was a
// `kicker` sitting on its own list beside a bare teal text link, and "Your
// cards" was a centred grey line with no action at all — which, sitting
// directly above the paths list, read as a *heading for the eight rows below
// it* rather than as a link to somewhere else entirely. Three sections, three
// grammars, and the one with the weakest grammar was the one nobody could
// find. This is the single shape they all use now.

import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { ChevronIcon } from "./chevron-icon";

/**
 * The action beside a section's kicker: teal text at a real touch size, never
 * a filled button — that treatment belongs to the page's one primary CTA, on a
 * screen that already had teal doing five different jobs (theme 4).
 *
 * Deliberately **one affordance**, not a per-section choice. Note what is left
 * using it: "Browse" on the cards section, which goes *somewhere else*. The
 * two "start a new thing of this section's kind" actions — "New path" and
 * "Deploy analyst" — have both moved into home's New menu, since duplicating
 * the same door per section is what stranded a teal link mid-page.
 */
function SectionAction({
  to,
  testid,
  children,
}: {
  to: string;
  testid: string;
  children: ReactNode;
}) {
  return (
    <Link
      // The `to` union is wider than this component can usefully model; every
      // call site passes a literal route that TanStack has already generated.
      to={to as never}
      data-testid={testid}
      // >=44px touch target: `min-h-[44px]` alone is not enough on an inline
      // link whose box is only as tall as its text, so `inline-flex
      // items-center` is what gives the padding somewhere to expand into.
      className="inline-flex min-h-[44px] shrink-0 items-center rounded-md px-2 text-sm font-semibold text-teal transition-colors hover:text-teal-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal"
    >
      {children}
    </Link>
  );
}

/**
 * What makes a section's kicker a disclosure control.
 *
 * The state is the *caller's*, not this component's: home owns which sections
 * are open, because the thing being collapsed is the caller's own subtree and
 * only the caller can label the header with what is hidden inside it ("8
 * paths"). The chevron and the ARIA wiring live here so the two collapsible
 * lists cannot disagree about either.
 */
export interface SectionCollapse {
  open: boolean;
  onToggle: () => void;
  /** The `id` of the region this header discloses — its `aria-controls`. */
  controls: string;
  testid: string;
}

/**
 * A section's kicker, an optional one-line summary beneath it, and an optional
 * action on the right.
 *
 * `action` is a `{to, label, testid}` triple rather than a `ReactNode` slot on
 * purpose — a slot is exactly how the three headers drifted apart in the first
 * place, since it lets each call site bring its own button.
 */
export function SectionHeader({
  kicker,
  summary,
  action,
  collapse,
  spacing = "mt-10",
}: {
  kicker: string;
  /** e.g. "10 due today" — the section's state, in the row grammar's voice. */
  summary?: ReactNode;
  action?: { to: string; label: string; testid: string };
  /** Present when the section collapses; the kicker becomes its toggle. */
  collapse?: SectionCollapse;
  spacing?: string;
}) {
  return (
    <div className={`${spacing} flex items-center justify-between gap-4`}>
      <div className="min-w-0">
        {collapse ? (
          // The kicker *is* the toggle rather than growing a separate control
          // beside it: the whole label is the hit area (a 11px chevron alone is
          // not a touch target), and the section keeps exactly one affordance
          // on its left, which is the rule this file exists to hold.
          <button
            type="button"
            data-testid={collapse.testid}
            aria-expanded={collapse.open}
            aria-controls={collapse.controls}
            onClick={collapse.onToggle}
            className="kicker inline-flex min-h-[44px] items-center gap-1.5 rounded-md pr-2 transition-colors hover:text-porcelain focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal"
          >
            <ChevronIcon open={collapse.open} />
            {kicker}
          </button>
        ) : (
          <p className="kicker">{kicker}</p>
        )}
        {summary ? <p className="mt-1 text-sm text-mist">{summary}</p> : null}
      </div>
      {action ? (
        <SectionAction to={action.to} testid={action.testid}>
          {action.label}
        </SectionAction>
      ) : null}
    </div>
  );
}
