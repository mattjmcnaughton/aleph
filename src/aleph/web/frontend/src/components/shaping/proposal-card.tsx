// The **Proposal** card (AL-331, Phase 2B TDD §8, PRD §5.4, mock Turn 3) — the
// tutor's edit plan, in the thread, with the tap that consents to it.
//
// **Apply is always an explicit tap.** Nothing here is reachable from
// conversation text: a learner who typed "yes do that" gets a fresh reply with a
// card on it, and this button is still the only thing that writes to the path
// (PRD §5.4 — never a silent rewrite). That is why **Not now** issues no request
// at all and persists nothing: declining must cost exactly as little as it
// promises.
//
// **The card renders a state; it does not compute one.** `proposalCardState` in
// `lib/shaping.ts` owns the §8 table (pending / applying / applied / stale /
// undone, plus the client-only dismissal), so the six branches below are a
// rendering of a decision made elsewhere and tested directly. The same goes for
// the cost line: it is derived from the operations, never from the agent's prose.
//
// **A stale Proposal is normal, not an error.** The learner chats, walks away,
// starts the lesson the Proposal named, comes back and taps — and the server
// answers a coded `409` whose `message` is written for a learner (docs/api.md).
// So the stale state shows *that* message and offers "ask again", which is the
// only thing that can actually help: re-applying a payload the path has outgrown
// never can.

import type { MessageProposal } from "../../lib/shaping";
import { conflictGroup, isAddLessons, proposalCostLine } from "../../lib/shaping";
import type { ProposalOperation } from "../../lib/tutor-stream";
import type { ProposalCardStatus } from "./use-shaping-rail";

export interface ProposalCardProps {
  /** The tutor message that made this Proposal — what Apply addresses. */
  messageId: string;
  proposal: MessageProposal;
  status: ProposalCardStatus;
  onApply: (messageId: string) => void;
  onDismiss: (messageId: string) => void;
  onAskAgain: (messageId: string) => void;
  onViewInPath: () => void;
}

/** Copy for a `superseded` proposal — the one stale state with no `409` behind it. */
const SUPERSEDED_COPY = "Another change landed first, so this no longer fits your path.";

const UNDONE_COPY = "Undone — your path is back where it was.";

export function ProposalCard({
  messageId,
  proposal,
  status,
  onApply,
  onDismiss,
  onAskAgain,
  onViewInPath,
}: ProposalCardProps) {
  const { state } = status;
  // A refusal that left the card **pending** is the retryable one
  // (`target_generating`) or a plain failure: say so above the buttons and leave
  // Apply exactly where it is, because the same tap is what fixes it.
  const pendingNotice = state === "pending" && status.message !== null ? status.message : null;

  return (
    <div
      data-testid="shaping-rail-proposal"
      // `data-resolution` is the *server's* derived answer and stays untouched;
      // `data-state` is what the learner is looking at, which also folds in the
      // in-flight apply and the client-only dismissal.
      data-resolution={proposal.resolution}
      data-state={state}
      data-operations={proposal.operations.length}
      className="mt-3 rounded-lg border border-iris/50 bg-iris/5 px-4 py-3"
    >
      <p className="font-mono text-[11px] font-medium uppercase tracking-kicker text-iris-300">
        Proposal
      </p>
      {/* The agent's own plain-language statement of the edit (TDD §5.1). */}
      <p className="mt-1.5 text-sm leading-6 text-porcelain">{proposal.summary}</p>

      {/* The scale, derived from the operations — the part a learner can check. */}
      <p
        data-testid="shaping-rail-proposal-cost"
        className="mt-1 font-mono text-[11px] uppercase tracking-kicker text-iris-400"
      >
        {proposalCostLine(proposal)}
      </p>

      <ul className="mt-3 space-y-3">
        {proposal.operations.map((operation) => (
          <OperationRow key={operationKey(operation)} operation={operation} />
        ))}
      </ul>

      <div className="mt-3 border-t border-iris/20 pt-3">
        {pendingNotice ? (
          <output
            data-testid="shaping-rail-proposal-notice"
            className="mb-2 block text-xs leading-5 text-mist"
          >
            {pendingNotice}
          </output>
        ) : null}

        {state === "pending" || state === "applying" ? (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="shaping-rail-proposal-apply"
              onClick={() => onApply(messageId)}
              disabled={state === "applying"}
              className="inline-flex items-center justify-center rounded-md bg-iris px-4 py-2 text-sm font-semibold text-night transition-colors hover:bg-iris-400 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {state === "applying" ? "Applying…" : "Apply"}
            </button>
            <button
              type="button"
              data-testid="shaping-rail-proposal-dismiss"
              onClick={() => onDismiss(messageId)}
              disabled={state === "applying"}
              className="inline-flex items-center justify-center rounded-md border border-divider px-3 py-2 text-sm text-mist transition-colors hover:border-iris/50 hover:text-porcelain disabled:cursor-not-allowed disabled:opacity-60"
            >
              Not now
            </button>
          </div>
        ) : null}

        {state === "applied" ? (
          <div className="flex flex-wrap items-center gap-3">
            <p data-testid="shaping-rail-proposal-applied" className="text-sm text-teal">
              Applied to your path.
            </p>
            <button
              type="button"
              data-testid="shaping-rail-proposal-view"
              onClick={onViewInPath}
              className="inline-flex items-center justify-center rounded-md border border-teal/60 px-3 py-2 text-xs font-semibold text-porcelain transition-colors hover:border-teal hover:bg-teal/10"
            >
              View in path
            </button>
          </div>
        ) : null}

        {state === "stale" || state === "undone" ? (
          <div>
            {/* An `<output>` and not an alert: the learner tapped, this is the
                answer to that tap, and nothing about a Proposal going stale is
                an emergency worth interrupting them for. */}
            <output
              data-testid={
                state === "undone" ? "shaping-rail-proposal-undone" : "shaping-rail-proposal-stale"
              }
              className="block text-sm leading-6 text-mist"
            >
              {staleCopy(status, state)}
            </output>
            <button
              type="button"
              data-testid="shaping-rail-proposal-ask-again"
              onClick={() => onAskAgain(messageId)}
              className="mt-2 inline-flex items-center justify-center rounded-md border border-iris/60 px-3 py-2 text-xs font-semibold text-porcelain transition-colors hover:border-iris hover:bg-iris/10"
            >
              Ask again
            </button>
          </div>
        ) : null}

        {state === "dismissed" ? (
          <p data-testid="shaping-rail-proposal-dismissed" className="text-sm text-mist">
            Not applied.
          </p>
        ) : null}
      </div>
    </div>
  );
}

