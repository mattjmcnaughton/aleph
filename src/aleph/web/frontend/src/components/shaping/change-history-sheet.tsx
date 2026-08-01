// The **Change history** sheet (AL-331, TDD §8, PRD §5.5) — every Change on this
// path, in plain language, with the one **Undo** the client may offer.
//
// **A record, not a second edit surface.** It lists what happened, including
// undone Changes (undo is a status, never a delete), and it carries no payload:
// the operations already rendered as a card in the thread, and repeating them
// here would invite editing a history.
//
// **It belongs to the path, not to the thread.** That is why it hangs off the
// rail's header rather than living in the message list, and why "new
// conversation" leaves it untouched (TDD D3): an applied Change is real path
// structure, and clearing a conversation could not take it back even if it
// wanted to.
//
// **Undo's two rules are the server's; this sheet only explains them.**
//   - *LIFO* — only the newest live Change may be undone, because a Change
//     stores its inverse as absolute positions recorded against the path as it
//     stood (docs/api.md). An older row therefore says so plainly instead of
//     offering a button that would answer `409 not_latest`.
//   - *Engagement* — a Change whose content the learner has met is permanent
//     history. No client can derive that (it can change between this list
//     rendering and the tap), so the sheet never pre-disables for it: it taps,
//     takes the `409 engaged`, and says the window is closed (PRD §5.5 — say so
//     plainly rather than hiding the button).

import type { Change, ChangeKind } from "../../lib/shaping";
import type { ShapingRailState } from "./use-shaping-rail";

/** What each edit shape is called in the record (CONTEXT.md: Addition, Revision). */
const KIND_LABEL: Record<ChangeKind, string> = {
  add_lessons: "Added lessons",
  revise_lesson: "Revised a lesson",
};

export function ChangeHistorySheet({ shaping }: { shaping: ShapingRailState }) {
  return (
    <section
      data-testid="shaping-rail-history"
      aria-label="Change history"
      className="flex h-full min-h-0 flex-col"
    >
      <div className="flex items-center gap-2 border-b border-divider px-4 py-3">
        <span className="mr-auto text-sm font-semibold text-porcelain">Change history</span>
        <button
          type="button"
          data-testid="shaping-rail-history-close"
          onClick={shaping.closeHistory}
          aria-label="Close change history"
          title="Close change history"
          className="grid h-7 w-7 place-items-center rounded-md border border-divider text-mist transition-colors hover:border-iris/50 hover:text-porcelain"
        >
          <span aria-hidden="true" className="text-sm leading-none">
            ×
          </span>
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {shaping.changesError ? (
          <p
            data-testid="shaping-rail-history-error"
            role="alert"
            className="rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-porcelain"
          >
            We couldn't load this path's history. Try opening it again.
          </p>
        ) : shaping.changesLoading ? (
          <p data-testid="shaping-rail-history-loading" className="text-sm text-mist">
            Loading…
          </p>
        ) : shaping.changes.length === 0 ? (
          <p data-testid="shaping-rail-history-empty" className="text-sm leading-6 text-mist">
            Nothing has shaped this path yet. Applied changes show up here — including the ones you
            undo.
          </p>
        ) : (
          <ul className="space-y-3">
            {shaping.changes.map((change) => (
              <ChangeRow key={change.id} change={change} shaping={shaping} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function ChangeRow({ change, shaping }: { change: Change; shaping: ShapingRailState }) {
  const undone = change.status === "undone";
  const undoing = shaping.undoingChangeId === change.id;
  const undoable = change.id === shaping.undoableChangeId;
  const error = shaping.undoError?.changeId === change.id ? shaping.undoError : null;
  // `engaged` closes the window for good, so the row stops offering the tap and
  // says why — the one case where the button is replaced rather than disabled.
  const closed = error?.reason === "engaged";

  return (
    <li
      data-testid="shaping-rail-history-change"
      data-change-id={change.id}
      data-status={change.status}
      className="rounded-lg border border-divider bg-surface px-4 py-3"
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        {change.kinds.map((kind) => (
          <span
            key={kind}
            data-testid="shaping-rail-history-kind"
            className="font-mono text-[10px] uppercase tracking-kicker text-iris-300"
          >
            {KIND_LABEL[kind]}
          </span>
        ))}
        <span
          data-testid="shaping-rail-history-when"
          className="ml-auto font-mono text-[10px] uppercase tracking-kicker text-slate"
        >
          {formatApplied(change.applied_at)}
        </span>
      </div>

      <p className="mt-1.5 text-sm leading-6 text-porcelain">{change.summary}</p>

      <p
        data-testid="shaping-rail-history-status"
        className={`mt-1 text-xs ${undone ? "text-mist" : "text-teal"}`}
      >
        {undone ? "Undone" : "Applied"}
      </p>

      {error ? (
        <output
          data-testid="shaping-rail-history-undo-error"
          className="mt-2 block text-xs leading-5 text-mist"
        >
          {error.message}
        </output>
      ) : null}

      {undone || closed ? null : undoable ? (
        <button
          type="button"
          data-testid="shaping-rail-history-undo"
          onClick={() => shaping.undoChange(change.id)}
          disabled={undoing}
          className="mt-2 inline-flex items-center justify-center rounded-md border border-divider px-3 py-1.5 text-xs font-semibold text-porcelain transition-colors hover:border-iris/50 hover:bg-iris/10 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {undoing ? "Undoing…" : "Undo"}
        </button>
      ) : (
        // Not an error and not a refusal: nothing is wrong with this Change, it
        // is simply not the one on top of the stack.
        <p
          data-testid="shaping-rail-history-not-latest"
          className="mt-2 text-xs leading-5 text-mist"
        >
          Undo the newest change first.
        </p>
      )}
    </li>
  );
}

/**
 * The date a Change landed — the history's "when", in the learner's locale.
 *
 * The year appears only when it is not the current one. A path can be shaped
 * over years and this list has no other ordering cue, so a bare "Jan 15" on an
 * old Change is a date the learner cannot place; within this year, the year is
 * noise on a row that is already the newest thing on the list.
 */
function formatApplied(appliedAt: string): string {
  const when = new Date(appliedAt);
  if (Number.isNaN(when.getTime())) return "";
  const thisYear = when.getFullYear() === new Date().getFullYear();
  return when.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(thisYear ? {} : { year: "numeric" }),
  });
}
