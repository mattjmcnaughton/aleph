// The shaping rail (AL-330, Phase 2B TDD §8/D14, mock Turn 3) — the tutor's
// surface on the **path route**, and the floating mark that opens it.
//
// **The rail tree's third mount.** D14 extends Phase 2's D12 unchanged: one tree,
// two CSS presentations. Everything below renders once. Whether it reads as a
// bottom sheet over the path or as a docked third column is decided by the `lg:`
// utilities on the `<aside>` `workspace.tsx` mounts it in — never by JS. There is
// no `matchMedia` here, no viewport state, and no branch that renders different
// markup at different widths; open/closed is the only state, and it is shared
// across both presentations by construction.
//
// **Naming.** This surface is the **shaping rail** (`shaping-rail-*` testids).
// The in-lesson tutor surface keeps `tutor-rail`, the units/lessons list inside
// this very view keeps `path-rail`, and the desktop left column keeps `Sidebar`
// — four things, four names (CONTEXT.md).
//
// **Iris is the tutor** (PRD §5.10), on this surface too: teal stays the path's
// accent — which is what will let AL-331's iris ghost rows read as *proposed*
// against the path rail's teal *real* ones.

import { AlephGlyph } from "../aleph-logo";
import { Markdown } from "../markdown";
import { TutorModelPicker } from "../model-picker";
import type { MessageProposal, ShapingMessage } from "../../lib/shaping";
import {
  SHAPING_MESSAGE_MAX_LENGTH,
  SHAPING_SUGGESTIONS,
  type ShapingRailState,
} from "./use-shaping-rail";

/** The aleph mark in the tutor's iris — one source for that square, as ever. */
function ShapingGlyph({ size = "xs" }: { size?: "xs" | "2xs" }) {
  return <AlephGlyph size={size} accent="iris" />;
}

/**
 * The entry point (PRD §5.1): a floating mark over the path view, the same
 * gesture the lesson route's mark is. Rendered exactly when the rail is closed,
 * at every width — at `lg` that makes it the way back from the header's
 * collapse, which is the same gesture in reverse.
 *
 * It is rendered *at all* only on a `ready` path with the `shaping` flag on
 * (`showMark`), and never as a disabled affordance: a path with no structure has
 * nothing to shape, and dangling the control would promise otherwise.
 */
export function ShapingMark({ shaping }: { shaping: ShapingRailState }) {
  if (!shaping.showMark) return null;
  return (
    <button
      type="button"
      data-testid="shaping-rail-mark"
      onClick={shaping.openRail}
      className="fixed bottom-5 right-5 z-30 inline-flex items-center gap-2 rounded-full border border-iris/60 bg-surface px-4 py-3 text-sm font-semibold text-porcelain shadow-glow-iris transition-colors hover:border-iris hover:bg-elevated"
    >
      <ShapingGlyph size="2xs" />
      Shape your path
    </button>
  );
}

/**
 * The rail itself. Rendered into `Workspace`'s rail slot, which owns the
 * sheet-vs-column classes; this component owns the contents and nothing about
 * where they sit.
 */
export function ShapingRail({ shaping }: { shaping: ShapingRailState }) {
  const streaming = shaping.status === "streaming";
  const empty = shaping.messages.length === 0 && !streaming && shaping.status !== "failed";

  return (
    <section
      data-testid="shaping-rail"
      aria-label="Shape your path"
      className="flex h-full min-h-0 flex-col"
    >
      <RailHeader shaping={shaping} />

      <div
        data-testid="shaping-rail-messages"
        // Replies arrive progressively and nothing moves focus to them, so the
        // thread announces itself; `polite` because a stream that interrupted
        // the learner on every delta would be unusable.
        aria-live="polite"
        className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4"
      >
        {shaping.clearError ? (
          <p
            data-testid="shaping-rail-clear-error"
            role="alert"
            className="rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-porcelain"
          >
            {shaping.clearError}
          </p>
        ) : null}

        {empty ? <EmptyState /> : null}

        {shaping.messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}

        {streaming ? (
          <div className="flex gap-2.5">
            <ShapingGlyph size="2xs" />
            <Markdown
              testid="shaping-rail-streaming"
              className="min-w-0 flex-1 text-sm [&_p]:text-sm [&_p]:leading-6"
            >
              {shaping.streamingText}
            </Markdown>
          </div>
        ) : null}

        {shaping.status === "failed" && shaping.errorMessage ? (
          <div
            data-testid="shaping-rail-error"
            role="alert"
            className="rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-porcelain"
          >
            <p className="font-semibold text-danger">{shaping.errorMessage}</p>
            {/* Literal, not reassurance: a failed reply puts the question back
                in the composer below (the same restore stop does). */}
            <p className="mt-1 text-mist">Your question is still here.</p>
            <button
              type="button"
              data-testid="shaping-rail-retry"
              onClick={shaping.retry}
              className="mt-3 inline-flex items-center justify-center rounded-md border border-iris/60 px-3 py-2 text-xs font-semibold text-porcelain transition-colors hover:border-iris hover:bg-iris/10"
            >
              Try again
            </button>
          </div>
        ) : null}
      </div>

      <Composer shaping={shaping} />
    </section>
  );
}

