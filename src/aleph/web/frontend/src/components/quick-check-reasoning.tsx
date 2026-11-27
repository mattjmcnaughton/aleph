// The Reasoning step of a Quick check (CONTEXT.md: *Reasoning*): before the
// answer is revealed the learner says why their choice is right, and the reveal
// shows how that reasoning measured against the Grading key, with at most one
// follow-up question. Formative only: the Outcome is still the option chosen.

import { useState } from "react";

import type { Reasoning, ReasoningFollowUp, ReasoningGrade } from "../lib/api";
import { PRIMARY_CTA } from "./state-card";

export const REASONING_MAX_LENGTH = 1000;

const FIELD =
  "mt-2 w-full resize-y rounded-md border border-divider bg-surface px-4 py-3 text-base text-porcelain placeholder:text-slate focus:border-teal focus:outline-none disabled:opacity-60";

/** The "why" box under the options, before the Attempt. */
export function ReasoningField({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="mt-5">
      <label htmlFor="quick-check-reasoning" className="text-[15px] font-medium text-porcelain">
        Why is that the answer?
      </label>
      <p className="mt-1 text-sm leading-6 text-slate">
        A sentence or two in your own words. It's checked against what a good answer needs to say.
      </p>
      <textarea
        id="quick-check-reasoning"
        data-testid="quick-check-reasoning"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={3}
        maxLength={REASONING_MAX_LENGTH}
        disabled={disabled}
        placeholder="Because…"
        className={FIELD}
      />
    </div>
  );
}

const GRADE_COPY: Record<ReasoningGrade, { label: string; tone: string }> = {
  solid: { label: "Solid reasoning", tone: "text-teal" },
  partial: { label: "Partly there", tone: "text-porcelain" },
  missing: { label: "Reasoning missing", tone: "text-porcelain" },
};

/** How the learner's Reasoning measured up, shown inside the reveal. */
export function ReasoningReveal({
  reasoning,
  onFollowUp,
  answering,
  answerErrored,
}: {
  reasoning: Reasoning;
  onFollowUp: (answer: string) => void;
  answering: boolean;
  answerErrored: boolean;
}) {
  const covered = new Set(reasoning.covered_points);
  const graded = reasoning.state === "graded";
  return (
    <div
      data-testid="reasoning-reveal"
      data-state={reasoning.state}
      data-grade={reasoning.grade ?? undefined}
      aria-live="polite"
      className="mt-4 rounded-md border border-divider p-5"
    >
      <p className="kicker">Your reasoning</p>
      <p className="mt-2 whitespace-pre-line text-[15px] leading-6 text-mist">{reasoning.text}</p>

      {graded && reasoning.grade ? (
        <>
          <p
            data-testid="reasoning-grade"
            className={`mt-4 text-[15px] font-semibold ${GRADE_COPY[reasoning.grade].tone}`}
          >
            {GRADE_COPY[reasoning.grade].label}
          </p>
          {reasoning.feedback ? (
            <p data-testid="reasoning-feedback" className="mt-1 text-[15px] leading-6">
              {reasoning.feedback}
            </p>
          ) : null}
        </>
      ) : (
        <p data-testid="reasoning-status" className="mt-4 text-sm leading-6 text-slate">
          {reasoning.state === "grading"
            ? "Still checking your reasoning. Reload in a moment to see it."
            : "Your reasoning couldn't be checked this time. Compare it with what a good answer says."}
        </p>
      )}

      <p className="mt-4 text-sm font-medium text-mist">A good answer says</p>
      <ul data-testid="reasoning-key-points" className="mt-2 space-y-2">
        {reasoning.key_points.map((point, index) => {
          const made = graded && covered.has(index);
          return (
            <li
              key={point}
              data-testid="reasoning-key-point"
              data-covered={graded ? made : undefined}
              className="flex gap-2 text-[15px] leading-6"
            >
              <span
                aria-hidden="true"
                className={`mt-[3px] grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border ${
                  made ? "border-teal bg-teal text-night" : "border-faint"
                }`}
              >
                {made ? (
                  <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" fill="none" aria-hidden="true">
                    <path
                      d="M2.5 6.2 5 8.5 9.5 3.5"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                ) : null}
              </span>
              {graded ? <span className="sr-only">{made ? "You said: " : "Missing: "}</span> : null}
              <span className={made ? "text-porcelain" : "text-mist"}>{point}</span>
            </li>
          );
        })}
      </ul>

      {graded && reasoning.misconceptions_shown.length > 0 ? (
        <div className="mt-4">
          <p className="text-sm font-medium text-mist">Watch out for</p>
          <ul data-testid="reasoning-misconceptions" className="mt-1 space-y-1">
            {reasoning.misconceptions_shown.map((item) => (
              <li key={item} className="text-[15px] leading-6 text-porcelain">
                {item}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {reasoning.follow_up ? (
        <FollowUpBlock
          followUp={reasoning.follow_up}
          onAnswer={onFollowUp}
          answering={answering}
          answerErrored={answerErrored}
        />
      ) : null}
    </div>
  );
}

function FollowUpBlock({
  followUp,
  onAnswer,
  answering,
  answerErrored,
}: {
  followUp: ReasoningFollowUp;
  onAnswer: (answer: string) => void;
  answering: boolean;
  answerErrored: boolean;
}) {
  const [draft, setDraft] = useState("");
  return (
    <div data-testid="reasoning-follow-up" className="mt-5 border-t border-divider pt-4">
      <p className="kicker">One more question</p>
      <p data-testid="reasoning-follow-up-question" className="mt-2 text-[15px] leading-6">
        {followUp.question}
      </p>
      {followUp.answer === null ? (
        <>
          <label htmlFor="reasoning-follow-up-answer" className="sr-only">
            Your answer
          </label>
          <textarea
            id="reasoning-follow-up-answer"
            data-testid="reasoning-follow-up-answer"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={2}
            maxLength={REASONING_MAX_LENGTH}
            disabled={answering}
            className={FIELD}
          />
          {answerErrored ? (
            <p
              aria-live="assertive"
              className="mt-2 text-sm leading-6 text-danger"
              data-testid="reasoning-follow-up-error"
            >
              That didn't go through. Check your connection and try again.
            </p>
          ) : null}
          <button
            type="button"
            data-testid="reasoning-follow-up-submit"
            disabled={answering || draft.trim() === ""}
            onClick={() => onAnswer(draft.trim())}
            className={`mt-3 ${PRIMARY_CTA}`}
          >
            {answering ? "Checking…" : "Answer"}
          </button>
        </>
      ) : (
        <>
          <p className="mt-3 whitespace-pre-line text-[15px] leading-6 text-mist">
            {followUp.answer}
          </p>
          {followUp.state === "graded" && followUp.grade ? (
            <p data-testid="reasoning-follow-up-feedback" className="mt-3 text-[15px] leading-6">
              <span className={`font-semibold ${GRADE_COPY[followUp.grade].tone}`}>
                {GRADE_COPY[followUp.grade].label}.
              </span>{" "}
              {followUp.feedback}
            </p>
          ) : (
            <p className="mt-3 text-sm leading-6 text-slate">
              {followUp.state === "grading"
                ? "Still checking your answer."
                : "Your answer couldn't be checked this time."}
            </p>
          )}
        </>
      )}
    </div>
  );
}
