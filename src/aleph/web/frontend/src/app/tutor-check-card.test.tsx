import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import type { TutorCheck } from "../lib/tutor-stream";
import { learnerUser } from "../mocks/handlers";
import { seedLesson } from "../mocks/lessons";
import { seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import {
  configureTutor,
  seedConversation,
  tutorAnswerRequests,
  tutorSendBodies,
} from "../mocks/tutor";
import { App } from "./app";

// The Tutor check card (AL-231, TDD §8, PRD §5.5, mock Turn 1c) — the tutor's
// own question, rendered inside the conversation.
//
// The three rules these tests exist to pin, all of which follow from one design
// decision (`TutorCheckDTO` carries `correct_index` + `explanation` on delivery,
// TDD §6):
//
//  1. **The reveal is local.** Selecting an option grades nothing server-side;
//     the answer is already in the payload the card is holding. So the reveal
//     lands whether the network is there or not, and a failed persist must not
//     take it back.
//  2. **The persist is fire-after.** `POST /messages/{id}/tutor-check-answer`
//     records `answered_index` so a revisited thread renders revealed. It is
//     never on the path to the reveal.
//  3. **A Tutor check is not a Quick check.** It is non-scoring and outside
//     progress (PRD §5.5): the card touches no lesson query, records no
//     Attempt, and the copy says so in words.

const PATH_ID = "p1000000-0000-4000-8000-000000000001";
const LESSON_ID = "les-tutor";
const CHECK_MESSAGE_ID = "msg-with-check";

/** A plain learner with the dark flag flipped on for them (a per-user override). */
const flagOnSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { tutor: true } },
};

const CHECK: TutorCheck = {
  stem: "What does K extends keyof T guarantee?",
  options: [
    "That T has at least one key.",
    "That K is one of T's own key names.",
    "That K is a string.",
  ],
  correct_index: 1,
  explanation: "That is what lets the return type be **T[K]** rather than unknown.",
  answered_index: null,
};

function useSession(session: AuthSession) {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

/** A ready lesson with a thread whose tutor message posed `check`. */
function seedThreadWithCheck(check: TutorCheck) {
  seedPath({ id: PATH_ID, topic: "TypeScript", level: "some_experience" });
  seedLesson({ id: LESSON_ID, path_id: PATH_ID, title: "Generic constraints" });
  seedConversation(PATH_ID, [
    { role: "learner", content: "Quiz me on this" },
    {
      id: CHECK_MESSAGE_ID,
      role: "tutor",
      content: "One for you — this doesn't count toward the lesson.",
      tutor_check: check,
    },
  ]);
}

async function gotoLesson(id = LESSON_ID): Promise<void> {
  window.history.pushState({}, "", `/lessons/${id}`);
  render(<App />);
  await screen.findByTestId("lesson-view");
}

async function openRail(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByTestId("tutor-rail-mark"));
  return screen.findByTestId("tutor-rail");
}

function options(): HTMLElement[] {
  return screen.getAllByTestId("tutor-rail-check-option");
}

function followUps(): HTMLElement[] {
  return screen.queryAllByTestId("tutor-rail-check-follow-up");
}

describe("Tutor check card — the posed question", () => {
  it("[AL-231] renders stem and options, and says plainly it doesn't count toward the lesson", async () => {
    useSession(flagOnSession);
    seedThreadWithCheck(CHECK);
    await gotoLesson();
    await openRail();

    const card = await screen.findByTestId("tutor-rail-check");
    expect(card.textContent).toMatch(/tutor check/i);
    expect(screen.getByTestId("tutor-rail-check-stem").textContent).toMatch(
      /what does k extends keyof t guarantee/i,
    );
    expect(options().map((option) => option.textContent)).toEqual(CHECK.options);
    // PRD §5.5: non-scoring, and the UI says so — before the learner answers,
    // not after, because that is when it changes how the question reads.
    expect(screen.getByTestId("tutor-rail-check-note").textContent).toMatch(
      /doesn't count toward the lesson/i,
    );
  });

  it("[AL-231] withholds the answer and the explanation until the learner picks", async () => {
    // The payload carries `correct_index` + `explanation` on delivery (TDD §6) —
    // that is what makes the reveal local. It must not make the card a spoiler.
    useSession(flagOnSession);
    seedThreadWithCheck(CHECK);
    await gotoLesson();
    await openRail();

    const card = await screen.findByTestId("tutor-rail-check");
    expect(screen.queryByTestId("tutor-rail-check-explanation")).toBeNull();
    expect(screen.queryByTestId("tutor-rail-check-outcome")).toBeNull();
    expect(card.textContent).not.toMatch(/lets the return type/i);
    expect(options().some((option) => option.getAttribute("data-correct") === "true")).toBe(false);
    // Follow-ups are offered *after* the reveal — there is nothing to ask why about.
    expect(followUps()).toHaveLength(0);
  });
});

