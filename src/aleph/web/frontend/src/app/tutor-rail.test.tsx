import { notifyManager } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { adminSession, learnerUser } from "../mocks/handlers";
import { seedLesson } from "../mocks/lessons";
import { ADMIN_MODEL_ALLOWLIST } from "../mocks/models";
import { seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import {
  configureTutor,
  finishTutorStream,
  seedConversation,
  tutorAbortedSendCount,
  tutorClearCount,
  tutorReadCount,
  tutorSendBodies,
} from "../mocks/tutor";
import { App } from "./app";

// The rail (AL-230, TDD §8/D12, PRD §5.1–§5.3/§5.6–§5.8): the tutor surface on
// the lesson route. Driven end to end through the real router, TanStack Query
// and MSW — including the send endpoint's `text/event-stream` body, so the
// composer's state machine is exercised over a real stream rather than a stub.
//
// Two rules these tests exist to pin:
//  1. **The entry point is gated twice** — `useFeatureFlag("tutor")` AND the
//     lesson having generated content. Neither renders a disabled affordance.
//  2. **One tree, two CSS presentations.** Open/closed is plain shared JS state;
//     sheet-vs-docked is classes only. jsdom has no CSS, so the assertion is on
//     the class list — which is precisely the point: nothing in JS may branch on
//     width (no `matchMedia`, no conditional rendering).

const PATH_ID = "p1000000-0000-4000-8000-000000000001";
const LESSON_ID = "les-tutor";

/** A plain learner with the dark flag flipped on for them (a per-user override). */
const flagOnSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { tutor: true } },
};

function useSession(session: AuthSession) {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

function seedReadyLesson(overrides: { id?: string; title?: string } = {}) {
  seedPath({ id: PATH_ID, topic: "TypeScript", level: "some_experience" });
  seedLesson({
    id: overrides.id ?? LESSON_ID,
    path_id: PATH_ID,
    title: overrides.title ?? "Generic constraints",
  });
}

async function gotoLesson(id = LESSON_ID): Promise<void> {
  window.history.pushState({}, "", `/lessons/${id}`);
  render(<App />);
  await screen.findByTestId("lesson-view");
}

/** Open the rail through its floating mark — the only door on a phone. */
async function openRail(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByTestId("tutor-rail-mark"));
  return screen.findByTestId("tutor-rail");
}

function composer(): HTMLTextAreaElement {
  return screen.getByTestId("tutor-rail-input") as HTMLTextAreaElement;
}

function ask(question: string): void {
  fireEvent.change(composer(), { target: { value: question } });
  fireEvent.click(screen.getByTestId("tutor-rail-send"));
}

function messages(): HTMLElement[] {
  return screen.queryAllByTestId("tutor-rail-message");
}

/**
 * Freeze TanStack's own subscriber notifications, and return the release.
 *
 * Used to pin the handover on the `done` frame: the rail clears the live turn
 * there and relies on the cache write beside it being on screen in the same
 * render. That is true because a query's result is read at render time rather
 * than delivered by the notification — which this proves by taking the
 * notification away and finding the settled turn rendered anyway.
 */
function holdCacheNotifications(): () => void {
  const held: Array<() => void> = [];
  notifyManager.setScheduler((callback) => held.push(callback));
  return () => {
    releaseCacheNotifications();
    for (const callback of held) callback();
  };
}

function releaseCacheNotifications(): void {
  notifyManager.setScheduler((callback) => setTimeout(callback, 0));
}

// Never leave a frozen cache behind for the next test, however a test ended.
afterEach(releaseCacheNotifications);

/**
 * Give the thread a scrollable shape. jsdom does no layout, so `scrollHeight`
 * and `clientHeight` are both 0 and every thread reads as "shorter than the
 * rail" — the one case the follow logic has nothing to do in. It does store
 * `scrollTop`, which is the thing being asserted.
 */
