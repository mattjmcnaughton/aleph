// The rail (AL-230, TDD §8/D12, mock Turn 1 + 2a) — the tutor's surface on the
// lesson route, and the floating mark that opens it.
//
// **One tree, two CSS presentations.** Everything below renders once. Whether it
// reads as a bottom sheet over the lesson or as a docked third column is decided
// by the `lg:` utilities on the `<aside>` `workspace.tsx` mounts it in — never by
// JS. There is no `matchMedia` here, no viewport state, and no branch that
// renders different markup at different widths; open/closed is the only state,
// and it is shared across both presentations by construction.
//
// **Naming.** This surface is the **rail** (`tutor-rail-*` testids). The
// units/lessons list inside the path view keeps `path-rail`, and the desktop
// left column keeps `Sidebar`/`Outline` — three different things, three names.
//
// **Iris is the tutor** (PRD §5.10): teal stays the path's accent, iris marks
// every tutor-owned affordance here.

import { AlephGlyph } from "../aleph-logo";
import { handleComposerKeyDown } from "../../lib/composer-keys";
import { Markdown } from "../markdown";
import { TutorModelPicker } from "../model-picker";
import { useThreadScroll } from "../use-thread-scroll";
import type { ConversationMessage } from "../../lib/tutor";
import { TutorCheckCard } from "./tutor-check-card";
import { TUTOR_MESSAGE_MAX_LENGTH, TUTOR_SUGGESTIONS, type TutorRailState } from "./use-tutor-rail";

/**
 * The aleph mark in the tutor's iris, not the brand's teal — the same
 * `AlephGlyph` every other surface renders, on its iris accent. There is one
 * source for that square (`aleph-logo.tsx`) and this is not a second one.
 */
function TutorGlyph({ size = "xs" }: { size?: "xs" | "2xs" }) {
  return <AlephGlyph size={size} accent="iris" />;
}

/**
 * The phone entry point (PRD §5.1): a floating mark over the lesson, chosen over
 * a bottom tab bar (persistent chrome, a navigation level the app doesn't have)
 * and over a purely inline card (it scrolls away). It is rendered exactly when
 * the rail is closed, at every width — at `lg` that makes it the way back from
 * the header's collapse, which is the same gesture in reverse.
 */
export function TutorMark({ tutor }: { tutor: TutorRailState }) {
  if (!tutor.showMark) return null;
  return (
    <button
      type="button"
      data-testid="tutor-rail-mark"
      onClick={tutor.openRail}
      className="fixed bottom-[max(1.25rem,env(safe-area-inset-bottom))] right-5 z-30 inline-flex items-center gap-2 rounded-full border border-iris/60 bg-surface px-4 py-3 text-sm font-semibold text-porcelain shadow-glow-iris transition-colors hover:border-iris hover:bg-elevated"
    >
      <TutorGlyph size="2xs" />
      Tutor
    </button>
  );
}

/**
 * The rail itself. Rendered into `Workspace`'s `tutorRail` slot, which owns the
 * sheet-vs-column classes; this component owns the contents and nothing about
 * where they sit.
 */
