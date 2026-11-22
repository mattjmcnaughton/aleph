import type { PathDraft } from "../../lib/api";
import { levelLabel } from "../../lib/onboarding";
import { draftChanges, guidanceLines } from "../../lib/path-draft";
import { SECONDARY_CTA } from "../state-card";
import type { PathDraftController } from "./use-path-draft";

// New path, drafted from an idea (CONTEXT.md: *Idea*, *Path draft*,
// *Interview*). Iris marks what Aleph proposes; nothing here creates anything.
// "Use this draft" hands the draft to the route, which fills its ordinary form,
// and "Build my path" there stays the page's one primary action
// (Constitution §2: models propose, learners authorize).

const IRIS_CARD = "mt-8 rounded-lg border border-iris/50 bg-iris/5 px-4 py-4";
const IRIS_BUTTON =
  "inline-flex min-h-12 w-full items-center justify-center rounded-md border border-iris-500 px-5 py-3 text-[15px] font-semibold text-iris-300 transition-colors hover:bg-iris-700/40 disabled:cursor-not-allowed disabled:opacity-50";
const QUIET_BUTTON =
  "inline-flex min-h-11 w-full items-center justify-center rounded-md px-4 py-2 text-sm font-medium text-mist transition-colors hover:text-porcelain";
const FIELD =
  "w-full rounded-md border border-divider bg-surface px-4 py-3 text-base text-porcelain placeholder:text-slate focus:border-teal focus:outline-none";

/** Mirrors `IdeaStr` in `dtos/path_drafts.py`. */
const IDEA_MAX_LENGTH = 1000;
/** Own-words answers stay well inside `AnswerStr` once options are joined on. */
const OWN_WORDS_MAX_LENGTH = 300;

interface FlowProps {
  flow: PathDraftController;
  onUse: (draft: PathDraft) => void;
  onFillMyself: () => void;
}

export function PathDraftFlow({ flow, onUse, onFillMyself }: FlowProps) {
  const { state } = flow;
  switch (state.step) {
    case "idea":
      return <IdeaStep flow={flow} onFillMyself={onFillMyself} />;
    case "drafting":
      return (
        <Working
          idea={state.idea}
          title="Drafting from your idea…"
          body="Aleph is writing a topic, guidance and a teaching style. A few seconds."
        />
      );
    case "draft":
    case "sharpened":
      return <DraftStep flow={flow} onUse={onUse} />;
    case "interview":
      return <InterviewStep flow={flow} />;
    case "redrafting":
      return state.error === "redraft" ? (
        <div className={IRIS_CARD}>
          <DraftError message="Aleph couldn't redraft just now. Your answers are kept." />
          <div className="mt-4 grid gap-3">
            <button type="button" className={IRIS_BUTTON} onClick={flow.redraft}>
              Try again
            </button>
            <button type="button" className={QUIET_BUTTON} onClick={flow.backToQuestions}>
              Back to the questions
            </button>
          </div>
        </div>
      ) : (
        <Working
          idea={state.idea}
          title="Redrafting with your answers…"
          body="Aleph is folding your answers into the draft."
        />
      );
  }
}

function DraftError({ message }: { message: string }) {
  return (
    <p
      data-testid="path-draft-error"
      role="alert"
      className="rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-danger"
    >
      {message}
    </p>
  );
}

function IdeaStep({ flow, onFillMyself }: Pick<FlowProps, "flow" | "onFillMyself">) {
  const { state } = flow;
  const failed = state.error === "draft";
  return (
    <>
      <form
        className={IRIS_CARD}
        data-testid="path-draft-idea"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          flow.draft();
        }}
      >
        <label htmlFor="path-draft-idea" className="text-sm font-medium text-porcelain">
          Start from an idea
        </label>
        <p className="mt-1 text-sm leading-6 text-mist">
          A sentence or two is enough. Aleph turns it into a topic and guidance you can edit.
        </p>
        <textarea
          id="path-draft-idea"
          rows={4}
          value={state.idea}
          onChange={(event) => flow.setIdea(event.target.value)}
          placeholder="e.g. I want to understand why our database queries get slow at work."
          maxLength={IDEA_MAX_LENGTH}
          className={`mt-3 resize-y ${FIELD} focus:border-iris`}
        />
        {failed ? (
          <div className="mt-3">
            <DraftError message="Aleph couldn't draft that just now. Your idea is still here." />
          </div>
        ) : null}
        <button type="submit" className={`mt-3 ${IRIS_BUTTON}`} disabled={state.idea.trim() === ""}>
          {failed ? "Try again" : "Draft it for me"}
        </button>
      </form>
      <button type="button" className={`mt-4 ${QUIET_BUTTON}`} onClick={onFillMyself}>
        I'll fill in the form myself
      </button>
    </>
  );
}

