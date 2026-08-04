// One card in the review session (PRD §3/§4.6, Phase 3 TDD §8): front, tap to
// reveal the back, then the two grades. `routes/review.tsx` owns the queue,
// the reveal state (reset per card), and the grade mutation; this component
// is purely presentational.

import type { QueueCard } from "../../lib/api";
import { CardSource } from "./card-source";

export function ReviewCard({
  card,
  revealed,
  onReveal,
  onGrade,
  grading,
}: {
  card: QueueCard;
  revealed: boolean;
  onReveal: () => void;
  /** `"again"` or `"got_it"` — the fixed two-outcome ladder (CONTEXT.md: *Review*). */
  onGrade: (grade: "again" | "got_it") => void;
  grading: boolean;
}) {
  return (
    <div className="flex flex-col gap-3">
      <button
        type="button"
        data-testid="review-card-flip"
        aria-expanded={revealed}
        onClick={onReveal}
        disabled={revealed}
        className="flex min-h-[13rem] w-full flex-col gap-3.5 rounded-lg border border-faint bg-surface p-5 text-left shadow-sm"
      >
        <p data-testid="review-card-front" className="text-lg font-semibold leading-snug">
          {card.front}
        </p>
        {revealed ? (
          <p
            data-testid="review-card-back"
            className="border-t border-divider pt-3.5 text-sm leading-6 text-porcelain"
          >
            {card.back}
          </p>
        ) : null}
        <p className="mt-auto text-xs text-slate">
          {revealed ? "How well did you know it?" : "Tap to reveal"}
        </p>
      </button>

      {/* Grades stay hidden until the answer is shown — grading a card you have
          not seen the back of is not a review (mock's own rule). */}
      {revealed ? (
        <div className="grid grid-cols-2 gap-2.5">
          <button
            type="button"
            data-testid="review-grade-again"
            onClick={() => onGrade("again")}
            disabled={grading}
            className="flex flex-col items-center gap-0.5 rounded-md bg-elevated px-1 py-3 text-sm font-semibold text-danger ring-1 ring-inset ring-danger-border/50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Again
            <span className="text-[11px] font-medium text-danger/75">later today</span>
          </button>
          <button
            type="button"
            data-testid="review-grade-got-it"
            onClick={() => onGrade("got_it")}
            disabled={grading}
            className="flex flex-col items-center gap-0.5 rounded-md bg-elevated px-1 py-3 text-sm font-semibold text-porcelain ring-1 ring-inset ring-white/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Got it
            {/* Read straight off the payload (§8) — never a ladder constant
                duplicated client-side. The server is the one place the ladder
                lives (`FLASHCARD_LADDER_DAYS`), and this is the whole reason
                `got_it_interval_days` rides on every queue card. */}
            <span
              data-testid="review-grade-interval"
              className="text-[11px] font-medium text-slate"
            >
              in {card.got_it_interval_days} {card.got_it_interval_days === 1 ? "day" : "days"}
            </span>
          </button>
        </div>
      ) : null}

      {/* Extracted to `card-source.tsx` (AL-410) so `/cards` renders the
          identical citation rule rather than re-deriving it — see that
          file's own header for D12's reasoning. */}
      <CardSource source={card.source} />
    </div>
  );
}
