// One row of `/cards` (AL-410 plan §6): front visible, tap to expand into
// back + citation + due date, an inline edit (two textareas + Save, capped
// exactly like the drafting agent's own output — `lib/flashcard-caps.ts`),
// and delete behind the two-step confirm idiom `routes/index.tsx` already
// established for paths (`path-delete-button` / `path-delete-confirm`,
// mirrored here testid for testid via `card-delete-button` /
// `card-delete-confirm`). `card.rung` rides on the DTO but this row never
// reads it — only the due date reaches the learner (plan's product call #2).
//
// Owns its own expand/edit UI state locally (unlike `ReviewCard`, whose
// `revealed` state `routes/review.tsx` owns): a list shows many rows at once,
// each independently expandable/editable, so there is no single "the current
// card" for a parent to hold the way the one-at-a-time review session has.
// Deletion is the one piece of state still lifted to the route
// (`use-delete-card.ts`) — the same "only one row confirming at a time"
// discipline `routes/index.tsx`'s `useDeletePath` already enforces for paths.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { FLASHCARDS_QUERY_PREFIX, type CardListItem, updateCard } from "../../lib/api";
import {
  BACK_WORDS_MAX,
  FRONT_WORDS_MAX,
  canSaveCardEdit,
  countWords,
} from "../../lib/flashcard-caps";
import type { DeleteCard } from "../../lib/use-delete-card";
import { CardSource } from "./card-source";

/** Stable DOM id for a row's own Delete button — where focus returns after a
 *  cancelled confirm (`routes/cards.tsx`'s `onCancelled`, the C3 rule). */
export function cardDeleteButtonId(cardId: string): string {
  return `card-delete-${cardId}`;
}

/**
 * Parse a `YYYY-MM-DD` wire date into a local midnight `Date` — never
 * `new Date(iso)`, which parses as UTC (`activity-strip.tsx`'s own rule, for
 * the same reason: a learner west of UTC could otherwise read the wrong
 * calendar day).
 */
function localDateFromISO(iso: string): Date {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day);
}

/**
 * "Due in 3 days" / "Due today" / "Due yesterday" — AL-410 plan's product
 * call #2: the due date is the one thing a row shows, `rung` never renders.
 * Computed against the browser's own local midnight: unlike
 * `ReviewSummary`/`ReviewQueue`, `CardListResponse` carries no server
 * `today` (docs/api.md), so this is display-only and feeds no due/queue
 * decision — those stay entirely server-side regardless of what this label
 * prints. `now` defaults to the real clock; a test passes a fixed one so the
 * label does not depend on which day the suite happens to run.
 */
export function formatDueLabel(dueOn: string, now: Date = new Date()): string {
  const due = localDateFromISO(dueOn);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diffDays = Math.round((due.getTime() - today.getTime()) / 86_400_000);
  if (diffDays === 0) return "Due today";
  if (diffDays === 1) return "Due tomorrow";
  if (diffDays === -1) return "Due yesterday";
  if (diffDays > 1) return `Due in ${diffDays} days`;
  return `Due ${Math.abs(diffDays)} days ago`;
}

export function CardRow({ card, deletion }: { card: CardListItem; deletion: DeleteCard }) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [front, setFront] = useState(card.front);
  const [back, setBack] = useState(card.back);
  // The mutation's own successful return, preferred over the `card` prop
  // until the parent's `invalidateQueries` (plan §6) lands a fresh fetch that
  // agrees with it — otherwise the row would flash back to its pre-edit text
  // for exactly as long as that refetch takes.
  const [saved, setSaved] = useState<CardListItem | null>(null);
  const display = saved && saved.id === card.id ? saved : card;

  const saveMutation = useMutation({
    mutationFn: updateCard,
    onSuccess: (result) => {
      setSaved(result);
      setEditing(false);
      // The one invalidation every flashcards mutation makes (plan §6): it is
      // what keeps the Daily queue's cached copy of this same card (if it is
      // due today) and the header pill's due count from silently disagreeing
      // with the text just saved here.
      void queryClient.invalidateQueries({ queryKey: FLASHCARDS_QUERY_PREFIX });
    },
  });

  function startEdit(): void {
    setFront(display.front);
    setBack(display.back);
    saveMutation.reset();
    setEditing(true);
  }

  function cancelEdit(): void {
    saveMutation.reset();
    setEditing(false);
  }

  const canSave = canSaveCardEdit(front, back);
  const confirmingDelete = deletion.confirmingId === card.id;

  return (
    <li
      data-testid="card-row"
      data-card-id={card.id}
      className="rounded-lg border border-divider bg-surface p-4 shadow-sm"
    >
      <button
        type="button"
        data-testid="card-row-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((prev) => !prev)}
        className="flex w-full items-start justify-between gap-3 text-left"
      >
        <p data-testid="card-row-front" className="min-w-0 text-base font-semibold leading-snug">
          {display.front}
        </p>
        <span data-testid="card-row-due" className="shrink-0 whitespace-nowrap text-xs text-mist">
          {formatDueLabel(card.due_on)}
        </span>
      </button>

      {expanded ? (
        editing ? (
          <CardEditForm
            front={front}
            back={back}
            onFrontChange={setFront}
            onBackChange={setBack}
            canSave={canSave}
            saving={saveMutation.isPending}
            errored={saveMutation.isError}
            onSave={() => saveMutation.mutate({ cardId: card.id, front, back })}
            onCancel={cancelEdit}
          />
        ) : confirmingDelete ? (
          <CardDeleteConfirm
            deleting={deletion.isDeleting(card.id)}
            errored={deletion.isErrored(card.id)}
            onCancel={deletion.cancel}
            onConfirm={() => deletion.confirm(card.id)}
          />
        ) : (
          <div className="mt-3 flex flex-col gap-3 border-t border-divider pt-3">
            <p data-testid="card-row-back" className="text-sm leading-6 text-porcelain">
              {display.back}
            </p>
            <CardSource source={card.source} testid="card-row-source" />
            <div className="flex gap-2">
              <button
                type="button"
                data-testid="card-edit-button"
                onClick={startEdit}
                className="flex-1 rounded-md border border-divider px-3 py-1.5 text-sm text-mist transition-colors hover:border-teal/40 hover:text-porcelain"
              >
                Edit
              </button>
              <button
                type="button"
                id={cardDeleteButtonId(card.id)}
                data-testid="card-delete-button"
                aria-label={`Delete "${display.front}"`}
                onClick={() => deletion.ask(card.id)}
                className="flex-1 rounded-md border border-divider px-3 py-1.5 text-sm text-mist transition-colors hover:border-danger-border hover:text-danger"
              >
                Delete
              </button>
            </div>
          </div>
        )
      ) : null}
    </li>
  );
}