function scrollableThread(scrollHeight = 2000, clientHeight = 400): HTMLElement {
  const thread = screen.getByTestId("tutor-rail-messages");
  Object.defineProperty(thread, "scrollHeight", { value: scrollHeight, configurable: true });
  Object.defineProperty(thread, "clientHeight", { value: clientHeight, configurable: true });
  return thread;
}

/** Scroll the thread up to somewhere the learner is reading, and say so. */
function scrollUp(thread: HTMLElement): void {
  thread.scrollTop = 0;
  fireEvent.scroll(thread);
}

describe("Tutor rail — entry point gating", () => {
  it("[AL-230] renders no entry point at all when the `tutor` flag is off", async () => {
    // The default fake learner ships dark (`feature_flags: {tutor: false}`).
    seedReadyLesson();
    await gotoLesson();
    await screen.findByTestId("lesson-read-passage");

    expect(screen.queryByTestId("tutor-rail-mark")).toBeNull();
    expect(screen.queryByTestId("tutor-rail")).toBeNull();
    expect(screen.queryByTestId("tutor-rail-column")).toBeNull();
    // Dark means dark: the gated-off surface costs no request at all, which is
    // what `skipToken` on the conversation query buys. A hidden-but-fetching
    // rail would put Phase 2 load on every Phase 1 learner.
    expect(tutorReadCount()).toBe(0);
  });

  it("[AL-230] shows the mark when the flag is on and the lesson has generated content", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    await gotoLesson();

    expect(await screen.findByTestId("tutor-rail-mark")).toBeTruthy();
    // Closed by default: no rail until the learner asks for one.
    expect(screen.queryByTestId("tutor-rail")).toBeNull();
  });

  it("[AL-230] renders no entry point for a lesson whose content has not generated", async () => {
    useSession(flagOnSession);
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "some_experience" });
    seedLesson({ id: "les-generating", path_id: PATH_ID, pollsRemaining: 50 });
    await gotoLesson("les-generating");

    await screen.findByTestId("lesson-generating");
    expect(screen.queryByTestId("tutor-rail-mark")).toBeNull();
    expect(screen.queryByTestId("tutor-rail")).toBeNull();
  });

  it("[AL-230] renders no entry point for a locked lesson, even once its content generated", async () => {
    // Prefetch (§14) generates ahead of the learner, so `generated` + `locked`
    // is an ordinary state, not a corner. The lesson view refuses to show the
    // passage; the tutor must refuse too, or it would answer questions about a
    // Read passage the learner has not been given.
    useSession(flagOnSession);
    seedPath({ id: PATH_ID, topic: "TypeScript", level: "some_experience" });
    seedLesson({ id: "les-locked", path_id: PATH_ID, unlock_state: "locked" });
    await gotoLesson("les-locked");

    await screen.findByTestId("lesson-locked");
    expect(screen.queryByTestId("tutor-rail-mark")).toBeNull();
    expect(screen.queryByTestId("tutor-rail")).toBeNull();
    expect(tutorReadCount()).toBe(0);
  });
});

