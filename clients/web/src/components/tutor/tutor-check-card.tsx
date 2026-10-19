// The Tutor check card (AL-231, TDD §8, PRD §5.5, mock Turn 1c) — the tutor's
// own question, inside the conversation.
//
// **It reveals locally, and that is the whole design.** `TutorCheckDTO` carries
// `correct_index` and `explanation` on delivery (TDD §6), so a tap grades
// nothing: the card already holds the answer. The persist that follows
// (`answered_index`) exists so a revisited thread renders revealed — it is never
// on the path to the feedback, and its failure is invisible by design.
//
// **A Tutor check is not a Quick check** (CONTEXT.md keeps the two words apart,
// and so does this file). It is non-scoring and outside progress: no Attempt, no
// completion, no §7 metric derived from Attempts, and nothing here reads or
// writes a single lesson query. The copy says so in words rather than leaving
// the learner to infer it from a card that looks exactly like the graded one.
//
// Two deliberate departures from the lesson's Quick check, both because grading
// is what the differences are about:
//   - **One tap answers.** There is no "Check answer" button: the feedback is
//     local, so a confirm step would only add a wait to something instant.
//   - **The reveal is terminal in the UI.** The route is last-wins, but the card
//     offers no re-answer: the learner has read the explanation, so a second
//     pick would record a choice they did not really make.

import { Markdown } from "../markdown";
import type { TutorCheck } from "../../lib/tutor-stream";

/**
 * The two one-tap asks offered once a check is revealed (PRD §5.5). Constant and
 * client-side like `TUTOR_SUGGESTIONS`, and sent the same way: as ordinary
 * content down the ordinary composer path, tagged `source: "suggestion"` because
 * that is what they are — a button, not something the learner typed. There is no
 * follow-up transport, and no server round trip other than the send itself.
 */
const TUTOR_CHECK_FOLLOW_UPS: readonly string[] = ["Another one", "Why is that right?"];

export interface TutorCheckCardProps {
  /** The message that posed the check — the id the answer is recorded against. */
  messageId: string;
  check: TutorCheck;
  /** Records the choice; the reveal *is* its cache write (`useTutorRail`). */
  onAnswer: (messageId: string, selectedIndex: number) => void;
  /** Sends a follow-up as if it had been typed into the composer. */
  onFollowUp: (content: string) => void;
  /** A reply is in flight — the composer is closed, so the follow-ups are too. */
  sending: boolean;
}

export function TutorCheckCard({
  messageId,
  check,
  onAnswer,
  onFollowUp,
  sending,
}: TutorCheckCardProps) {
  // The single source of truth for "is this revealed", whether the learner
  // tapped a moment ago or the thread came back from the server this way.
  const answered = check.answered_index;
  const revealed = answered !== null;
  const correct = revealed && answered === check.correct_index;

  return (
    <div
      data-testid="tutor-rail-check"
      data-answered={revealed ? "true" : undefined}
      className="mt-3 rounded-lg border border-iris/40 bg-iris/[0.07] p-3.5"
    >
      {/* The card names itself: inside a reply, an unlabelled block of options
          is indistinguishable from the graded Quick check the learner has just
          been reading below it. `iris-400` is the mock's own kicker colour. */}
      <p className="kicker text-iris-400">Tutor check</p>
      {/* Stated before the answer, not after: it changes how the question reads.
          A learner who thinks this is graded will treat a Tutor check as a test
          rather than as practice — and it is neither an Attempt nor progress. */}
      <p data-testid="tutor-rail-check-note" className="mt-1 text-xs leading-5 text-mist">
        This doesn't count toward the lesson.
      </p>

      <p
        data-testid="tutor-rail-check-stem"
        className="mt-2 text-sm font-semibold leading-snug text-porcelain"
      >
        {check.stem}
      </p>

      <div className="mt-3 flex flex-col gap-1.5">
        {check.options.map((option, index) => {
          const isSelected = answered === index;
          const isCorrect = check.correct_index === index;
          return (
            <button
              key={`${index}-${option}`}
              type="button"
              data-testid="tutor-rail-check-option"
              // Only meaningful once revealed — before that, publishing which
              // option is keyed correct would put the answer in the DOM.
              data-correct={revealed ? String(isCorrect) : undefined}
              data-selected={revealed ? String(isSelected) : undefined}
              // `aria-disabled`, not native `disabled` (the Quick check's rule):
              // an answered card stays tabbable so a screen-reader user can read
              // back which option was theirs and which was right.
              aria-disabled={revealed || undefined}
              onClick={() => {
                if (revealed) return;
                onAnswer(messageId, index);
              }}
              className={optionClass(revealed, isSelected, isCorrect)}
            >
              {/* The reveal is carried by colour; these prefixes carry the same
                  meaning to assistive tech. */}
              {revealed && isCorrect ? <span className="sr-only">Correct answer: </span> : null}
              {revealed && isSelected && !isCorrect ? (
                <span className="sr-only">Your answer: </span>
              ) : null}
              <span className="min-w-0 flex-1">{option}</span>
            </button>
          );
        })}
      </div>

      {revealed ? (
        <div className="mt-3">
          <p
            data-testid="tutor-rail-check-outcome"
            data-outcome={correct ? "correct" : "incorrect"}
            aria-live="polite"
            className={`text-xs font-semibold ${correct ? "text-teal" : "text-iris-300"}`}
          >
            {correct ? "Correct." : "Not quite."}
          </p>
          {/* Generated prose through the one renderer, always — `markdown.tsx`
              is the security boundary, and an explanation is model output. */}
          <Markdown
            testid="tutor-rail-check-explanation"
            className="mt-1.5 text-sm text-mist [&_p]:text-sm [&_p]:leading-6"
          >
            {check.explanation}
          </Markdown>

          <div className="mt-3 flex flex-wrap gap-2">
            {TUTOR_CHECK_FOLLOW_UPS.map((followUp) => (
              <button
                key={followUp}
                type="button"
                data-testid="tutor-rail-check-follow-up"
                disabled={sending}
                onClick={() => onFollowUp(followUp)}
                className="rounded-full border border-divider px-3 py-1.5 text-xs text-mist transition-colors hover:border-iris/50 hover:text-porcelain disabled:cursor-not-allowed disabled:opacity-50"
              >
                {followUp}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

const OPTION_BASE =
  "flex w-full items-center gap-2.5 rounded-md border px-3 py-2.5 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-iris focus-visible:ring-offset-2 focus-visible:ring-offset-night";

/**
 * Correct is teal and a wrong pick is danger — the same reveal vocabulary the
 * lesson's Quick check uses, because "this one was right" must not mean two
 * different colours in one app. Iris stays the card's own frame: the *surface*
 * is the tutor's, the *verdict* is the shared one (mock Turn 1c draws it exactly
 * this way).
 */
function optionClass(revealed: boolean, selected: boolean, correct: boolean): string {
  if (!revealed) {
    return `${OPTION_BASE} cursor-pointer border-divider bg-surface/60 text-mist hover:border-iris/50 hover:text-porcelain`;
  }
  if (correct) return `${OPTION_BASE} border-teal bg-teal/10 text-porcelain`;
  if (selected) return `${OPTION_BASE} border-danger-border/60 bg-danger-bg text-porcelain`;
  return `${OPTION_BASE} border-divider bg-surface/50 text-slate`;
}