/**
 * What the card says about a Proposal it can no longer apply.
 *
 * The server's own `message` wins wherever there is one: it names *which* rule
 * fired ("this lesson has been started since"), which a client cannot, and it is
 * already written for a learner. The two fallbacks cover the states no `409`
 * produced — a `superseded` resolution read off the thread, and an `undone` one.
 */
function staleCopy(status: ProposalCardStatus, state: "stale" | "undone"): string {
  if (state === "undone") return status.message ?? UNDONE_COPY;
  if (status.reason !== null && conflictGroup(status.reason) === "ask_again") {
    return status.message ?? SUPERSEDED_COPY;
  }
  return SUPERSEDED_COPY;
}

/**
 * A React key for one operation. The payload carries no operation ids and its
 * order *is* the apply order, so the key is drawn from what the operation says:
 * the slot and titles it names, or the lesson it revises. Content, not index —
 * an index key would let two cards' rows swap identity on a re-render.
 */
function operationKey(operation: ProposalOperation): string {
  return isAddLessons(operation)
    ? `add-${operation.insert_at_position}-${operation.lessons.map((l) => l.title).join("|")}`
    : `revise-${operation.lesson_id}`;
}

/**
 * One operation, grouped with its rationale (PRD §5.4). Two shapes and no more
 * (D1), discriminated structurally exactly as the wire is.
 */
function OperationRow({ operation }: { operation: ProposalOperation }) {
  if (isAddLessons(operation)) {
    const count = operation.lessons.length;
    return (
      <li data-testid="shaping-rail-proposal-operation" data-kind="add_lessons">
        <p className="text-sm font-semibold text-porcelain">
          {count === 1 ? "Adds 1 lesson" : `Adds ${count} lessons`}
          {operation.new_unit ? ` in a new unit — ${operation.new_unit.title}` : null}
        </p>
        <ul className="mt-1.5 space-y-1">
          {operation.lessons.map((lesson) => (
            <li
              key={lesson.title}
              data-testid="shaping-rail-proposal-lesson"
              className="flex items-baseline gap-2 text-sm leading-6 text-porcelain"
            >
              <span
                aria-hidden="true"
                className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-iris"
              />
              <span className="min-w-0">{lesson.title}</span>
            </li>
          ))}
        </ul>
        <p className="mt-1.5 text-xs leading-5 text-mist">{operation.rationale}</p>
      </li>
    );
  }
  return (
    <li data-testid="shaping-rail-proposal-operation" data-kind="revise_lesson">
      <p className="text-sm font-semibold text-porcelain">
        Revises a lesson you haven't started
        {operation.new_title ? ` — ${operation.new_title}` : null}
      </p>
      {/* The instruction is what the revision *does*; the rationale is why. Both
          are shown, because consenting to a re-teach without seeing the
          instruction would be consenting to a blank cheque. */}
      <p className="mt-1.5 text-sm leading-6 text-porcelain">“{operation.instruction}”</p>
      <p className="mt-1.5 text-xs leading-5 text-mist">{operation.rationale}</p>
    </li>
  );
}