describe("Tutor rail — one tree, two CSS presentations (D12)", () => {
  it("[AL-230] mounts a single rail carrying both the sheet and the docked-column classes", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    const column = screen.getByTestId("tutor-rail-column");
    // Below `lg`: a bottom sheet over the lesson. At `lg`: the third column.
    expect(column.className).toMatch(/\bfixed\b/);
    expect(column.className).toMatch(/\binset-x-0\b/);
    expect(column.className).toMatch(/\bbottom-0\b/);
    // At `lg`: `lg:sticky` beneath the app header, not `lg:static` — a plain
    // flex sibling would stretch to the whole lesson's height (easily several
    // thousand pixels) and strand the composer off the bottom of the screen.
    expect(column.className).toMatch(/\blg:sticky\b/);
    expect(column.className).not.toMatch(/\blg:static\b/);
    expect(column.className).toMatch(/lg:top-\[var\(--app-header-h\)\]/);
    expect(column.className).toMatch(/lg:h-\[calc\(100dvh-var\(--app-header-h\)\)\]/);
    expect(column.className).toMatch(/lg:w-\[400px\]/);
    // One tree, not one per presentation.
    expect(screen.getAllByTestId("tutor-rail")).toHaveLength(1);
  });

  it("[AL-260] gives the lesson bottom clearance below `lg` for as long as the rail is open", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    await gotoLesson();
    const main = await screen.findByTestId("lesson-view");

    // Closed: the ordinary page — nothing sits over its tail to make room for.
    expect(main.className).not.toMatch(/pb-\[75vh\]/);

    await openRail();

    // Open: 75vh of bottom padding, the sheet's own cap, so the tail of the
    // lesson (the Quick check's submit, "Mark complete") can be scrolled clear
    // of it. Cancelled at `lg`, where the rail is a column beside `main` and
    // covers nothing.
    expect(main.className).toMatch(/pb-\[75vh\]/);
    expect(main.className).toMatch(/lg:pb-10/);

    // ...and it leaves with the rail, rather than being a permanent hole at the
    // bottom of every lesson.
    fireEvent.click(screen.getByTestId("tutor-rail-collapse"));
    await waitFor(() => expect(screen.queryByTestId("tutor-rail")).toBeNull());
    expect(screen.getByTestId("lesson-view").className).not.toMatch(/pb-\[75vh\]/);
  });

  it("[AL-230] collapse closes the rail and restores the mark (shared open state)", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    fireEvent.click(screen.getByTestId("tutor-rail-collapse"));

    await waitFor(() => expect(screen.queryByTestId("tutor-rail")).toBeNull());
    expect(screen.queryByTestId("tutor-rail-column")).toBeNull();
    expect(screen.getByTestId("tutor-rail-mark")).toBeTruthy();
  });
});

describe("Tutor rail — empty state, chip and suggestions", () => {
  it("[AL-230] names what the tutor can see and offers the suggestion vocabulary", async () => {
    useSession(flagOnSession);
    seedReadyLesson({ title: "Generic constraints" });
    await gotoLesson();
    await openRail();

    const empty = await screen.findByTestId("tutor-rail-empty");
    expect(empty.textContent).toMatch(/read passage/i);
    expect(empty.textContent).toMatch(/quick check/i);
    expect(empty.textContent).toMatch(/generic constraints/i);

    const suggestions = screen.getAllByTestId("tutor-rail-suggestion").map((b) => b.textContent);
    expect(suggestions).toEqual([
      "Explain this simpler",
      "Go deeper",
      "Quiz me on this",
      "Show me a real example",
    ]);
  });

  it("[AL-230] states the scope in the context chip — Reading · lesson title", async () => {
    useSession(flagOnSession);
    seedReadyLesson({ title: "Generic constraints" });
    await gotoLesson();
    await openRail();

    const chip = screen.getByTestId("tutor-rail-context-chip");
    expect(chip.textContent).toMatch(/reading/i);
    expect(chip.textContent).toMatch(/generic constraints/i);
  });

  it("[AL-230] a suggestion sends as typed content tagged `source: suggestion`", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    fireEvent.click(screen.getAllByTestId("tutor-rail-suggestion")[1]);

    await waitFor(() => expect(tutorSendBodies()).toHaveLength(1));
    expect(tutorSendBodies()[0]).toEqual({
      lesson_id: LESSON_ID,
      content: "Go deeper",
      source: "suggestion",
    });
  });
});

