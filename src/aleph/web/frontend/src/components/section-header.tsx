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

/**
 * The action beside a section's kicker.
 *
 * Deliberately **one affordance**, not a per-section choice: "New path" and
 * "Deploy analyst" are the same kind of move (start a new thing of this
 * section's kind) and were rendering as a filled 44px-tall primary button and
 * a 20px inline text link respectively. Teal text at a real touch size is the
 * shape both get, which also takes a filled teal button off a screen that had
 * teal doing five different jobs (theme 4).
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
  spacing = "mt-10",
}: {
  kicker: string;
  /** e.g. "10 due today" — the section's state, in the row grammar's voice. */
  summary?: ReactNode;
  action?: { to: string; label: string; testid: string };
  spacing?: string;
}) {
  return (
    <div className={`${spacing} flex items-center justify-between gap-4`}>
      <div className="min-w-0">
        <p className="kicker">{kicker}</p>
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