// --- Header: change history, new conversation, collapse, admin picker --------

function RailHeader({ shaping }: { shaping: ShapingRailState }) {
  return (
    <div
      data-testid="shaping-rail-header"
      className="flex flex-wrap items-center gap-2 border-b border-divider px-4 py-3"
    >
      <ShapingGlyph />
      <span className="mr-auto text-sm font-semibold text-porcelain">Shape your path</span>

      <TutorModelPicker
        isAdmin={shaping.isAdmin}
        allowlist={shaping.modelAllowlist}
        value={shaping.model}
        onChange={shaping.setModel}
        testid="shaping-rail-model-picker"
        label="Shaper model (admin)"
      />

      {/* The **change history** is a record of the *path*, not of this thread —
          it survives a cleared conversation (TDD D3), which is why it is a
          header control rather than something inside the message list. AL-330
          owns this button's place in the header and nothing more: AL-331 hangs
          the read-only sheet off it, and brings its own open state along with
          the thing that state would open. */}
      <button
        type="button"
        data-testid="shaping-rail-change-history"
        title="Change history"
        className="rounded-md border border-divider px-2.5 py-1.5 text-xs text-mist transition-colors hover:border-iris/50 hover:text-porcelain"
      >
        Changes
      </button>

      {shaping.confirmingNew ? (
        // Confirm in place, like the switcher's delete: clearing a thread is
        // destructive and not undoable, and it must never sit under one tap.
        <span className="flex items-center gap-2">
          <span className="text-xs text-mist">Clear this conversation?</span>
          <button
            type="button"
            data-testid="shaping-rail-new-conversation-confirm"
            onClick={shaping.confirmNewConversation}
            className="rounded-md border border-danger-border/60 bg-danger-bg px-2.5 py-1.5 text-xs font-semibold text-danger transition-colors hover:border-danger"
          >
            Clear
          </button>
          <button
            type="button"
            data-testid="shaping-rail-new-conversation-cancel"
            onClick={shaping.cancelNewConversation}
            className="rounded-md border border-divider px-2.5 py-1.5 text-xs text-mist transition-colors hover:text-porcelain"
          >
            Keep
          </button>
        </span>
      ) : (
        <button
          type="button"
          data-testid="shaping-rail-new-conversation"
          onClick={shaping.askNewConversation}
          title="New conversation"
          className="rounded-md border border-divider px-2.5 py-1.5 text-xs text-mist transition-colors hover:border-iris/50 hover:text-porcelain"
        >
          New conversation
        </button>
      )}

      <button
        type="button"
        data-testid="shaping-rail-collapse"
        onClick={shaping.closeRail}
        aria-label="Close shaping"
        title="Close shaping"
        className="grid h-7 w-7 place-items-center rounded-md border border-divider text-mist transition-colors hover:border-iris/50 hover:text-porcelain"
      >
        <span aria-hidden="true" className="text-sm leading-none">
          ×
        </span>
      </button>
    </div>
  );
}

// --- Messages ----------------------------------------------------------------

function MessageBubble({ message }: { message: ShapingMessage }) {
  const isTutor = message.role === "tutor";
  return (
    <div
      data-testid="shaping-rail-message"
      data-role={message.role}
      // A produced Proposal rides the cached message, so it survives a collapse,
      // a reopen, and a page revisit — with the resolution the server derived.
      data-proposal={message.proposal ? "true" : undefined}
      className={isTutor ? "flex gap-2.5" : "flex justify-end"}
    >
      {isTutor ? <ShapingGlyph size="2xs" /> : null}
      {isTutor ? (
        <div className="min-w-0 flex-1">
          {/* Generated prose goes through the one renderer, always (the security
              boundary — no second pipeline, no `dangerouslySetInnerHTML`). */}
          <Markdown className="text-sm [&_p]:text-sm [&_p]:leading-6">{message.content}</Markdown>
          {message.proposal ? <ProposalSlot proposal={message.proposal} /> : null}
        </div>
      ) : (
        <p className="max-w-[85%] whitespace-pre-wrap rounded-lg border border-divider bg-surface px-3 py-2 text-sm leading-6 text-porcelain">
          {message.content}
        </p>
      )}
    </div>
  );
}