function Working({ idea, title, body }: { idea: string; title: string; body: string }) {
  return (
    <>
      <div
        className="mt-8 rounded-lg border border-iris/50 bg-iris/5 px-5 py-8 text-center"
        data-testid="path-draft-working"
        aria-live="polite"
      >
        <div
          className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-divider border-t-iris"
          aria-hidden="true"
        />
        <h2 className="mt-4 text-lg font-semibold">{title}</h2>
        <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">{body}</p>
      </div>
      {idea.trim() ? (
        <p className="mt-4 text-sm leading-6 text-slate">Your idea: “{idea.trim()}”</p>
      ) : null}
    </>
  );
}

function DraftRow({
  label,
  changed,
  children,
}: { label: string; changed?: boolean; children: React.ReactNode }) {
  return (
    <div className="border-t border-divider py-3 first:border-t-0 first:pt-0">
      <dt className="flex items-center gap-2 text-xs font-medium text-iris-300">
        {label}
        {changed ? (
          <span
            data-testid="topic-changed"
            className="rounded-sm bg-iris-700/50 px-1.5 py-px text-[11px] font-medium text-iris-300"
          >
            From your answers
          </span>
        ) : null}
      </dt>
      <dd className="mt-1 text-sm leading-6 text-porcelain">{children}</dd>
    </div>
  );
}

function DraftStep({ flow, onUse }: Pick<FlowProps, "flow" | "onUse">) {
  const { state } = flow;
  const draft = state.current;
  if (draft === null) return null;
  const sharpened = state.step === "sharpened";
  const { topicChanged, changedLines } = draftChanges(sharpened ? state.previous : null, draft);
  const count = state.answers.length;
  const lines = guidanceLines(draft.guidance);
  return (
    <>
      <div className={IRIS_CARD} data-testid="path-draft">
        <p className="text-xs font-medium text-iris-300">
          {sharpened ? "Aleph's draft, sharpened" : "Aleph's draft"}
        </p>
        <p className="mt-1 text-sm leading-6 text-mist">
          {sharpened
            ? `Redrafted from your idea and ${count} ${count === 1 ? "answer" : "answers"}.${
                changedLines.size > 0 ? " Marked lines are new." : ""
              }`
            : "Made from your idea. Nothing is set until you use it, and you can edit every field after."}
        </p>
        <dl className="mt-4">
          <DraftRow label="Topic" changed={topicChanged}>
            <span className="font-serif text-[1.0625rem]">{draft.topic}</span>
          </DraftRow>
          <DraftRow label="Level">{levelLabel(draft.level)}</DraftRow>
          <DraftRow label="Guidance">
            {lines.length > 0 ? (
              <ul className="space-y-1.5">
                {lines.map((line, index) => (
                  <li
                    // Lines can repeat; position disambiguates.
                    key={`${index}-${line}`}
                    data-testid="guidance-line"
                    data-changed={changedLines.has(index) ? "true" : undefined}
                    className={
                      changedLines.has(index)
                        ? "-mx-2 rounded-sm border-l-2 border-iris bg-iris/10 px-2"
                        : undefined
                    }
                  >
                    {line}
                  </li>
                ))}
              </ul>
            ) : (
              <span className="text-mist">None</span>
            )}
          </DraftRow>
          <DraftRow label="Teaching style">
            {draft.teaching_style || <span className="text-mist">None</span>}
          </DraftRow>
        </dl>
      </div>
      <div className="mt-4 grid gap-3">
        <button type="button" className={IRIS_BUTTON} onClick={() => onUse(draft)}>
          Use this draft
        </button>
        <button type="button" className={SECONDARY_CTA} onClick={flow.startInterview}>
          {sharpened ? "Ask me more" : "Sharpen it: ask me a few questions"}
        </button>
        <button
          type="button"
          className={QUIET_BUTTON}
          onClick={sharpened ? flow.backToFirst : flow.changeIdea}
        >
          {sharpened ? "Back to the first draft" : "Change my idea"}
        </button>
      </div>
    </>
  );
}