function CardEditForm({
  front,
  back,
  onFrontChange,
  onBackChange,
  canSave,
  saving,
  errored,
  onSave,
  onCancel,
}: {
  front: string;
  back: string;
  onFrontChange: (value: string) => void;
  onBackChange: (value: string) => void;
  /** Whether `{front, back}` would pass the backend's own validator
   *  (`canSaveCardEdit` — the *same* caps `UpdateCardRequest` enforces). */
  canSave: boolean;
  saving: boolean;
  errored: boolean;
  onSave: () => void;
  onCancel: () => void;
}) {
  const frontWords = countWords(front);
  const backWords = countWords(back);

  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-divider pt-3">
      <label className="flex flex-col gap-1 text-xs text-mist">
        Front
        <textarea
          data-testid="card-edit-front"
          value={front}
          onChange={(event) => onFrontChange(event.target.value)}
          rows={2}
          className="rounded-md border border-divider bg-elevated p-2 text-sm text-porcelain"
        />
        <span
          data-testid="card-edit-front-count"
          className={`self-end text-[11px] ${frontWords > FRONT_WORDS_MAX ? "text-danger" : "text-slate"}`}
        >
          {frontWords}/{FRONT_WORDS_MAX} words
        </span>
      </label>

      <label className="flex flex-col gap-1 text-xs text-mist">
        Back
        <textarea
          data-testid="card-edit-back"
          value={back}
          onChange={(event) => onBackChange(event.target.value)}
          rows={3}
          className="rounded-md border border-divider bg-elevated p-2 text-sm text-porcelain"
        />
        <span
          data-testid="card-edit-back-count"
          className={`self-end text-[11px] ${backWords > BACK_WORDS_MAX ? "text-danger" : "text-slate"}`}
        >
          {backWords}/{BACK_WORDS_MAX} words
        </span>
      </label>

      {errored ? (
        <p
          data-testid="card-edit-error"
          aria-live="assertive"
          className="text-sm leading-6 text-danger"
        >
          That didn't go through. Check your connection and try again.
        </p>
      ) : null}

      <div className="flex gap-2">
        <button
          type="button"
          data-testid="card-edit-save"
          onClick={onSave}
          disabled={!canSave || saving}
          className="flex-1 rounded-md bg-teal px-3 py-2 text-sm font-semibold text-night transition-colors hover:bg-teal-bright disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          data-testid="card-edit-cancel"
          onClick={onCancel}
          disabled={saving}
          className="flex-1 rounded-md border border-divider px-3 py-2 text-sm font-semibold text-mist transition-colors hover:text-porcelain disabled:cursor-not-allowed disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

/**
 * The inline confirm step (mirrors `routes/index.tsx`'s `DeleteConfirm`,
 * testids and all, per AL-410 plan §6): deletion is destructive and not
 * undoable, so it always costs a second, deliberate tap rather than
 * `window.confirm`, which is not part of Nocturne and behaves badly on a
 * phone.
 */
function CardDeleteConfirm({
  deleting,
  errored,
  onCancel,
  onConfirm,
}: {
  deleting: boolean;
  errored: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  // The Delete button the learner just pressed is gone from the DOM, so focus
  // would land on <body>. It goes to the safe default instead — "Keep it",
  // never the destructive button (C3): a stray Enter must not delete a card.
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  return (
    <div className="mt-3 rounded-md border border-danger-border/60 bg-danger-bg p-3">
      <p className="text-sm leading-6 text-porcelain">Delete this card? This can't be undone.</p>
      <div aria-live="assertive">
        {errored ? (
          <p data-testid="card-delete-error" className="mt-2 text-sm leading-6 text-danger">
            We couldn't delete that card. Check your connection and try again.
          </p>
        ) : null}
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          data-testid="card-delete-confirm"
          onClick={onConfirm}
          disabled={deleting}
          className="flex-1 rounded-md bg-danger px-3 py-2 text-sm font-semibold text-night transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {deleting ? "Deleting…" : "Delete"}
        </button>
        <button
          type="button"
          ref={cancelRef}
          data-testid="card-delete-cancel"
          onClick={onCancel}
          disabled={deleting}
          className="flex-1 rounded-md border border-divider px-3 py-2 text-sm font-semibold text-mist transition-colors hover:text-porcelain"
        >
          Keep it
        </button>
      </div>
    </div>
  );
}
