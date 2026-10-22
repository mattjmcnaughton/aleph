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

import { useState } from "react";
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
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);

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
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
      >
        <div className="space-y-6 px-5 py-6">
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

          {empty ? <EmptyState tutor={tutor} /> : null}

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
            <div>
              <Markdown
                testid="tutor-rail-streaming"
                className="min-w-0 text-base [&_p]:text-base [&_p]:leading-7"
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
      </div>

      {suggestionsOpen && tutor.status !== "streaming" ? (
        <div className="mx-4 min-h-0 max-h-48 overflow-y-auto overscroll-contain rounded-xl border border-divider bg-surface">
          {TUTOR_SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              data-testid="tutor-rail-suggestion"
              onClick={() => {
                setSuggestionsOpen(false);
                tutor.send(suggestion, "suggestion");
              }}
              className="min-h-11 w-full rounded-lg px-3 py-2 text-left text-sm text-porcelain hover:bg-elevated"
            >
              {suggestion}
            </button>
          ))}
        </div>
      ) : null}
      <Composer
        tutor={tutor}
        suggestionsOpen={suggestionsOpen}
        toggleSuggestions={() => setSuggestionsOpen(!suggestionsOpen)}
      />
    </section>
  );
}

// --- Header: title, full-width scope, secondary actions and close ------------