describe("Tutor rail — the live turn (PRD §5.6)", () => {
  it("[AL-230] echoes the question into the thread on send, before any reply exists", async () => {
    // The wait is the provider's, and it is seconds long. A turn is persisted
    // whole or not at all (D2), so the cached thread cannot show the question
    // until the whole reply has landed — the rail shows it in the meantime.
    useSession(flagOnSession);
    configureTutor({ hang: true, replyDeltas: [] });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("What breaks if I drop the extends?");

    const echo = await screen.findByTestId("tutor-rail-pending");
    expect(echo.textContent).toMatch(/what breaks if i drop the extends\?/i);
    // The composer really is empty — the question moved into the thread rather
    // than being left behind in it.
    expect(composer().value).toBe("");
    // And nothing was persisted: the echo is the client's, not the thread's.
    expect(messages()).toHaveLength(0);
    expect(screen.queryByTestId("tutor-rail-empty")).toBeNull();
  });

  it("[AL-230] says the tutor is thinking until the first token, then gets out of the way", async () => {
    useSession(flagOnSession);
    configureTutor({ hang: true, replyDeltas: [] });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("Why does the constraint matter?");

    const thinking = await screen.findByTestId("tutor-rail-thinking");
    expect(thinking.textContent).toMatch(/thinking/i);
    // Nothing pretends to be a reply while there is no reply text.
    expect(screen.queryByTestId("tutor-rail-streaming")).toBeNull();
  });

  it("[AL-230] replaces the thinking indicator with the reply as soon as a delta lands", async () => {
    useSession(flagOnSession);
    configureTutor({ hang: true, replyDeltas: ["Think of ", "a constraint"] });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("Why does the constraint matter?");

    const streaming = await screen.findByTestId("tutor-rail-streaming");
    await waitFor(() => expect(streaming.textContent).toMatch(/think of a constraint/i));
    expect(screen.queryByTestId("tutor-rail-thinking")).toBeNull();
    // The question stays put underneath its own reply for the whole turn.
    expect(screen.getByTestId("tutor-rail-pending").textContent).toMatch(
      /why does the constraint matter\?/i,
    );
  });

  it("[AL-230] scrolls down to the question it just echoed", async () => {
    // Showing the question instantly is worth nothing if it is shown below the
    // fold, and the thread has never scrolled itself. Asking is the learner
    // saying they want to be where their question is.
    useSession(flagOnSession);
    configureTutor({ hang: true, replyDeltas: [] });
    seedReadyLesson();
    seedConversation(PATH_ID, [
      { role: "learner", content: "Explain this simpler" },
      { role: "tutor", content: "Think of it as a promise." },
    ]);
    await gotoLesson();
    await openRail();
    await waitFor(() => expect(messages()).toHaveLength(2));

    const thread = scrollableThread();
    scrollUp(thread);

    ask("What breaks if I drop the extends?");

    await screen.findByTestId("tutor-rail-pending");
    expect(thread.scrollTop).toBe(2000);
  });

  it("[AL-230] does not drag a learner who scrolled up mid-reply back down", async () => {
    // Once they scroll away from the tail they are reading, not waiting. A rail
    // that re-pinned them on every delta would take what they are reading off
    // screen several times a second.
    useSession(flagOnSession);
    configureTutor({ hang: true, replyDeltas: ["Think of "] });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("Why does the constraint matter?");
    await screen.findByTestId("tutor-rail-streaming");

    const thread = scrollableThread();
    scrollUp(thread);

    // The turn settles underneath them — the thread grows, and stays put.
    finishTutorStream();
    await waitFor(() => expect(messages()).toHaveLength(2));
    expect(thread.scrollTop).toBe(0);
  });

  it("[AL-230] hands the turn straight over to the cached thread, with nothing in between", async () => {
    // The handover is the one place the live copy and the cached one could come
    // apart: the rail drops the echo and the deltas on the `done` frame, so if
    // the appended pair were not on screen by that same render, every send would
    // blink its whole turn out and back. Holding TanStack's notifications is how
    // that is stated — the settled pair has to be rendered without one.
    useSession(flagOnSession);
    configureTutor({ hang: true, replyDeltas: ["A constraint is a promise."] });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("Explain the constraint");
    await screen.findByTestId("tutor-rail-streaming");

    const release = holdCacheNotifications();
    finishTutorStream();
    await waitFor(() => expect(composer().disabled).toBe(false));

    // The live turn is gone and the settled pair has taken its place, in the one
    // render the `done` frame caused.
    expect(messages()).toHaveLength(2);
    expect(screen.queryByTestId("tutor-rail-pending")).toBeNull();
    expect(screen.queryByTestId("tutor-rail-streaming")).toBeNull();
    expect(screen.queryByTestId("tutor-rail-thinking")).toBeNull();
    // Handed over, not duplicated — one copy of the question on screen.
    expect(screen.getAllByText(/explain the constraint/i)).toHaveLength(1);
    expect(messages()[1].textContent).toMatch(/a constraint is a promise/i);

    release();
    await waitFor(() => expect(messages()).toHaveLength(2));
  });
});

