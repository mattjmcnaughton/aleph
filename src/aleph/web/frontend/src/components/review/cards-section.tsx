// "Your cards" as a real section (design critique, theme 2).
//
// The door into `/cards` used to be a centred, mist-grey, undecorated line
// sitting immediately above the paths list — the only centred element on the
// page, in the one position that makes a line of text read as a *heading for
// what follows*. The reasoning behind having the door at all was right (a
// learner with 200 kept cards and none due today is exactly the learner who
// wants to browse them, and that is the one day `DueTodayCard` renders
// nothing), but nobody was going to click a section heading. It gets the same
// grammar Paths and Beats have instead: a kicker, a line of state, an action.
//
// Gated on the `flashcards` flag only — never on `due_count` — which is the
// property that makes it survive a quiet day.

import type { ReviewSummary } from "../../lib/api";
import { SectionHeader } from "../section-header";

/**
 * `summary` follows the same decoration contract as `DueTodayCard` and
 * `ReviewPill`: `undefined` on load, on a flag-off `skipToken` idle, or on a
 * failed `GET /reviews/summary`. Unlike those two, `undefined` here does **not**
 * hide the section — it only empties the summary line. The section is the
 * learner's route to their own kept cards, and a failed *summary* request is
 * no reason to take the door away; it is a reason not to claim a number.
 */
export function CardsSection({ summary }: { summary: ReviewSummary | undefined }) {
  return (
    <SectionHeader
      kicker="Your cards"
      summary={<span data-testid="cards-section-summary">{summaryLine(summary)}</span>}
      action={{ to: "/cards", label: "Browse", testid: "your-cards-link" }}
    />
  );
}

/**
 * No kept-card total is stated here, deliberately: `GET /flashcards` returns a
 * page and its cursor, never a count, so any total on this line would be
 * invented. What the summary *does* know is today's queue, which is also the
 * only number that changes what the learner would do next.
 */
function summaryLine(summary: ReviewSummary | undefined): string {
  if (summary === undefined) return "Browse and edit every card you've kept";
  if (summary.due_count === 0) return "Nothing due today";
  return `${summary.due_count} due today`;
}