export function TutorRail({ tutor }: { tutor: TutorRailState }) {
  // A turn the rail is rendering itself: the question, the wait, the deltas.
  // Read off the question rather than off `status`, which tracks the same window
  // today only because every exit from a stream clears both — the thing the
  // empty state and the scroll care about is whether a turn is *on screen*.
  const live = tutor.pendingQuestion !== null;
  const empty = tutor.messages.length === 0 && !live && tutor.status !== "failed";
  // The thread does not scroll itself, and everything a turn adds is added to
  // the bottom of it — so on a thread taller than the rail, a question shown
  // the instant it is sent would still be shown off screen.
  const thread = useThreadScroll(live);

  return (
    <section data-testid="tutor-rail" aria-label="Tutor" className="flex min-h-0 flex-1 flex-col">
      <RailHeader tutor={tutor} />

      <div
        ref={thread.ref}
        onScroll={thread.onScroll}
        data-testid="tutor-rail-messages"
        // Replies arrive progressively and nothing moves focus to them, so the
        // thread announces itself; `polite` because a stream that interrupted
        // the learner mid-sentence on every delta would be unusable.
        aria-live="polite"
        className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4"
      >
        {/* New conversation failed. Its own notice rather than the reply's error
            card below: nothing was sent, so there is no question to try again —
            the only honest offer is to clear it again. */}
        {tutor.clearError ? (
          <p
            data-testid="tutor-rail-clear-error"
            role="alert"
            className="rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-porcelain"
          >
            {tutor.clearError}
          </p>
        ) : null}

        {empty ? <EmptyState lessonTitle={tutor.lessonTitle} /> : null}

        {tutor.messages.map((message) => (
          <MessageBubble key={message.id} message={message} tutor={tutor} />
        ))}

        {/* The live turn, in the order it happens. The question is the same
            bubble the settled message renders, so the handover to the cached
            thread changes nothing on screen. */}
        {tutor.pendingQuestion !== null ? (
          <LearnerBubble testid="tutor-rail-pending" content={tutor.pendingQuestion} />
        ) : null}

        {tutor.thinking ? <Thinking /> : null}

        {tutor.streamingText !== "" ? (
          <div className="flex gap-2.5">
            <TutorGlyph size="2xs" />
            <Markdown
              testid="tutor-rail-streaming"
              className="min-w-0 flex-1 text-sm [&_p]:text-sm [&_p]:leading-6"
            >
              {tutor.streamingText}
            </Markdown>
          </div>
        ) : null}

        {tutor.status === "failed" && tutor.errorMessage ? (
          <div
            data-testid="tutor-rail-error"
            role="alert"
            className="rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-porcelain"
          >
            <p className="font-semibold text-danger">{tutor.errorMessage}</p>
            {/* Literal, not reassurance: a failed reply puts the question back
                in the composer below (the same restore stop does), so the
                learner can edit it or send it again by hand. */}
            <p className="mt-1 text-mist">Your question is still here.</p>
            <button
              type="button"
              data-testid="tutor-rail-retry"
              onClick={tutor.retry}
              className="mt-3 inline-flex items-center justify-center rounded-md border border-iris/60 px-3 py-2 text-xs font-semibold text-porcelain transition-colors hover:border-iris hover:bg-iris/10"
            >
              Try again
            </button>
          </div>
        ) : null}
      </div>

      <Composer tutor={tutor} />
    </section>
  );
}

// --- Header: handle, title + scope, new conversation, close, admin picker ----

function RailHeader({ tutor }: { tutor: TutorRailState }) {
  return (
    <div data-testid="tutor-rail-header" className="border-b border-divider px-4 pb-3 pt-1 lg:pt-3">
      {/* The sheet's grab handle (phone only): the visual cue that this is a
          sheet over the lesson, and a tap on it dismisses. The pill is 4px
          tall, so the button around it is the tap target, not the pill.
          Hidden from AT and skipped by the tab order on purpose — the × below
          is the accessible close, and this is a second way to reach the same
          action, not a second control to announce. */}
      <button
        type="button"
        data-testid="tutor-rail-handle"
        aria-hidden="true"
        tabIndex={-1}
        onClick={tutor.closeRail}
        className="mx-auto mb-1 block px-6 py-2 lg:hidden"
      >
        <span className="block h-1 w-9 rounded-full bg-divider" />
      </button>

      <div className="flex flex-wrap items-center gap-2">
        <TutorGlyph />
        <span className="mr-auto min-w-0 flex-1">
          <span className="block text-sm font-semibold leading-5 text-porcelain">Tutor</span>
          {/* The scope statement, told once, as the surface's subtitle: what the
              tutor can see is the lesson, and the lesson is named where the
              surface is named. */}
          <span
            data-testid="tutor-rail-context-chip"
            title={tutor.lessonTitle}
            className="flex items-start gap-1.5 text-xs leading-4 text-mist"
          >
            <span
              aria-hidden="true"
              className="mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full bg-iris"
            />
            {/* Two lines before clipping: the scope statement is the point of
                the line (PRD §5.2), and one line clipped the PRD's own example
                title. `title` carries the whole thing for a hover. */}
            <span className="line-clamp-2">
              Reading · <span className="text-porcelain">{tutor.lessonTitle}</span>
            </span>
          </span>
        </span>

        {tutor.confirmingNew ? null : (
          <button
            type="button"
            data-testid="tutor-rail-new-conversation"
            onClick={tutor.askNewConversation}
            title="New conversation"
            className="shrink-0 rounded-md border border-divider px-2.5 py-1.5 text-xs text-mist transition-colors hover:border-iris/50 hover:text-porcelain"
          >
            New conversation
          </button>
        )}

        <button
          type="button"
          data-testid="tutor-rail-collapse"
          onClick={tutor.closeRail}
          aria-label="Close the tutor"
          title="Close the tutor"
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-divider text-mist transition-colors hover:border-iris/50 hover:text-porcelain"
        >
          <span aria-hidden="true" className="text-sm leading-none">
            ×
          </span>
        </button>

        {tutor.confirmingNew ? (
          // Confirm in place, like the switcher's delete: clearing a thread is
          // destructive and not undoable, and it must never sit under one tap.
          // It takes its own row (`basis-full`) under the title row, so the ×
          // keeps its corner and the tab order runs top to bottom.
          <span className="flex basis-full items-center justify-end gap-2">
            <span className="text-xs text-mist">Clear this conversation?</span>
            <button
              type="button"
              data-testid="tutor-rail-new-conversation-confirm"
              onClick={tutor.confirmNewConversation}
              className="rounded-md border border-danger-border/60 bg-danger-bg px-2.5 py-1.5 text-xs font-semibold text-danger transition-colors hover:border-danger"
            >
              Clear
            </button>
            <button
              type="button"
              data-testid="tutor-rail-new-conversation-cancel"
              onClick={tutor.cancelNewConversation}
              className="rounded-md border border-divider px-2.5 py-1.5 text-xs text-mist transition-colors hover:text-porcelain"
            >
              Keep
            </button>
          </span>
        ) : null}

        {/* The admin picker gets a row of its own rather than a share of the
            title row: on a phone it is the widest thing in the header, and
            letting it wrap the row is what pushed the × out of its corner.
            `empty:hidden` because the picker renders nothing for a non-admin,
            and an empty wrapper would still cost the row gap. */}
        <span className="basis-full empty:hidden">
          <TutorModelPicker
            isAdmin={tutor.isAdmin}
            allowlist={tutor.modelAllowlist}
            value={tutor.model}
            onChange={tutor.setModel}
          />
        </span>
      </div>
    </div>
  );
}