function InterviewStep({ flow }: Pick<FlowProps, "flow">) {
  const { state } = flow;
  const round = state.round;

  if (round === null) {
    if (state.error !== "questions") {
      return (
        <Working
          idea={state.idea}
          title="Thinking of a few questions…"
          body="Aleph is reading the draft for what it still needs to know."
        />
      );
    }
    return (
      <div className={IRIS_CARD} data-testid="draft-interview">
        <DraftError message="Aleph couldn't think of questions just now. Your draft is kept." />
        <div className="mt-4 grid gap-3">
          <button type="button" className={IRIS_BUTTON} onClick={flow.startInterview}>
            Try again
          </button>
          <button type="button" className={QUIET_BUTTON} onClick={flow.backToDraft}>
            Back to the draft
          </button>
        </div>
      </div>
    );
  }

  const { questions, index } = round;
  const question = questions[index];
  const last = index === questions.length - 1;
  const picked = round.picks[index];

  return (
    <>
      <div className={IRIS_CARD} data-testid="draft-interview">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium text-iris-300">
            Question {index + 1} of {questions.length}
          </p>
          <div className="flex gap-1" aria-hidden="true">
            {questions.map((q, i) => (
              <span
                key={q.prompt}
                className={`h-1.5 w-6 rounded-full ${i <= index ? "bg-iris" : "bg-faint"}`}
              />
            ))}
          </div>
        </div>
        <h2 className="mt-3 font-serif text-xl font-medium leading-7">{question.prompt}</h2>
        <p className="mt-1 text-sm leading-6 text-mist">{question.why}</p>
        <fieldset className="mt-4 grid gap-2">
          <legend className="sr-only">{question.prompt}</legend>
          {question.options.map((option, i) => {
            const selected = picked.includes(i);
            return (
              <label
                key={option}
                className={`flex min-h-12 cursor-pointer items-center gap-3 rounded-md border px-4 py-3 text-sm font-medium transition-colors has-[:focus-visible]:border-iris ${
                  selected
                    ? "border-iris bg-iris/10 text-porcelain"
                    : "border-divider bg-surface text-mist hover:text-porcelain"
                }`}
              >
                <input
                  type={question.multi ? "checkbox" : "radio"}
                  name={`draft-question-${index}`}
                  checked={selected}
                  onChange={() => flow.toggle(i)}
                  className="sr-only"
                />
                {option}
              </label>
            );
          })}
          <input
            type="text"
            aria-label="Or say it in your own words…"
            placeholder="Or say it in your own words…"
            value={round.texts[index]}
            onChange={(event) => flow.setOwnWords(event.target.value)}
            maxLength={OWN_WORDS_MAX_LENGTH}
            className={`${FIELD} focus:border-iris`}
          />
        </fieldset>
        <div className="mt-4 flex gap-3">
          <button
            type="button"
            className={`${QUIET_BUTTON} w-auto flex-1`}
            onClick={() => (index === 0 ? flow.backToDraft() : flow.goTo(index - 1))}
          >
            Back
          </button>
          <button
            type="button"
            className={`${IRIS_BUTTON} flex-[2]`}
            onClick={() => (last ? flow.redraft() : flow.goTo(index + 1))}
          >
            {last ? "Redraft" : "Next"}
          </button>
        </div>
      </div>
      <button type="button" className={`mt-4 ${QUIET_BUTTON}`} onClick={flow.redraft}>
        That's enough, redraft with what I've said
      </button>
    </>
  );
}
