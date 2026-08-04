// The card citation line — "From {lesson} · {path}" — shared by the review
// session (`review-card.tsx`) and the card list (`card-row.tsx`, AL-410).
// Extracted rather than re-derived a second time: AL-410 plan §6 is explicit
// that the citation on `/cards` must render "exactly as the review session
// does", and this is D12's one home for the rule that makes that true —
// `kind === "linked"` is the one case a citation is safe to dereference at
// all (docs/api.md: a `DegradedCitationDTO` carries no `lesson_id` field to
// link to in the first place). Both titles are copied onto the card at draft
// time (D12), which is what lets the citation's *text* survive its source
// lesson even once the link cannot (`w27.spec.ts`).

import { Link } from "@tanstack/react-router";
import type { FlashcardCitation } from "../../lib/api";

export function CardSource({
  source,
  testid = "review-card-source",
}: {
  source: FlashcardCitation;
  /** Defaults to the review session's original testid, so pulling this block
   *  out of `review-card.tsx` changes nothing any existing test (vitest or
   *  `w27.spec.ts`) already queries. `card-row.tsx` passes its own. */
  testid?: string;
}) {
  return (
    <p data-testid={testid} className="text-xs text-slate">
      From{" "}
      {source.kind === "linked" ? (
        <Link to="/lessons/$lessonId" params={{ lessonId: source.lesson_id }} className="text-teal">
          {source.lesson_title}
        </Link>
      ) : (
        source.lesson_title
      )}{" "}
      · {source.path_title}
    </p>
  );
}