// --- Messages ----------------------------------------------------------------

/**
 * A learner's turn in the thread. One component for two callers on purpose: the
 * cached message and the live echo of a question still being answered are the
 * same bubble, so the moment one replaces the other is invisible. Only the
 * testid differs — `tutor-rail-message` is a message the conversation *has*, and
 * an echo is not one until the turn settles.
 */
function LearnerBubble({ testid, content }: { testid: string; content: string }) {
  return (
    <div data-testid={testid} data-role="learner" className="flex justify-end">
      <p className="max-w-[85%] whitespace-pre-wrap rounded-lg border border-divider bg-surface px-3 py-2 text-sm leading-6 text-porcelain">
        {content}
      </p>
    </div>
  );
}

/**
 * The wait, said out loud (PRD §5.6). Between the send and the first token sit
 * admission, the reply semaphore and the provider's own time to first token —
 * seconds, on a surface whose only other in-flight signal is a disabled
 * textarea. It is shown from the send until the first delta and replaced by the
 * reply itself, so a running turn is never a silent one.
 *
 * The dots are decoration and marked as such; the label is what the messages
 * list's `aria-live="polite"` announces, and what survives `motion-reduce`.
 */
function Thinking() {
  return (
    <div data-testid="tutor-rail-thinking" className="flex items-center gap-2.5">
      <TutorGlyph size="2xs" />
      <span aria-hidden="true" className="flex items-center gap-1">
        {THINKING_DOT_DELAYS.map((delay) => (
          <span
            key={delay}
            className={`h-1.5 w-1.5 animate-thinking rounded-full bg-iris motion-reduce:animate-none ${delay}`}
          />
        ))}
      </span>
      <span className="text-xs text-mist">Thinking…</span>
    </div>
  );
}

/**
 * Written out rather than computed: Tailwind scans this file for literal class
 * strings, so an interpolated delay would generate no CSS at all.
 */
const THINKING_DOT_DELAYS = [
  "[animation-delay:0ms]",
  "[animation-delay:160ms]",
  "[animation-delay:320ms]",
] as const;

function MessageBubble({
  message,
  tutor,
}: { message: ConversationMessage; tutor: TutorRailState }) {
  if (message.role !== "tutor") {
    return <LearnerBubble testid="tutor-rail-message" content={message.content} />;
  }
  return (
    <div
      data-testid="tutor-rail-message"
      data-role={message.role}
      // A posed Tutor check rides the cached message, so it survives a collapse,
      // a reopen, and a page revisit — and so does the answer written onto it.
      data-tutor-check={message.tutor_check ? "true" : undefined}
      className="flex gap-2.5"
    >
      <TutorGlyph size="2xs" />
      <div className="min-w-0 flex-1">
        {/* Generated prose goes through the one renderer, always (the security
            boundary — no second pipeline, no `dangerouslySetInnerHTML`). */}
        <Markdown className="text-sm [&_p]:text-sm [&_p]:leading-6">{message.content}</Markdown>
        {/* The card is part of the reply, under it — a Tutor check is posed
         *in* the conversation (PRD §5.5), not in a surface beside it. */}
        {message.tutor_check ? (
          <TutorCheckCard
            messageId={message.id}
            check={message.tutor_check}
            onAnswer={tutor.answerCheck}
            onFollowUp={(content) => tutor.send(content, "suggestion")}
            sending={tutor.status === "streaming"}
          />
        ) : null}
      </div>
    </div>
  );
}

