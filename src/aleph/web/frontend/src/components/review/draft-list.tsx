// The drafts block below a lesson's completion state (PRD §3, Phase 3 TDD
// §5.2/§8): "Aleph drafted N cards", per-card keep/discard defaulting to kept,
// the primary action naming its own count, and "Skip — keep none" equally
// reachable. Rendered by `routes/lessons.$lessonId.tsx`, which owns the
// trigger + poll + keep mutations; this component owns the toggle state and
// the drafting-run's own state branches (generating / failed / generated).

import { useState } from "react";
import type { FlashcardDraftCard, FlashcardDrafts } from "../../lib/api";
import { PRIMARY_CTA, RetryNotices, Spinner, StateCard } from "../state-card";

export function DraftList({
  drafts,
  onKeep,
  keeping,
  keepErrored,
  onRetry,
  retrying,
  triggerRateLimited,
  triggerErrored,
}: {
  /** The poll's latest value — `undefined` while loading, gated off, or on a
   *  failed fetch. Drafting is optional next to an already-recorded
   *  completion, so a failed poll renders nothing rather than an error card:
   *  there is nothing here worth interrupting the lesson's own success state
   *  for. */
  drafts: FlashcardDrafts | undefined;
  /** Submit a keep — `[]` for "Skip — keep none". */
  onKeep: (keptIds: string[]) => void;
  keeping: boolean;
  keepErrored: boolean;
  /** Re-trigger drafting after a `failed` run (§5.6 — a retry affordance, never
   *  a dead spinner, the same shape `state-card.tsx` already gives generation). */
  onRetry: () => void;
  retrying: boolean;
  /**
   * The trigger mutation hit the daily cap (§5.6's `429` row): the completion
   * still stands, but no run was ever claimed, so the poll is stuck at
   * `not_started` with nothing else to show for it. Ticket 3's fix — without
   * this, a capped learner sees the completion succeed and then nothing,
   * indistinguishable from "this lesson produced no cards".
   */
  triggerRateLimited: boolean;
  /** The trigger mutation failed for any other reason (e.g. a `409
   *  lesson_not_complete` race) — the same swallow, a generic notice instead
   *  of the daily-cap one. */
  triggerErrored: boolean;
}) {
  if (drafts === undefined) return null;

  if (drafts.state === "not_started") {
    // The ordinary case — drafting was never triggered for this lesson, or the
    // trigger is still in flight and hasn't landed `generating` yet — is
    // silent, same as before. Only a trigger that actually failed earns a line
    // here, beside the state the failure leaves the poll stuck in (§5.6).
    if (!triggerRateLimited && !triggerErrored) return null;
    return (
      <div data-testid="flashcard-drafts-trigger-error" className="mt-6">
        <RetryNotices
          testidPrefix="flashcard-drafts-trigger"
          rateLimited={triggerRateLimited}
          errored={triggerErrored}
          rateLimitMessage="Drafting is unavailable today — you've reached today's limit. Try again tomorrow."
        />
      </div>
    );
  }

  if (drafts.state === "generating") {
    return (
      <StateCard testid="flashcard-drafts-generating" spacing="mt-6" ariaLive="polite">
        <Spinner />
        <p className="mt-3 text-sm leading-6 text-mist">Aleph is drafting flashcards…</p>
      </StateCard>
    );
  }

  if (drafts.state === "failed") {
    return (
      <StateCard testid="flashcard-drafts-failed" spacing="mt-6" ariaLive="polite">
        <p className="text-sm leading-6 text-mist">Aleph couldn't draft flashcards this time.</p>
        <button
          type="button"
          data-testid="flashcard-drafts-retry"
          onClick={onRetry}
          disabled={retrying}
          className={`mt-4 ${PRIMARY_CTA}`}
        >
          {retrying ? "Retrying…" : "Try again"}
        </button>
      </StateCard>
    );
  }

  // `generated` with nothing left to keep — already resolved (kept, or every
  // draft discarded) in an earlier visit (D7's "abandoned drafts wait", now
  // resumed and finished). Nothing to show; the lesson stands complete either way.
  if (drafts.cards.length === 0) return null;

  return (
    <DraftReview cards={drafts.cards} onKeep={onKeep} keeping={keeping} keepErrored={keepErrored} />
  );
}

function DraftReview({
  cards,
  onKeep,
  keeping,
  keepErrored,
}: {
  cards: FlashcardDraftCard[];
  onKeep: (keptIds: string[]) => void;
  keeping: boolean;
  keepErrored: boolean;
}) {
  // All keeping by default (PRD §3): the common case is one tap, not four.
  // Initialized once from the resolved card set — `DraftList` above only ever
  // mounts this once `state === "generated"` with cards, so there is no
  // mid-review reset to worry about.
  const [kept, setKept] = useState<Set<string>>(() => new Set(cards.map((card) => card.id)));

  function toggle(id: string): void {
    setKept((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  const keptCount = kept.size;
  // "Keep none" rather than "Keep 0 cards" — the mock's own copy, kept
  // verbatim: the primary action always names what tapping it will do.
  const primaryLabel =
    keptCount === 0 ? "Keep none" : `Keep ${keptCount} ${keptCount === 1 ? "card" : "cards"}`;

  return (
    <div
      data-testid="draft-list"
      className="mt-6 rounded-lg border border-iris-700 bg-iris-900 p-4"
    >
      <div className="flex items-baseline justify-between gap-2">
        <p className="kicker text-iris-400">
          Aleph drafted {cards.length} {cards.length === 1 ? "card" : "cards"}
        </p>
        <span data-testid="draft-keep-count" className="text-xs tabular-nums text-mist">
          {keptCount} kept
        </span>
      </div>
      <p className="mt-2 text-xs leading-5 text-mist">
        Keep only what you want to see again. Discarded drafts are not saved.
      </p>

      <ul className="mt-3 flex flex-col gap-2">
        {cards.map((card) => {
          const isKept = kept.has(card.id);
          return (
            <li
              key={card.id}
              data-testid="draft-card"
              data-kept={isKept}
              className={`flex items-start gap-3 rounded-md bg-elevated p-3 transition-opacity ${
                isKept ? "" : "opacity-40"
              }`}
            >
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold leading-snug">{card.front}</p>
                <p className="mt-1 text-xs leading-5 text-mist">{card.back}</p>
              </div>
              <button
                type="button"
                data-testid="draft-toggle"
                aria-pressed={isKept}
                aria-label={isKept ? `Discard "${card.front}"` : `Keep "${card.front}"`}
                onClick={() => toggle(card.id)}
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-sm font-bold ${
                  isKept ? "bg-teal text-night" : "text-slate ring-1 ring-inset ring-faint"
                }`}
              >
                ✓
              </button>
            </li>
          );
        })}
      </ul>

      {keepErrored ? (
        <p
          data-testid="draft-keep-error"
          aria-live="assertive"
          className="mt-4 text-sm leading-6 text-danger"
        >
          That didn't go through. Check your connection and try again.
        </p>
      ) : null}

      <button
        type="button"
        data-testid="draft-keep-button"
        onClick={() => onKeep([...kept])}
        disabled={keeping}
        className={`mt-4 ${PRIMARY_CTA}`}
      >
        {keeping ? "Saving…" : primaryLabel}
      </button>
      <button
        type="button"
        data-testid="draft-skip-button"
        onClick={() => onKeep([])}
        disabled={keeping}
        className="mt-2 w-full rounded-md py-2 text-sm text-mist transition-colors hover:text-porcelain disabled:cursor-not-allowed disabled:opacity-50"
      >
        Skip — keep none
      </button>
    </div>
  );
}