/**
 * **The proposal card's mount point — deliberately not the card.** AL-331 owns
 * the interior (operations grouped with rationale and cost, the pending /
 * applying / applied / stale / undone states, Apply and Not now, ghost rows,
 * undo). What AL-330 owes it is this: the payload arrives on the stream, lands
 * on the message, and has a place to render — so the seam is proven end to end
 * before the card exists.
 *
 * It shows the payload's own `summary` and nothing computed: the summary is the
 * agent's plain-language statement of what the operations do (TDD §5.1), so
 * showing it is honest even without the card, and there is no second, derived
 * description here for AL-331 to have to reconcile with.
 */
function ProposalSlot({ proposal }: { proposal: MessageProposal }) {
  return (
    <div
      data-testid="shaping-rail-proposal"
      data-resolution={proposal.resolution}
      data-operations={proposal.operations.length}
      className="mt-3 rounded-lg border border-iris/50 bg-iris/5 px-4 py-3"
    >
      <p className="font-mono text-[11px] font-medium uppercase tracking-kicker text-iris-300">
        Proposal
      </p>
      <p className="mt-1.5 text-sm leading-6 text-porcelain">{proposal.summary}</p>
    </div>
  );
}

/**
 * The empty state names what shaping can do **and what it can't** (PRD §5.1).
 * The second half is not hedging: the vocabulary is closed (D1), so a learner
 * who asks to remove or reorder gets a **declined edit** — and being told the
 * boundary before asking is the difference between a rule and a rebuff.
 */
function EmptyState() {
  return (
    <div
      data-testid="shaping-rail-empty"
      className="rounded-lg border border-divider bg-surface p-4"
    >
      <p className="text-sm font-semibold text-porcelain">I can change this path with you.</p>
      <p className="mt-2 text-sm leading-6 text-mist">
        I can <span className="text-porcelain">add lessons</span> and{" "}
        <span className="text-porcelain">revise ones you haven't started</span> — and nothing
        happens until you tap Apply.
      </p>
      <p className="mt-2 text-sm leading-6 text-mist">
        I can't remove or reorder lessons, change work you've already done, or touch your progress.
      </p>
    </div>
  );
}

// --- Composer: suggestions, chip, textarea, send/stop -------------------------

function Composer({ shaping }: { shaping: ShapingRailState }) {
  const streaming = shaping.status === "streaming";

  return (
    <div className="border-t border-divider px-4 py-3">
      {/* Suggestions sit with the composer so they are offered in the empty
          state and again after a reply settles (PRD §5.3) — never mid-stream,
          when tapping one could only queue a send the server would reject. */}
      {streaming ? null : (
        <div className="mb-3 flex flex-wrap gap-2">
          {SHAPING_SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion.label}
              type="button"
              data-testid="shaping-rail-suggestion"
              onClick={() => shaping.useSuggestion(suggestion)}
              className="rounded-full border border-divider px-3 py-1.5 text-xs text-mist transition-colors hover:border-iris/50 hover:text-porcelain"
            >
              {suggestion.label}
            </button>
          ))}
        </div>
      )}

      {/* The scope statement, told once, where the ask is typed. On this surface
          it is also a warning label: this conversation can change the path. */}
      <p
        data-testid="shaping-rail-context-chip"
        className="mb-2 inline-flex items-center gap-2 rounded-full border border-divider px-3 py-1 text-xs text-mist"
      >
        <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-iris" />
        Shaping · <span className="text-porcelain">{shaping.topic}</span>
      </p>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          shaping.send(shaping.draft, "typed");
        }}
      >
        <textarea
          data-testid="shaping-rail-input"
          aria-label="Ask to change this path"
          value={shaping.draft}
          onChange={(event) => shaping.setDraft(event.target.value)}
          disabled={streaming}
          rows={2}
          maxLength={SHAPING_MESSAGE_MAX_LENGTH}
          placeholder="Ask to add or revise lessons…"
          className="w-full resize-none rounded-md border border-divider bg-surface px-3 py-2 text-sm text-porcelain placeholder:text-slate focus:border-iris focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
        />
        <div className="mt-2 flex justify-end">
          {streaming ? (
            // Stop is the *only* control in flight: it aborts the request, and
            // the question comes back to the composer for editing (TDD §5.6).
            <button
              type="button"
              data-testid="shaping-rail-stop"
              onClick={shaping.stop}
              className="inline-flex items-center justify-center rounded-md border border-divider px-4 py-2 text-sm font-semibold text-porcelain transition-colors hover:border-iris/50"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              data-testid="shaping-rail-send"
              disabled={shaping.draft.trim() === ""}
              className="inline-flex items-center justify-center rounded-md bg-iris px-4 py-2 text-sm font-semibold text-night transition-colors hover:bg-iris-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Ask
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