/**
 * The empty state names what the tutor can see (PRD §5.1/§5.2) rather than
 * presenting a bare composer — the scope statement is the whole point, because
 * a learner who doesn't know the tutor has the passage won't ask about it.
 */
function EmptyState({ lessonTitle }: { lessonTitle: string }) {
  return (
    <div data-testid="tutor-rail-empty" className="rounded-lg border border-divider bg-surface p-4">
      <p className="text-sm font-semibold text-porcelain">I can see this lesson.</p>
      <p className="mt-2 text-sm leading-6 text-mist">
        The Read passage and Quick check for <span className="text-porcelain">{lessonTitle}</span>,
        your answer to it once you've made one, and the names of every unit and lesson on this path.
        Ask me anything about it.
      </p>
    </div>
  );
}

// --- Composer: suggestions, textarea, send/stop -------------------------------

function Composer({ tutor }: { tutor: TutorRailState }) {
  const streaming = tutor.status === "streaming";

  return (
    // `pb-[max(...)]`: the sheet ends at the physical bottom of a phone, so the
    // composer pads by the home-indicator inset where there is one (and by its
    // ordinary 12px where there isn't). Needs `viewport-fit=cover` in index.html.
    <div className="border-t border-divider px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
      {/* Suggestions sit with the composer so they are offered in the empty
          state and again after a reply settles (PRD §5.3) — never mid-stream,
          when tapping one could only queue a send the server would 409. One
          row that scrolls sideways, not a wrap: on a phone two rows of chips
          were a quarter of the sheet, on every reply. The docked column has
          vertical room and a mouse instead of a thumb, so there it wraps. */}
      {streaming ? null : (
        <div className="-mx-4 mb-3 flex gap-2 overflow-x-auto px-4 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden lg:flex-wrap lg:overflow-visible">
          {TUTOR_SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              data-testid="tutor-rail-suggestion"
              onClick={() => tutor.send(suggestion, "suggestion")}
              className="shrink-0 whitespace-nowrap rounded-full border border-divider px-3 py-1.5 text-xs text-mist transition-colors hover:border-iris/50 hover:text-porcelain"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      <form
        className="flex items-stretch gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          tutor.send(tutor.draft, "typed");
        }}
      >
        <textarea
          data-testid="tutor-rail-input"
          aria-label="Ask about this lesson"
          value={tutor.draft}
          onChange={(event) => tutor.setDraft(event.target.value)}
          onKeyDown={(event) =>
            handleComposerKeyDown(event, () => tutor.send(tutor.draft, "typed"))
          }
          disabled={streaming}
          rows={1}
          maxLength={TUTOR_MESSAGE_MAX_LENGTH}
          placeholder="Ask about this lesson…"
          // One row until the question needs more: `field-sizing: content`
          // grows the box with its text (to `max-h-40`, then it scrolls), so
          // the multi-line question Shift+Enter exists for (`composer-keys.ts`)
          // is not edited through a one-line window. Browsers without it keep
          // the one row and scroll inside it.
          className="min-w-0 flex-1 resize-none [field-sizing:content] max-h-40 rounded-md border border-divider bg-surface px-3 py-2 text-sm leading-6 text-porcelain placeholder:text-slate focus:border-iris focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
        />
        {streaming ? (
          // Stop is the *only* control in flight: it aborts the request, and
          // the question comes back to the composer for editing (TDD §5.6).
          <button
            type="button"
            data-testid="tutor-rail-stop"
            onClick={tutor.stop}
            className="inline-flex shrink-0 items-center justify-center rounded-md border border-divider px-4 text-sm font-semibold text-porcelain transition-colors hover:border-iris/50"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            data-testid="tutor-rail-send"
            disabled={tutor.draft.trim() === ""}
            className="inline-flex shrink-0 items-center justify-center rounded-md bg-iris px-4 text-sm font-semibold text-night transition-colors hover:bg-iris-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Ask
          </button>
        )}
      </form>
    </div>
  );
}