describe("Tutor rail — composer state machine", () => {
  it("[AL-230] disables the composer and offers stop while a reply is in flight", async () => {
    useSession(flagOnSession);
    configureTutor({ hang: true, replyDeltas: ["Think of ", "a constraint"] });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("What breaks if I drop the extends?");

    const streaming = await screen.findByTestId("tutor-rail-streaming");
    await waitFor(() => expect(streaming.textContent).toMatch(/think of a constraint/i));
    expect(composer().disabled).toBe(true);
    expect(screen.getByTestId("tutor-rail-stop")).toBeTruthy();
    expect(screen.queryByTestId("tutor-rail-send")).toBeNull();
  });

  it("[AL-230] a completed stream appends the turn to the thread and clears the composer", async () => {
    useSession(flagOnSession);
    configureTutor({ replyDeltas: ["A constraint is ", "a promise."] });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("Explain the constraint");

    await waitFor(() => expect(messages()).toHaveLength(2));
    expect(messages()[0].getAttribute("data-role")).toBe("learner");
    expect(messages()[0].textContent).toMatch(/explain the constraint/i);
    expect(messages()[1].getAttribute("data-role")).toBe("tutor");
    expect(messages()[1].textContent).toMatch(/a constraint is a promise\./i);
    expect(composer().value).toBe("");
    expect(composer().disabled).toBe(false);
    // The settled turn replaces the streaming bubble — no double render.
    expect(screen.queryByTestId("tutor-rail-streaming")).toBeNull();
  });

  it("[AL-230] stop aborts the reply and restores the question to the composer", async () => {
    useSession(flagOnSession);
    configureTutor({ hang: true, replyDeltas: ["half a sen"] });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("Why does the constraint matter?");
    await screen.findByTestId("tutor-rail-streaming");
    fireEvent.click(screen.getByTestId("tutor-rail-stop"));

    await waitFor(() => expect(composer().value).toBe("Why does the constraint matter?"));
    expect(composer().disabled).toBe(false);
    // Nothing persisted, nothing left on screen: a turn exists whole or not at all.
    expect(screen.queryByTestId("tutor-rail-streaming")).toBeNull();
    expect(messages()).toHaveLength(0);
    // The echoed question goes back to the composer rather than standing over a
    // thread that will never hold it — one copy of it on screen, always.
    expect(screen.queryByTestId("tutor-rail-pending")).toBeNull();
    expect(screen.queryByTestId("tutor-rail-thinking")).toBeNull();
  });

  it("[AL-230] a failed reply discards the partial text, keeps the question, and retries it", async () => {
    useSession(flagOnSession);
    configureTutor({
      replyDeltas: ["half an ans"],
      failWith: { code: "upstream_error", message: "The tutor didn't answer." },
    });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("What breaks without it?");

    const failure = await screen.findByTestId("tutor-rail-error");
    expect(failure.textContent).toMatch(/didn't answer/i);
    // Partial text is discarded and the cache is untouched — a failed reply
    // invalidates nothing, so the thread is still empty.
    expect(screen.queryByTestId("tutor-rail-streaming")).toBeNull();
    expect(messages()).toHaveLength(0);
    // The echo goes with it: "your question is still here" points at the
    // composer, and it would not be true twice over.
    expect(screen.queryByTestId("tutor-rail-pending")).toBeNull();
    // "Your question is still here" is literal: failure restores it to the
    // composer, exactly as stop does, so it can be edited by hand as well as
    // re-sent by the retry button.
    expect(failure.textContent).toMatch(/your question is still here/i);
    expect(composer().value).toBe("What breaks without it?");
    expect(composer().disabled).toBe(false);

    configureTutor({ failWith: null, replyDeltas: ["The body stops compiling."] });
    fireEvent.click(screen.getByTestId("tutor-rail-retry"));

    await waitFor(() => expect(messages()).toHaveLength(2));
    // Retry re-sends the preserved question and takes it back out of the
    // composer — the mirror is a convenience, `pendingRef` is still the source.
    expect(composer().value).toBe("");
    // The client kept its own copy of the question — never the server's.
    expect(tutorSendBodies().map((body) => body.content)).toEqual([
      "What breaks without it?",
      "What breaks without it?",
    ]);
    expect(screen.queryByTestId("tutor-rail-error")).toBeNull();
  });

  it("[AL-230] a pre-stream failure envelope reads as a failed reply, not a dead spinner", async () => {
    useSession(flagOnSession);
    configureTutor({
      preStreamError: { status: 409, code: "conflict", message: "A reply is already in flight." },
    });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("Two at once");

    const failure = await screen.findByTestId("tutor-rail-error");
    expect(failure.textContent).toMatch(/already in flight/i);
    expect(composer().disabled).toBe(false);
    expect(screen.getByTestId("tutor-rail-retry")).toBeTruthy();
  });
});

describe("Tutor rail — Shift+Enter sends", () => {
  it("[AL-260] Shift+Enter sends the draft as typed content", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    fireEvent.change(composer(), { target: { value: "What breaks without the constraint?" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: true });

    await waitFor(() => expect(tutorSendBodies()).toHaveLength(1));
    expect(tutorSendBodies()[0]).toEqual({
      lesson_id: LESSON_ID,
      content: "What breaks without the constraint?",
      source: "typed",
    });
  });

  // A regression guard, not the red half of a red-green pair: with the gesture
  // deleted outright this would still pass. It is here to fail the day someone
  // "corrects" the binding to the conventional Enter-sends, which would take a
  // line break away from every learner mid-question.
  it("[AL-260] plain Enter does not send — it is left to insert a newline", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    fireEvent.change(composer(), { target: { value: "Still writing this" } });
    fireEvent.keyDown(composer(), { key: "Enter" });

    // A send is async, so asserting "nothing went out" on the next line would
    // pass even with a request in flight. The negative is proved instead by
    // sending a *different* question afterwards and finding exactly one body:
    // the second one. (jsdom does not perform the textarea's own newline
    // insertion, so the newline itself is not what this owns.)
    fireEvent.change(composer(), { target: { value: "Sent on purpose" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: true });

    await waitFor(() => expect(tutorSendBodies()).toHaveLength(1));
    expect(tutorSendBodies()[0].content).toBe("Sent on purpose");
  });

  it("[AL-260] Shift+Enter mid-IME-composition does not send", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    // Mid-composition the characters are still in the IME's buffer, so the
    // controlled value holds only what was committed before it — sending here
    // would post that stale prefix *and* eat the commit chord. Same shape of
    // proof as above: the composition must not be the question that arrives.
    fireEvent.change(composer(), { target: { value: "制約" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: true, isComposing: true });

    fireEvent.change(composer(), { target: { value: "制約とは何ですか" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: true });

    await waitFor(() => expect(tutorSendBodies()).toHaveLength(1));
    expect(tutorSendBodies()[0].content).toBe("制約とは何ですか");
  });
});

describe("Tutor rail — the thread", () => {
  it("[AL-230] restores the path's existing thread when the rail opens", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    seedConversation(PATH_ID, [
      { role: "learner", content: "Explain this simpler" },
      { role: "tutor", content: "Think of `<T>` as a blank." },
    ]);
    await gotoLesson();
    await openRail();

    await waitFor(() => expect(messages()).toHaveLength(2));
    expect(screen.queryByTestId("tutor-rail-empty")).toBeNull();
  });

  it("[AL-230] renders tutor replies through the Markdown component", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    seedConversation(PATH_ID, [
      { role: "tutor", content: "A constraint is a **promise** about the blank." },
    ]);
    await gotoLesson();
    await openRail();

    const message = (await screen.findAllByTestId("tutor-rail-message"))[0];
    expect(message.querySelector("strong")?.textContent).toBe("promise");
    expect(message.textContent).not.toContain("**");
  });

  it("[AL-230] carries a posed Tutor check into the cached thread (the AL-231 seam)", async () => {
    useSession(flagOnSession);
    configureTutor({
      replyDeltas: ["One for you — this doesn't count toward the lesson."],
      check: {
        stem: "What does K extends keyof T guarantee?",
        options: ["That T has a key", "That K is one of T's key names", "That K is a string"],
        correct_index: 1,
        explanation: "That is what lets the return type be T[K].",
        answered_index: null,
      },
    });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("Quiz me on this");

    await waitFor(() => expect(messages()).toHaveLength(2));
    expect(messages()[1].getAttribute("data-tutor-check")).toBe("true");

    // Still there after a collapse + reopen: it landed in the query cache, not
    // in the streaming component's transient state.
    fireEvent.click(screen.getByTestId("tutor-rail-collapse"));
    await waitFor(() => expect(screen.queryByTestId("tutor-rail")).toBeNull());
    await openRail();
    await waitFor(() => expect(messages()).toHaveLength(2));
    expect(messages()[1].getAttribute("data-tutor-check")).toBe("true");
  });

  it("[AL-230] new conversation confirms first, then clears the thread", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    seedConversation(PATH_ID, [
      { role: "learner", content: "Explain this simpler" },
      { role: "tutor", content: "Think of it as a promise." },
    ]);
    await gotoLesson();
    await openRail();
    await waitFor(() => expect(messages()).toHaveLength(2));

    fireEvent.click(screen.getByTestId("tutor-rail-new-conversation"));
    // Confirm step first — destructive and not undoable, like deleting a path.
    expect(tutorClearCount()).toBe(0);
    expect(messages()).toHaveLength(2);

    fireEvent.click(screen.getByTestId("tutor-rail-new-conversation-confirm"));

    await waitFor(() => expect(messages()).toHaveLength(0));
    expect(tutorClearCount()).toBe(1);
    expect(await screen.findByTestId("tutor-rail-empty")).toBeTruthy();
  });

  it("[AL-230] new conversation mid-stream ends the reply first, and nothing lands on the cleared thread", async () => {
    // The one action that can reach the state machine while a stream is running.
    // It must route through the same "end the stream" path stop uses: clearing
    // the thread while leaving the stream alive lets the settle path append the
    // abandoned turn onto the conversation the learner just emptied.
    useSession(flagOnSession);
    configureTutor({ hang: true, replyDeltas: ["Think of "] });
    seedReadyLesson();
    seedConversation(PATH_ID, [
      { role: "learner", content: "Explain this simpler" },
      { role: "tutor", content: "Think of it as a promise." },
    ]);
    await gotoLesson();
    await openRail();
    await waitFor(() => expect(messages()).toHaveLength(2));

    ask("And what breaks without it?");
    await screen.findByTestId("tutor-rail-streaming");

    fireEvent.click(screen.getByTestId("tutor-rail-new-conversation"));
    fireEvent.click(screen.getByTestId("tutor-rail-new-conversation-confirm"));

    // Stopped first: the send is hung up on, and only then is the thread cleared.
    await waitFor(() => expect(tutorAbortedSendCount()).toBe(1));
    await waitFor(() => expect(tutorClearCount()).toBe(1));
    await waitFor(() => expect(messages()).toHaveLength(0));
    await screen.findByTestId("tutor-rail-empty");
    expect(screen.queryByTestId("tutor-rail-streaming")).toBeNull();
    // The echoed question is cleared with the thread it was asked into, rather
    // than left standing over the empty rail.
    expect(screen.queryByTestId("tutor-rail-pending")).toBeNull();

    // The composer is usable again, and empty: the learner chose to clear, so
    // the abandoned question is discarded rather than restored the way stop does.
    expect(composer().disabled).toBe(false);
    expect(composer().value).toBe("");

    // Now let the abandoned stream reach the point it would have settled at, and
    // send a fresh question whose round trip is strictly longer than a
    // resurrected append would be. The thread must hold that turn and only it.
    finishTutorStream();
    configureTutor({ hang: false, replyDeltas: ["A fresh start."] });
    ask("Starting over");

    await waitFor(() => expect(messages()).toHaveLength(2));
    expect(messages()[0].textContent).toMatch(/starting over/i);
    expect(messages()[1].textContent).toMatch(/a fresh start\./i);
  });

  it("[AL-230] surfaces a failed clear instead of pretending the thread is gone", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    seedConversation(PATH_ID, [
      { role: "learner", content: "Explain this simpler" },
      { role: "tutor", content: "Think of it as a promise." },
    ]);
    await gotoLesson();
    await openRail();
    await waitFor(() => expect(messages()).toHaveLength(2));

    server.use(
      http.delete(`${API_V1_BASE}/paths/:pathId/conversation`, () =>
        HttpResponse.json(
          { error: { code: "internal_error", message: "Something went wrong." } },
          { status: 500 },
        ),
      ),
    );

    fireEvent.click(screen.getByTestId("tutor-rail-new-conversation"));
    fireEvent.click(screen.getByTestId("tutor-rail-new-conversation-confirm"));

    const failure = await screen.findByTestId("tutor-rail-clear-error");
    expect(failure.textContent).toMatch(/still here/i);
    // The thread is untouched, and this is not the reply's error card — nothing
    // was asked, so there is no question to "Try again" with.
    expect(messages()).toHaveLength(2);
    expect(screen.queryByTestId("tutor-rail-error")).toBeNull();
  });

  it("[AL-230] leaving the lesson mid-stream ends the reply, and nothing lands on the thread", async () => {
    // D2, symmetrically: the server discards a turn whose client hung up, so the
    // client must hang up rather than run the stream to completion and write a
    // turn into the cache of a route the learner has left.
    useSession(flagOnSession);
    configureTutor({ hang: true, replyDeltas: ["Think of "] });
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("Why does the constraint matter?");
    await screen.findByTestId("tutor-rail-streaming");

    fireEvent.click(screen.getByRole("link", { name: "Your paths" }));
    await screen.findByTestId("paths-switcher");

    await waitFor(() => expect(tutorAbortedSendCount()).toBe(1));

    // The stream reaches the point it would have settled at with nobody left to
    // hear it. Coming back to the *same* client — the thread is still cached, so
    // this reads the cache rather than the server — finds nothing appended.
    finishTutorStream();
    window.history.back();
    await screen.findByTestId("lesson-view");
    await openRail();

    await screen.findByTestId("tutor-rail-empty");
    expect(messages()).toHaveLength(0);
    expect(screen.queryByTestId("tutor-rail-streaming")).toBeNull();
  });
});