function RailHeader({ tutor }: { tutor: TutorRailState }) {
  return (
    <div
      data-testid="tutor-rail-header"
      className="relative z-10 shrink-0 border-b border-divider px-5 pb-3 pt-3"
    >
      <div className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-x-2 gap-y-2">
        <TutorGlyph />
        <span className="text-base font-semibold text-porcelain">Tutor</span>
        <details
          className="relative"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.currentTarget.open = false;
              event.currentTarget.querySelector("summary")?.focus();
            }
          }}
        >
          <summary
            aria-label="Tutor options"
            className="grid h-11 w-11 cursor-pointer list-none place-items-center rounded-lg text-xl text-mist hover:bg-surface focus-visible:outline focus-visible:outline-iris [&::-webkit-details-marker]:hidden"
          >
            <span aria-hidden="true">⋯</span>
          </summary>
          <div className="absolute right-0 top-full mt-2 w-56 rounded-lg border border-divider bg-surface p-2 shadow-lg">
            <button
              type="button"
              data-testid="tutor-rail-new-conversation"
              onClick={(event) => {
                event.currentTarget.closest("details")?.removeAttribute("open");
                tutor.askNewConversation();
              }}
              className="min-h-11 w-full rounded-md px-3 text-left text-sm text-porcelain hover:bg-elevated"
            >
              New conversation
            </button>
            <TutorModelPicker
              isAdmin={tutor.isAdmin}
              allowlist={tutor.modelAllowlist}
              value={tutor.model}
              onChange={tutor.setModel}
            />
          </div>
        </details>

        <button
          type="button"
          data-testid="tutor-rail-collapse"
          onClick={tutor.closeRail}
          aria-label="Close the tutor"
          title="Back to lesson"
          className="grid h-11 w-11 place-items-center rounded-lg text-mist transition-colors hover:bg-surface hover:text-porcelain focus-visible:outline focus-visible:outline-iris"
        >
          <svg
            aria-hidden="true"
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          >
            <path d="m6 6 12 12M18 6 6 18" />
          </svg>
        </button>

        <span
          data-testid="tutor-rail-context-chip"
          title={tutor.lessonTitle}
          className="col-span-4 flex items-start gap-2 text-sm leading-5 text-mist"
        >
          <span aria-hidden="true" className="mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full bg-iris" />
          {/* Two lines before clipping: the scope statement is the point of
                the line (PRD §5.2), and one line clipped the PRD's own example
                title. `title` carries the whole thing for a hover. */}
          <span className="line-clamp-2">
            Reading · <span className="text-porcelain">{tutor.lessonTitle}</span>
          </span>
        </span>

        {tutor.confirmingNew ? (
          // Confirm in place, like the switcher's delete: clearing a thread is
          // destructive and not undoable, and it must never sit under one tap.
          // It takes its own row (`basis-full`) under the title row, so the ×
          // keeps its corner and the tab order runs top to bottom.
          <span className="col-span-4 flex flex-wrap items-center justify-end gap-2">
            <span className="text-xs text-mist">Clear this conversation?</span>
            <button
              type="button"
              data-testid="tutor-rail-new-conversation-confirm"
              onClick={tutor.confirmNewConversation}
              className="min-h-11 rounded-md border border-danger-border/60 bg-danger-bg px-3 text-sm font-semibold text-danger transition-colors hover:border-danger"
            >
              Clear
            </button>
            <button
              type="button"
              data-testid="tutor-rail-new-conversation-cancel"
              onClick={tutor.cancelNewConversation}
              className="min-h-11 rounded-md border border-divider px-3 text-sm text-mist transition-colors hover:text-porcelain"
            >
              Keep
            </button>
          </span>
        ) : null}
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
      <p className="max-w-[90%] whitespace-pre-wrap break-words rounded-2xl bg-surface px-4 py-3 text-base leading-7 text-porcelain">
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
      className="min-w-0"
    >
      <div className="min-w-0">
        {/* Generated prose goes through the one renderer, always (the security
            boundary — no second pipeline, no `dangerouslySetInnerHTML`). */}
        <Markdown className="text-base [&_p]:text-base [&_p]:leading-7">{message.content}</Markdown>
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
function EmptyState({ tutor }: { tutor: TutorRailState }) {
  return (
    <div data-testid="tutor-rail-empty" className="py-6">
      <p className="text-xl font-semibold text-porcelain">What would you like to unpack?</p>
      <p className="mt-3 text-base leading-7 text-mist">
        Ask about this lesson’s Read passage or Quick check.
      </p>
      <div className="mt-6 grid gap-3">
        {[TUTOR_SUGGESTIONS[0], TUTOR_SUGGESTIONS[3]].map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            onClick={() => tutor.send(suggestion, "suggestion")}
            className="min-h-12 rounded-xl border border-divider px-4 py-3 text-left text-sm text-porcelain transition-colors hover:border-iris hover:bg-surface"
          >
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  );
}

// --- Composer: suggestions, textarea, send/stop -------------------------------

function Composer({
  tutor,
  suggestionsOpen,
  toggleSuggestions,
}: {
  tutor: TutorRailState;
  suggestionsOpen: boolean;
  toggleSuggestions: () => void;
}) {
  const streaming = tutor.status === "streaming";

  return (
    // `pb-[max(...)]`: the sheet ends at the physical bottom of a phone, so the
    // composer pads by the home-indicator inset where there is one (and by its
    // ordinary 12px where there isn't). Needs `viewport-fit=cover` in index.html.
    <div className="shrink-0 border-t border-divider px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
      <form
        className="flex items-end gap-1 rounded-2xl border border-divider bg-surface p-1.5 focus-within:border-iris"
        onSubmit={(event) => {
          event.preventDefault();
          tutor.send(tutor.draft, "typed");
        }}
      >
        {streaming ? null : (
          <button
            type="button"
            aria-label="Suggested questions"
            aria-expanded={suggestionsOpen}
            title="Suggested questions"
            onClick={toggleSuggestions}
            className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-xl text-mist hover:bg-elevated focus-visible:outline focus-visible:outline-iris"
          >
            <span aria-hidden="true">{suggestionsOpen ? "−" : "+"}</span>
          </button>
        )}
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
          // grows the box with its text (then it scrolls), so
          // the multi-line question Shift+Enter exists for (`composer-keys.ts`)
          // is not edited through a one-line window. Browsers without it keep
          // the one row and scroll inside it.
          className="min-h-11 min-w-0 flex-1 resize-none [field-sizing:content] max-h-[min(8rem,calc(var(--tutor-viewport-height,100dvh)*0.2))] bg-transparent px-1 py-2.5 text-base leading-6 text-porcelain placeholder:text-mist focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
        />
        {streaming ? (
          // Stop is the *only* control in flight: it aborts the request, and
          // the question comes back to the composer for editing (TDD §5.6).
          <button
            type="button"
            data-testid="tutor-rail-stop"
            onClick={tutor.stop}
            className="inline-flex h-11 shrink-0 items-center justify-center rounded-xl border border-divider px-3 text-sm font-semibold text-porcelain transition-colors hover:border-iris/50"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            data-testid="tutor-rail-send"
            aria-label="Ask"
            disabled={tutor.draft.trim() === ""}
            className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-iris text-night transition-colors hover:bg-iris-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <svg
              aria-hidden="true"
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12 19V5m-6 6 6-6 6 6" />
            </svg>
          </button>
        )}
      </form>
    </div>
  );
}
