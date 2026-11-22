import { useRef, useState } from "react";
import {
  type InterviewAnswer,
  type InterviewQuestion,
  type PathDraft,
  createDraftQuestions,
  createPathDraft,
} from "../../lib/api";
import { MAX_ANSWERS, answerText } from "../../lib/path-draft";

// The New path draft flow's state (CONTEXT.md: *Idea*, *Path draft*,
// *Interview*). Held by the route, not the flow component, so the draft
// survives a trip to the form and back ("See draft"). Everything lives here in
// the client: the API is stateless, so each call resends the idea, every answer
// so far and the draft being sharpened. A failed call keeps all of it.

export type DraftStep = "idea" | "drafting" | "draft" | "interview" | "redrafting" | "sharpened";

export interface InterviewRound {
  questions: InterviewQuestion[];
  index: number;
  /** Per question: the indexes of the picked options. */
  picks: number[][];
  /** Per question: the learner's own words. */
  texts: string[];
}

export interface PathDraftState {
  idea: string;
  step: DraftStep;
  /** Which call failed, shown inline in the current card with Try again. */
  error: "draft" | "questions" | "redraft" | null;
  /** The draft from the idea alone ("Back to the first draft"). */
  first: PathDraft | null;
  /** The draft on show. */
  current: PathDraft | null;
  /** The draft `current` was redrafted from; what its marks compare against. */
  previous: PathDraft | null;
  /** Every answer `current` was drafted with, across rounds. */
  answers: InterviewAnswer[];
  /** The round being answered; `null` while its questions load. */
  round: InterviewRound | null;
}

const INITIAL: PathDraftState = {
  idea: "",
  step: "idea",
  error: null,
  first: null,
  current: null,
  previous: null,
  answers: [],
  round: null,
};

function roundAnswers(round: InterviewRound | null): InterviewAnswer[] {
  if (round === null) return [];
  return round.questions.flatMap((question, index) => {
    const answer = answerText(question.options, round.picks[index], round.texts[index]);
    return answer === null ? [] : [{ question: question.prompt, answer }];
  });
}

export function usePathDraft() {
  const [state, setState] = useState<PathDraftState>(INITIAL);
  // Only the newest request may land: a response to a call the learner has
  // since walked away from is dropped.
  const requestRef = useRef(0);

  function patch(update: Partial<PathDraftState>) {
    setState((current) => ({ ...current, ...update }));
  }

  async function draft() {
    const request = ++requestRef.current;
    const idea = state.idea.trim();
    if (!idea) return;
    patch({ step: "drafting", error: null });
    try {
      const drafted = await createPathDraft({ idea });
      if (request !== requestRef.current) return;
      patch({
        step: "draft",
        first: drafted,
        current: drafted,
        previous: null,
        answers: [],
        round: null,
      });
    } catch {
      if (request === requestRef.current) patch({ step: "idea", error: "draft" });
    }
  }

  async function startInterview() {
    const request = ++requestRef.current;
    if (state.current === null) return;
    patch({ step: "interview", error: null, round: null });
    try {
      const { questions } = await createDraftQuestions({
        idea: state.idea.trim(),
        draft: state.current,
        answers: state.answers.slice(-MAX_ANSWERS),
      });
      if (request !== requestRef.current) return;
      patch({
        round: {
          questions,
          index: 0,
          picks: questions.map(() => []),
          texts: questions.map(() => ""),
        },
      });
    } catch {
      if (request === requestRef.current) patch({ error: "questions" });
    }
  }

  /** Leave the interview (or a failed redraft) for the draft it was sharpening. */
  function backToDraft() {
    requestRef.current += 1;
    patch({ step: state.previous ? "sharpened" : "draft", error: null, round: null });
  }

  function updateRound(update: (round: InterviewRound) => InterviewRound) {
    setState((current) => (current.round ? { ...current, round: update(current.round) } : current));
  }

  function toggle(option: number) {
    updateRound((round) => {
      const question = round.questions[round.index];
      const picked = round.picks[round.index];
      const next = question.multi
        ? picked.includes(option)
          ? picked.filter((value) => value !== option)
          : [...picked, option]
        : [option];
      return { ...round, picks: round.picks.map((p, i) => (i === round.index ? next : p)) };
    });
  }

  function setOwnWords(text: string) {
    updateRound((round) => ({
      ...round,
      texts: round.texts.map((t, i) => (i === round.index ? text : t)),
    }));
  }

  function goTo(index: number) {
    updateRound((round) => ({ ...round, index }));
  }

  async function redraft() {
    const fresh = roundAnswers(state.round);
    if (fresh.length === 0 || state.current === null) {
      // Nothing said this round: there is nothing to redraft with.
      backToDraft();
      return;
    }
    const request = ++requestRef.current;
    const answers = [...state.answers, ...fresh];
    const base = state.current;
    patch({ step: "redrafting", error: null });
    try {
      const redrafted = await createPathDraft({
        idea: state.idea.trim(),
        answers: answers.slice(-MAX_ANSWERS),
        previous: base,
      });
      if (request !== requestRef.current) return;
      patch({
        step: "sharpened",
        previous: base,
        current: redrafted,
        answers,
        round: null,
      });
    } catch {
      if (request === requestRef.current) patch({ error: "redraft" });
    }
  }

  return {
    state,
    setIdea: (idea: string) => patch({ idea }),
    draft,
    changeIdea: () => {
      requestRef.current += 1;
      patch({ step: "idea", error: null });
    },
    startInterview,
    backToDraft,
    /** From a failed redraft back to the round, answers intact. */
    backToQuestions: () => {
      requestRef.current += 1;
      patch({ step: "interview", error: null });
    },
    toggle,
    setOwnWords,
    goTo,
    redraft,
    backToFirst: () =>
      patch({ step: "draft", current: state.first, previous: null, answers: [], round: null }),
  };
}

export type PathDraftController = ReturnType<typeof usePathDraft>;