describe("Tutor rail — admin model picker (§5.3)", () => {
  it("[AL-230] renders no picker for a non-admin learner", async () => {
    useSession(flagOnSession);
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    expect(screen.queryByTestId("tutor-rail-model-picker")).toBeNull();
  });

  it("[AL-230] an admin's pick rides the send body as a per-message override", async () => {
    useSession(adminSession);
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    const picker = (await screen.findByTestId("tutor-rail-model-picker")) as HTMLSelectElement;
    expect([...picker.options].map((option) => option.value)).toEqual([
      "",
      ...ADMIN_MODEL_ALLOWLIST,
    ]);

    fireEvent.change(picker, { target: { value: "anthropic/claude-haiku-4-5" } });
    ask("How does this feel at haiku speed?");

    await waitFor(() => expect(tutorSendBodies()).toHaveLength(1));
    expect(tutorSendBodies()[0].model).toBe("anthropic/claude-haiku-4-5");
  });

  it("[AL-230] omits the model key entirely when the slot is left on the server default", async () => {
    useSession(adminSession);
    seedReadyLesson();
    await gotoLesson();
    await openRail();

    ask("Server default please");

    await waitFor(() => expect(tutorSendBodies()).toHaveLength(1));
    expect("model" in tutorSendBodies()[0]).toBe(false);
  });
});