describe("Tutor check card — the reveal", () => {
  it("[AL-231] selecting an option reveals correct/incorrect and the explanation locally", async () => {
    useSession(flagOnSession);
    seedThreadWithCheck(CHECK);
    await gotoLesson();
    await openRail();
    await screen.findByTestId("tutor-rail-check");

    fireEvent.click(options()[1]);

    const outcome = await screen.findByTestId("tutor-rail-check-outcome");
    expect(outcome.getAttribute("data-outcome")).toBe("correct");
    const revealed = options();
    expect(revealed[1].getAttribute("data-correct")).toBe("true");
    expect(revealed[1].getAttribute("data-selected")).toBe("true");
    expect(revealed[0].getAttribute("data-correct")).toBe("false");
    // Generated prose goes through the one renderer, always (the security boundary).
    const explanation = screen.getByTestId("tutor-rail-check-explanation");
    expect(explanation.querySelector("strong")?.textContent).toBe("T[K]");
    expect(explanation.textContent).not.toContain("**");
  });

  it("[AL-231] a wrong pick shows the learner's choice and the keyed answer together", async () => {
    useSession(flagOnSession);
    seedThreadWithCheck(CHECK);
    await gotoLesson();
    await openRail();
    await screen.findByTestId("tutor-rail-check");

    fireEvent.click(options()[2]);

    const outcome = await screen.findByTestId("tutor-rail-check-outcome");
    expect(outcome.getAttribute("data-outcome")).toBe("incorrect");
    const revealed = options();
    expect(revealed[2].getAttribute("data-selected")).toBe("true");
    expect(revealed[2].getAttribute("data-correct")).toBe("false");
    expect(revealed[1].getAttribute("data-correct")).toBe("true");
  });

  it("[AL-231] persists the answer fire-after, as {selected_index} against the message id", async () => {
    useSession(flagOnSession);
    seedThreadWithCheck(CHECK);
    await gotoLesson();
    await openRail();
    await screen.findByTestId("tutor-rail-check");

    fireEvent.click(options()[1]);

    // The reveal is on screen before anything is asserted about the request:
    // grading is local, the POST only records.
    await screen.findByTestId("tutor-rail-check-outcome");
    await waitFor(() => expect(tutorAnswerRequests()).toHaveLength(1));
    expect(tutorAnswerRequests()[0]).toEqual({
      message_id: CHECK_MESSAGE_ID,
      selected_index: 1,
    });
  });

  it("[AL-231] reveals while the persist is still in flight — the POST is never awaited", async () => {
    useSession(flagOnSession);
    seedThreadWithCheck(CHECK);
    await gotoLesson();
    await openRail();
    await screen.findByTestId("tutor-rail-check");

    let landed = false;
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post(`${API_V1_BASE}/messages/:messageId/tutor-check-answer`, async () => {
        landed = true;
        await held;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    fireEvent.click(options()[1]);

    // Revealed with the request still open: the feedback came out of the
    // payload the card was already holding, not out of a response.
    await screen.findByTestId("tutor-rail-check-outcome");
    expect(screen.getByTestId("tutor-rail-check-explanation")).toBeTruthy();
    await waitFor(() => expect(landed).toBe(true));
    release();
  });

  it("[AL-231] a failed persist does not un-reveal, and the reveal survives a reopen", async () => {
    // The whole point of the local reveal: the learner's feedback does not
    // depend on the network. A failed persist is silent — there is no learner
    // action that would fix it, and un-revealing would take back an answer they
    // have already read.
    useSession(flagOnSession);
    configureTutor({
      answerError: { status: 500, code: "internal_error", message: "Something went wrong." },
    });
    seedThreadWithCheck(CHECK);
    await gotoLesson();
    await openRail();
    await screen.findByTestId("tutor-rail-check");

    fireEvent.click(options()[1]);

    await screen.findByTestId("tutor-rail-check-outcome");
    await waitFor(() => expect(tutorAnswerRequests()).toHaveLength(1));
    // No error surface of its own, and nothing was rolled back.
    expect(screen.getByTestId("tutor-rail-check-explanation")).toBeTruthy();

    // Optimistic write, so a collapse + reopen reads the answered card back out
    // of the cache rather than showing the question again.
    fireEvent.click(screen.getByTestId("tutor-rail-collapse"));
    await waitFor(() => expect(screen.queryByTestId("tutor-rail")).toBeNull());
    await openRail();

    await screen.findByTestId("tutor-rail-check-outcome");
    expect(options()[1].getAttribute("data-selected")).toBe("true");
  });

  it("[AL-231] locks the answered card — a second tap neither re-reveals nor re-posts", async () => {
    useSession(flagOnSession);
    seedThreadWithCheck(CHECK);
    await gotoLesson();
    await openRail();
    await screen.findByTestId("tutor-rail-check");

    fireEvent.click(options()[1]);
    await screen.findByTestId("tutor-rail-check-outcome");
    fireEvent.click(options()[2]);

    await waitFor(() => expect(tutorAnswerRequests()).toHaveLength(1));
    expect(options()[1].getAttribute("data-selected")).toBe("true");
    expect(options()[2].getAttribute("data-selected")).toBe("false");
  });
});

describe("Tutor check card — a revisited thread", () => {
  it("[AL-231] renders the revealed state from `answered_index`, posting nothing", async () => {
    useSession(flagOnSession);
    seedThreadWithCheck({ ...CHECK, answered_index: 2 });
    await gotoLesson();
    await openRail();

    const outcome = await screen.findByTestId("tutor-rail-check-outcome");
    expect(outcome.getAttribute("data-outcome")).toBe("incorrect");
    expect(options()[2].getAttribute("data-selected")).toBe("true");
    expect(screen.getByTestId("tutor-rail-check-explanation")).toBeTruthy();
    expect(followUps()).toHaveLength(2);
    // Rendering a stored answer is a read. Nothing is re-recorded.
    expect(tutorAnswerRequests()).toHaveLength(0);
  });
});

describe("Tutor check card — follow-ups", () => {
  it("[AL-231] offers 'Another one' and 'Why is that right?' once revealed, through the composer path", async () => {
    useSession(flagOnSession);
    configureTutor({ replyDeltas: ["Here's another."] });
    seedThreadWithCheck(CHECK);
    await gotoLesson();
    await openRail();
    await screen.findByTestId("tutor-rail-check");

    fireEvent.click(options()[1]);
    await screen.findByTestId("tutor-rail-check-outcome");

    expect(followUps().map((button) => button.textContent)).toEqual([
      "Another one",
      "Why is that right?",
    ]);

    fireEvent.click(followUps()[0]);

    // The ordinary send: same endpoint, same body shape, same appended turn as
    // a typed question or a suggestion. No second transport.
    await waitFor(() => expect(tutorSendBodies()).toHaveLength(1));
    expect(tutorSendBodies()[0]).toEqual({
      lesson_id: LESSON_ID,
      content: "Another one",
      source: "suggestion",
    });
    await waitFor(() => expect(screen.getAllByTestId("tutor-rail-message")).toHaveLength(4));
  });

  it("[AL-231] 'Why is that right?' sends its own prefilled question", async () => {
    useSession(flagOnSession);
    seedThreadWithCheck({ ...CHECK, answered_index: 1 });
    await gotoLesson();
    await openRail();
    await screen.findByTestId("tutor-rail-check-outcome");

    fireEvent.click(followUps()[1]);

    await waitFor(() => expect(tutorSendBodies()).toHaveLength(1));
    expect(tutorSendBodies()[0].content).toBe("Why is that right?");
    expect(tutorSendBodies()[0].source).toBe("suggestion");
  });
});

describe("Tutor check card — a streamed check", () => {
  it("[AL-231] renders and answers a check that arrived on the reply stream", async () => {
    // Same card, same message id, whether the check came from `GET /conversation`
    // or from the stream's `tutor_check` event — the rail puts both on the
    // cached tutor message (AL-230), and the card only ever reads it from there.
    useSession(flagOnSession);
    configureTutor({
      replyDeltas: ["One for you — this doesn't count toward the lesson."],
      check: CHECK,
    });
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "some_experience" });
    seedLesson({ id: LESSON_ID, path_id: PATH_ID, title: "Generic constraints" });
    await gotoLesson();
    await openRail();

    fireEvent.click(screen.getAllByTestId("tutor-rail-suggestion")[2]);

    await screen.findByTestId("tutor-rail-check");
    fireEvent.click(options()[1]);

    await screen.findByTestId("tutor-rail-check-outcome");
    await waitFor(() => expect(tutorAnswerRequests()).toHaveLength(1));
    // `tutor-1` is the fake's id for the first settled turn's tutor message.
    expect(tutorAnswerRequests()[0]).toEqual({ message_id: "tutor-1", selected_index: 1 });
  });
});

