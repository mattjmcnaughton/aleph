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
      className="fixed bottom-5 right-5 z-30 inline-flex items-center gap-2 rounded-full border border-iris/60 bg-surface px-4 py-3 text-sm font-semibold text-porcelain shadow-glow-iris transition-colors hover:border-iris hover:bg-elevated"
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
  const streaming = tutor.status === "streaming";
  const empty = tutor.messages.length === 0 && !streaming && tutor.status !== "failed";

  return (
    <section data-testid="tutor-rail" aria-label="Tutor" className="flex min-h-0 flex-1 flex-col">
      <RailHeader tutor={tutor} />

      <div
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

        {streaming ? (
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

// --- Header: new conversation, collapse, and the admin picker ----------------

function RailHeader({ tutor }: { tutor: TutorRailState }) {
  return (
    <div
      data-testid="tutor-rail-header"
      className="flex flex-wrap items-center gap-2 border-b border-divider px-4 py-3"
    >
      <TutorGlyph />
      <span className="mr-auto text-sm font-semibold text-porcelain">Tutor</span>

      <TutorModelPicker
        isAdmin={tutor.isAdmin}
        allowlist={tutor.modelAllowlist}
        value={tutor.model}
        onChange={tutor.setModel}
      />

      {tutor.confirmingNew ? (
        // Confirm in place, like the switcher's delete: clearing a thread is
        // destructive and not undoable, and it must never sit under one tap.
        <span className="flex items-center gap-2">
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
      ) : (
        <button
          type="button"
          data-testid="tutor-rail-new-conversation"
          onClick={tutor.askNewConversation}
          title="New conversation"
          className="rounded-md border border-divider px-2.5 py-1.5 text-xs text-mist transition-colors hover:border-iris/50 hover:text-porcelain"
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

function MessageBubble({
  message,
  tutor,
}: { message: ConversationMessage; tutor: TutorRailState }) {
  const isTutor = message.role === "tutor";
  return (
    <div
      data-testid="tutor-rail-message"
      data-role={message.role}
      // A posed Tutor check rides the cached message, so it survives a collapse,
      // a reopen, and a page revisit — and so does the answer written onto it.
      data-tutor-check={message.tutor_check ? "true" : undefined}
      className={isTutor ? "flex gap-2.5" : "flex justify-end"}
    >
      {isTutor ? <TutorGlyph size="2xs" /> : null}
      {isTutor ? (
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
      ) : (
        <p className="max-w-[85%] whitespace-pre-wrap rounded-lg border border-divider bg-surface px-3 py-2 text-sm leading-6 text-porcelain">
          {message.content}
        </p>
      )}
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

// --- Composer: chip, suggestions, textarea, send/stop -------------------------

function Composer({ tutor }: { tutor: TutorRailState }) {
  const streaming = tutor.status === "streaming";

  return (
    <div className="border-t border-divider px-4 py-3">
      {/* Suggestions sit with the composer so they are offered in the empty
          state and again after a reply settles (PRD §5.3) — never mid-stream,
          when tapping one could only queue a send the server would 409. */}
      {streaming ? null : (
        <div className="mb-3 flex flex-wrap gap-2">
          {TUTOR_SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              data-testid="tutor-rail-suggestion"
              onClick={() => tutor.send(suggestion, "suggestion")}
              className="rounded-full border border-divider px-3 py-1.5 text-xs text-mist transition-colors hover:border-iris/50 hover:text-porcelain"
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}

      {/* The scope statement, told once, where the question is typed. */}
      <p
        data-testid="tutor-rail-context-chip"
        className="mb-2 inline-flex items-center gap-2 rounded-full border border-divider px-3 py-1 text-xs text-mist"
      >
        <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-iris" />
        Reading · <span className="text-porcelain">{tutor.lessonTitle}</span>
      </p>

      <form
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
          rows={2}
          maxLength={TUTOR_MESSAGE_MAX_LENGTH}
          placeholder="Ask about this lesson…"
          className="w-full resize-none rounded-md border border-divider bg-surface px-3 py-2 text-sm text-porcelain placeholder:text-slate focus:border-iris focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
        />
        <div className="mt-2 flex justify-end">
          {streaming ? (
            // Stop is the *only* control in flight: it aborts the request, and
            // the question comes back to the composer for editing (TDD §5.6).
            <button
              type="button"
              data-testid="tutor-rail-stop"
              onClick={tutor.stop}
              className="inline-flex items-center justify-center rounded-md border border-divider px-4 py-2 text-sm font-semibold text-porcelain transition-colors hover:border-iris/50"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              data-testid="tutor-rail-send"
              disabled={tutor.draft.trim() === ""}
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