describe("Tutor check card — no Quick check state, anywhere", () => {
  it("[AL-231] answering records no Attempt and refetches no lesson query", async () => {
    useSession(flagOnSession);
    seedThreadWithCheck(CHECK);
    await gotoLesson();
    await screen.findByTestId("quick-check-stem");
    await openRail();
    await screen.findByTestId("tutor-rail-check");

    // Every request from here on is watched. A Tutor check is non-scoring and
    // outside progress (PRD §5.5): no Attempt, no complete, and not even a
    // lesson read — nothing about the lesson changed, so nothing invalidates.
    const lessonRequests: string[] = [];
    const watch = ({ request }: { request: Request }) => {
      if (new URL(request.url).pathname.includes("/lessons/")) {
        lessonRequests.push(`${request.method} ${new URL(request.url).pathname}`);
      }
    };
    server.events.on("request:start", watch);

    try {
      fireEvent.click(options()[1]);
      await screen.findByTestId("tutor-rail-check-outcome");
      await waitFor(() => expect(tutorAnswerRequests()).toHaveLength(1));
      expect(lessonRequests).toEqual([]);
    } finally {
      server.events.removeListener("request:start", watch);
    }

    // And the lesson's own Quick check is exactly where the learner left it:
    // unanswered, still submittable, with no reveal of its keyed answer.
    expect(screen.getByTestId("quick-check-submit")).toBeTruthy();
    expect(screen.queryByTestId("outcome-reveal")).toBeNull();
  });
});
