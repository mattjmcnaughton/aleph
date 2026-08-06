import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import type { Proposal } from "../lib/tutor-stream";
import { adminUser, learnerUser } from "../mocks/handlers";
import { ADMIN_MODEL_ALLOWLIST } from "../mocks/models";
import { FRESH_PATH_UNITS, MID_PATH_UNITS, seedPath } from "../mocks/paths";
import { server } from "../mocks/server";
import {
  configureShaping,
  finishShapingStream,
  seedShapingConversation,
  shapingAbortedSendCount,
  shapingClearCount,
  shapingReadCount,
  shapingSendBodies,
} from "../mocks/shaping";
import { tutorReadCount } from "../mocks/tutor";
import { App } from "./app";

// The shaping rail (AL-330, TDD §8/D14, PRD §5.1–§5.3): the rail tree's **third
// mount**, on the path route. Driven end to end through the real router,
// TanStack Query and MSW — including the send endpoint's `text/event-stream`
// body, so the composer's state machine runs over a real stream.
//
// Three rules these tests exist to pin:
//  1. **The entry point is gated twice** — `useFeatureFlag("shaping")` AND the
//     path being `ready` (PRD §5.1: there must be a structure to shape). Neither
//     renders a disabled affordance, and a gated-off surface costs no request.
//  2. **One tree, two CSS presentations** (D14 extends D12 unchanged): sheet
//     below `lg`, docked column at `lg`, decided by classes only. jsdom has no
//     CSS, so asserting on the class list *is* the assertion — nothing in JS may
//     branch on width (no `matchMedia`).
//  3. **The in-lesson rail is a different thread** (W21): opening the shaping
//     rail reads `/shaping/conversation` and never `/conversation`.
//
// And one that is a property of *this route* rather than of the rail: the
// sidebar switcher moves between paths on the **same** route, so nothing
// remounts — see the path-switch block at the bottom.

const PATH_ID = "p2000000-0000-4000-8000-000000000001";
/** A second path, to switch to — the same route, a different `pathId` param. */
const OTHER_PATH_ID = "p2000000-0000-4000-8000-000000000002";

/** A plain learner with the dark `shaping` flag flipped on for them. */
const shapingOnSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...learnerUser, feature_flags: { shaping: true } },
};

/** An admin dogfooding shaping (ADMIN_DEFAULT_FLAGS) — the picker's audience. */
const adminShapingSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: { ...adminUser, feature_flags: { shaping: true } },
};

const PROPOSAL: Proposal = {
  summary: "Adds 2 lessons on `unknown` before Utility Types (≈ 10 min).",
  operations: [
    {
      insert_at_position: 4,
      new_unit: null,
      lessons: [{ title: "`unknown` vs `any`" }, { title: "Narrowing `unknown`" }],
      rationale: "You missed the narrowing check, and Utility Types assumes it.",
      estimated_minutes: 10,
    },
  ],
};

function useSession(session: AuthSession) {
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

function seedReadyPath(): void {
  seedPath({
    id: PATH_ID,
    topic: "TypeScript",
    level: "some_experience",
    units: MID_PATH_UNITS,
  });
}

/** The path the switcher block navigates *to* — a different topic, so the
 *  context chip alone says which conversation is on screen. */
function seedOtherReadyPath(): void {
  seedPath({
    id: OTHER_PATH_ID,
    topic: "Rust ownership",
    level: "new_to_it",
    units: FRESH_PATH_UNITS,
  });
}

async function gotoPath(): Promise<void> {
  window.history.pushState({}, "", `/paths/${PATH_ID}`);
  render(<App />);
  await screen.findByTestId("path-view");
}

/** Open the rail through its floating mark — the only door on a phone. */
async function openRail(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByTestId("shaping-rail-mark"));
  return screen.findByTestId("shaping-rail");
}

/**
 * The preamble almost every case below shares: a session with the dark flag
 * flipped on, a `ready` path to shape, the route rendered, and the rail opened
 * through its mark. Anything a case wants the fakes to already hold — a seeded
 * thread, a configured stream — is set before this, since none of it depends on
 * the render.
 */
async function openShapingRail(session: AuthSession = shapingOnSession): Promise<void> {
  useSession(session);
  seedReadyPath();
  await gotoPath();
  await openRail();
}

/** The switcher row for a path — its own query, so it can lag the route. */
async function sidebarPathItem(pathId: string): Promise<HTMLElement> {
  return waitFor(() => {
    const item = screen
      .getAllByTestId("sidebar-path-item")
      .find((el) => el.getAttribute("data-path-id") === pathId);
    if (!item) throw new Error(`no sidebar-path-item for path ${pathId}`);
    return item;
  });
}

function composer(): HTMLTextAreaElement {
  return screen.getByTestId("shaping-rail-input") as HTMLTextAreaElement;
}

function ask(question: string): void {
  fireEvent.change(composer(), { target: { value: question } });
  fireEvent.click(screen.getByTestId("shaping-rail-send"));
}

function messages(): HTMLElement[] {
  return screen.queryAllByTestId("shaping-rail-message");
}

describe("Shaping rail — entry point gating (PRD §5.1)", () => {
  it("[AL-330] renders no entry point at all when the `shaping` flag is off", async () => {
    // The default fake learner ships dark: no `shaping` key at all resolves off.
    seedReadyPath();
    await gotoPath();
    await screen.findByTestId("path-rail");

    expect(screen.queryByTestId("shaping-rail-mark")).toBeNull();
    expect(screen.queryByTestId("shaping-rail")).toBeNull();
    expect(screen.queryByTestId("shaping-rail-column")).toBeNull();
    // Dark means dark: a gated-off surface costs no request at all.
    expect(shapingReadCount()).toBe(0);
  });

  it("[AL-330] shows the mark on a `ready` path when the flag is on", async () => {
    useSession(shapingOnSession);
    seedReadyPath();
    await gotoPath();

    expect(await screen.findByTestId("shaping-rail-mark")).toBeTruthy();
    // Closed by default: no rail until the learner asks for one.
    expect(screen.queryByTestId("shaping-rail")).toBeNull();
  });

  it("[AL-330] renders no entry point while the outline is still generating", async () => {
    // There is no structure to shape yet (PRD §5.1); the server says the same
    // with a `409`, so the UI's absence is a convenience, not the rule.
    useSession(shapingOnSession);
    seedPath({
      id: PATH_ID,
      topic: "TypeScript",
      level: "some_experience",
      resolution: "generating",
      pollsRemaining: 50,
    });
    await gotoPath();

    await screen.findByTestId("path-generating");
    expect(screen.queryByTestId("shaping-rail-mark")).toBeNull();
    expect(shapingReadCount()).toBe(0);
  });

  it("[AL-330] renders no entry point on a refused path", async () => {
    useSession(shapingOnSession);
    seedPath({
      id: PATH_ID,
      topic: "TypeScript",
      level: "some_experience",
      resolution: "refused",
    });
    await gotoPath();

    await screen.findByTestId("path-refused");
    expect(screen.queryByTestId("shaping-rail-mark")).toBeNull();
    expect(shapingReadCount()).toBe(0);
  });

  it("[AL-330] renders no entry point on a failed path", async () => {
    useSession(shapingOnSession);
    seedPath({
      id: PATH_ID,
      topic: "TypeScript",
      level: "some_experience",
      resolution: "failed",
    });
    await gotoPath();

    await screen.findByTestId("path-failed");
    expect(screen.queryByTestId("shaping-rail-mark")).toBeNull();
    expect(shapingReadCount()).toBe(0);
  });
});

describe("Shaping rail — one tree, two CSS presentations (D14)", () => {
  it("[AL-330] mounts a single rail carrying both the sheet and the docked-column classes", async () => {
    await openShapingRail();

    const column = screen.getByTestId("shaping-rail-column");
    // Below `lg`: a bottom sheet over the path, capped so the path shows behind.
    expect(column.className).toMatch(/fixed/);
    expect(column.className).toMatch(/bottom-0/);
    // At `lg`: `lg:sticky` beneath the app header, not a plain flex sibling
    // stretched to the path view's full height (which is what used to strand
    // this composer off the bottom of the screen too).
    expect(column.className).toMatch(/\blg:sticky\b/);
    expect(column.className).not.toMatch(/\blg:static\b/);
    expect(column.className).toMatch(/lg:top-\[var\(--app-header-h\)\]/);
    expect(column.className).toMatch(/lg:h-\[calc\(100dvh-var\(--app-header-h\)\)\]/);
    expect(column.className).toMatch(/lg:w-\[400px\]/);
    // Exactly one rail in the tree at any width — never a second mount.
    expect(screen.getAllByTestId("shaping-rail")).toHaveLength(1);
  });

  it("[AL-330] collapse closes the rail and brings the mark back", async () => {
    await openShapingRail();

    fireEvent.click(screen.getByTestId("shaping-rail-collapse"));

    await waitFor(() => expect(screen.queryByTestId("shaping-rail")).toBeNull());
    expect(screen.queryByTestId("shaping-rail-column")).toBeNull();
    expect(screen.getByTestId("shaping-rail-mark")).toBeTruthy();
  });
});

describe("Shaping rail — the conversation surface", () => {
  it("[AL-330] names the scope in the context chip as `Shaping · {topic}`", async () => {
    await openShapingRail();

    const chip = screen.getByTestId("shaping-rail-context-chip");
    expect(chip.textContent).toContain("Shaping");
    expect(chip.textContent).toContain("TypeScript");
  });

  it("[AL-330] empty-states what shaping can and cannot do, and offers the suggestions", async () => {
    await openShapingRail();

    const empty = await screen.findByTestId("shaping-rail-empty");
    // The vocabulary boundary, stated up front (PRD §5.1): what it can do…
    expect(empty.textContent).toMatch(/add/i);
    expect(empty.textContent).toMatch(/revise/i);
    // …and what it cannot, so an out-of-vocabulary ask is not a surprise.
    expect(empty.textContent).toMatch(/remove/i);
    expect(empty.textContent).toMatch(/reorder/i);

    expect(screen.getAllByTestId("shaping-rail-suggestion")).toHaveLength(4);
  });

  it("[AL-330] reads the shaping thread, never the in-lesson one (W21)", async () => {
    seedShapingConversation(PATH_ID, [
      { role: "learner", content: "What's missing?" },
      { role: "tutor", content: "Nothing on `unknown`." },
    ]);
    await openShapingRail();

    await waitFor(() => expect(messages()).toHaveLength(2));
    expect(shapingReadCount()).toBeGreaterThan(0);
    // The 2A thread is a different conversation on a different route: opening
    // this surface must not touch it.
    expect(tutorReadCount()).toBe(0);
  });

  it("[AL-330] sends a suggestion as `source: suggestion`", async () => {
    await openShapingRail();

    const suggestions = await screen.findAllByTestId("shaping-rail-suggestion");
    const whatsMissing = suggestions.find((button) =>
      (button.textContent ?? "").includes("What's missing?"),
    );
    fireEvent.click(whatsMissing as HTMLElement);

    await waitFor(() => expect(shapingSendBodies()).toHaveLength(1));
    expect(shapingSendBodies()[0]).toEqual({
      content: "What's missing?",
      source: "suggestion",
    });
  });

  it("[AL-330] the `Add practice on…` suggestion prefills the composer instead of sending", async () => {
    // PRD §5.3 marks this one *(opens composer prefilled)*: it is the start of a
    // sentence, not an ask — sending it as-is would be a question with no object.
    await openShapingRail();

    const suggestions = await screen.findAllByTestId("shaping-rail-suggestion");
    const addPractice = suggestions.find((button) =>
      (button.textContent ?? "").startsWith("Add practice on"),
    );
    fireEvent.click(addPractice as HTMLElement);

    expect(composer().value).toMatch(/^Add practice on/);
    expect(shapingSendBodies()).toHaveLength(0);
  });

  it("[AL-330] appends the settled turn to the thread", async () => {
    configureShaping({ replyDeltas: ["Two short lessons ", "would close that gap."] });
    await openShapingRail();

    ask("What's missing?");

    await waitFor(() => expect(messages()).toHaveLength(2));
    expect(messages()[0].dataset.role).toBe("learner");
    expect(messages()[1].dataset.role).toBe("tutor");
    expect(messages()[1].textContent).toContain("Two short lessons would close that gap.");
    expect(shapingSendBodies()[0]).toEqual({ content: "What's missing?", source: "typed" });
  });
});

describe("Shaping rail — the live turn (PRD §5.6)", () => {
  it("[AL-330] echoes the question into the thread on send, before any reply exists", async () => {
    // A turn is persisted whole or not at all, so the cached thread cannot show
    // the question until the whole reply has landed. The rail shows it in the
    // meantime rather than leaving the learner staring at an empty composer.
    configureShaping({ hang: true, replyDeltas: [] });
    await openShapingRail();

    ask("Add practice on narrowing");

    const echo = await screen.findByTestId("shaping-rail-pending");
    expect(echo.textContent).toMatch(/add practice on narrowing/i);
    expect(composer().value).toBe("");
    // Nothing was persisted: the echo is the client's, not the thread's.
    expect(messages()).toHaveLength(0);
    expect(screen.queryByTestId("shaping-rail-empty")).toBeNull();
  });

  it("[AL-330] says the tutor is thinking until the first token, then gets out of the way", async () => {
    configureShaping({ hang: true, replyDeltas: [] });
    await openShapingRail();

    ask("What's missing?");

    const thinking = await screen.findByTestId("shaping-rail-thinking");
    expect(thinking.textContent).toMatch(/thinking/i);
    // Nothing pretends to be a reply while there is no reply text.
    expect(screen.queryByTestId("shaping-rail-streaming")).toBeNull();

    configureShaping({ hang: false });
    finishShapingStream();
    await waitFor(() => expect(screen.queryByTestId("shaping-rail-thinking")).toBeNull());
  });

  it("[AL-330] shows the reply under the question, and hands both over on settle", async () => {
    configureShaping({ hang: true, replyDeltas: ["Two short lessons ", "would close that gap."] });
    await openShapingRail();

    // Not one of the suggestion labels: those reappear beside the composer once
    // the turn settles, and would count as a second copy of the question.
    ask("Where are the gaps in this path?");

    const streaming = await screen.findByTestId("shaping-rail-streaming");
    await waitFor(() => expect(streaming.textContent).toMatch(/two short lessons/i));
    expect(screen.queryByTestId("shaping-rail-thinking")).toBeNull();
    // The question stays put underneath its own reply for the whole turn.
    expect(screen.getByTestId("shaping-rail-pending").textContent).toMatch(/where are the gaps/i);

    finishShapingStream();

    // Handed over, not duplicated: the settled pair is the only copy left.
    await waitFor(() => expect(messages()).toHaveLength(2));
    expect(screen.queryByTestId("shaping-rail-pending")).toBeNull();
    expect(screen.queryByTestId("shaping-rail-streaming")).toBeNull();
    expect(screen.getAllByText(/where are the gaps in this path\?/i)).toHaveLength(1);
  });

  it("[AL-330] stop and failure take the echo back, leaving one copy in the composer", async () => {
    configureShaping({ hang: true, replyDeltas: [] });
    await openShapingRail();

    ask("Add practice on narrowing");
    fireEvent.click(await screen.findByTestId("shaping-rail-stop"));

    await waitFor(() => expect(composer().value).toBe("Add practice on narrowing"));
    expect(screen.queryByTestId("shaping-rail-pending")).toBeNull();
    expect(screen.queryByTestId("shaping-rail-thinking")).toBeNull();

    configureShaping({
      hang: false,
      failWith: { code: "upstream_error", message: "The tutor is unavailable right now." },
    });
    fireEvent.click(screen.getByTestId("shaping-rail-send"));

    await screen.findByTestId("shaping-rail-error");
    // "Your question is still here" points at the composer, and it would not be
    // true twice over.
    expect(composer().value).toBe("Add practice on narrowing");
    expect(screen.queryByTestId("shaping-rail-pending")).toBeNull();
  });
});

describe("Shaping rail — composer state machine (PRD §5.6)", () => {
  it("[AL-330] disables the composer in flight and offers stop instead of send", async () => {
    configureShaping({ hang: true });
    await openShapingRail();

    ask("Add practice on narrowing");

    await screen.findByTestId("shaping-rail-stop");
    expect(composer().disabled).toBe(true);
    expect(screen.queryByTestId("shaping-rail-send")).toBeNull();
    // Suggestions are withdrawn mid-stream — tapping one could only queue a
    // send the server would reject.
    expect(screen.queryAllByTestId("shaping-rail-suggestion")).toHaveLength(0);
  });

  it("[AL-330] stop aborts the request and restores the question to the composer", async () => {
    configureShaping({ hang: true });
    await openShapingRail();

    // A delta, not an absolute: `tests/setup.ts` resets the fakes *before*
    // `cleanup()`, so a previous case's unmount-abort can land after the reset.
    const abortsBefore = shapingAbortedSendCount();
    ask("Add practice on narrowing");
    fireEvent.click(await screen.findByTestId("shaping-rail-stop"));

    await waitFor(() => expect(composer().value).toBe("Add practice on narrowing"));
    expect(composer().disabled).toBe(false);
    expect(shapingAbortedSendCount()).toBe(abortsBefore + 1);

    // A late `done` frame must change nothing: the client hung up, so the turn
    // was never persisted and nothing may be appended after the fact.
    finishShapingStream();
    await waitFor(() => expect(messages()).toHaveLength(0));
  });

  it("[AL-330] a failed reply keeps the question, explains, and retries", async () => {
    configureShaping({
      failWith: { code: "upstream_error", message: "The tutor is unavailable right now." },
    });
    await openShapingRail();

    ask("What's missing?");

    const error = await screen.findByTestId("shaping-rail-error");
    // The server words its own failures for a learner; the rail uses them verbatim.
    expect(error.textContent).toContain("The tutor is unavailable right now.");
    expect(composer().value).toBe("What's missing?");
    expect(messages()).toHaveLength(0);

    // Retry re-sends the same question — the client owns it, the server never had it.
    configureShaping({ failWith: null, replyDeltas: ["Here's the gap."] });
    fireEvent.click(screen.getByTestId("shaping-rail-retry"));

    await waitFor(() => expect(messages()).toHaveLength(2));
    expect(shapingSendBodies()).toHaveLength(2);
    expect(shapingSendBodies()[1].content).toBe("What's missing?");
  });

  it("[AL-330] words a pre-stream `409` as an ordinary failure — nothing streamed", async () => {
    // Admission failures are plain JSON envelopes, never `error` frames: SSE
    // starts only once the turn is admitted (§5.5). A non-`ready` path is the
    // server backstop behind the hidden entry point (PRD §5.1); a reply already
    // in flight on this conversation (D11) arrives identically. Either way it
    // reaches the rail as an `ApiError`, and the rail says what the server said.
    configureShaping({
      preStreamError: {
        status: 409,
        code: "conflict",
        message: "this path is not ready to shape yet",
      },
    });
    await openShapingRail();

    ask("Add practice on narrowing");

    const error = await screen.findByTestId("shaping-rail-error");
    expect(error.textContent).toContain("this path is not ready to shape yet");
    // No stream ever opened, so there is nothing to have half-rendered.
    expect(screen.queryByTestId("shaping-rail-streaming")).toBeNull();
    expect(messages()).toHaveLength(0);
    // The question is the client's, as it is for every other failure.
    expect(composer().value).toBe("Add practice on narrowing");
    expect(screen.getByTestId("shaping-rail-retry")).toBeTruthy();
  });

  it("[AL-330] refuses to send an empty question", async () => {
    await openShapingRail();

    fireEvent.change(composer(), { target: { value: "   " } });
    expect((screen.getByTestId("shaping-rail-send") as HTMLButtonElement).disabled).toBe(true);
    expect(shapingSendBodies()).toHaveLength(0);
  });
});

describe("Shaping rail — Shift+Enter sends", () => {
  it("[AL-330] Shift+Enter sends the draft as typed content", async () => {
    await openShapingRail();

    fireEvent.change(composer(), { target: { value: "Add practice on narrowing" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: true });

    await waitFor(() => expect(shapingSendBodies()).toHaveLength(1));
    expect(shapingSendBodies()[0]).toEqual({
      content: "Add practice on narrowing",
      source: "typed",
    });
  });

  // A regression guard, not the red half of a red-green pair: with the gesture
  // deleted outright this would still pass. It is here to fail the day someone
  // "corrects" the binding to the conventional Enter-sends.
  it("[AL-330] plain Enter does not send — it is left to insert a newline", async () => {
    await openShapingRail();

    fireEvent.change(composer(), { target: { value: "Still writing this" } });
    fireEvent.keyDown(composer(), { key: "Enter" });

    // A send is async, so asserting "nothing went out" on the next line would
    // pass even with a request in flight. The negative is proved instead by
    // sending a *different* ask afterwards and finding exactly one body: the
    // second one.
    fireEvent.change(composer(), { target: { value: "Sent on purpose" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: true });

    await waitFor(() => expect(shapingSendBodies()).toHaveLength(1));
    expect(shapingSendBodies()[0].content).toBe("Sent on purpose");
  });

  it("[AL-330] Shift+Enter mid-IME-composition does not send", async () => {
    await openShapingRail();

    // Mid-composition the characters are still in the IME's buffer, not in the
    // controlled value — sending here would post the stale prefix and eat the
    // commit chord. Same shape of proof as above.
    fireEvent.change(composer(), { target: { value: "制約" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: true, isComposing: true });

    fireEvent.change(composer(), { target: { value: "制約の練習を追加して" } });
    fireEvent.keyDown(composer(), { key: "Enter", shiftKey: true });

    await waitFor(() => expect(shapingSendBodies()).toHaveLength(1));
    expect(shapingSendBodies()[0].content).toBe("制約の練習を追加して");
  });
});

describe("Shaping rail — proposals on the wire (TDD §5.4)", () => {
  it("[AL-330] keeps a streamed `proposal` on the turn it arrived with", async () => {
    configureShaping({ replyDeltas: ["Here's what I'd add."], proposal: PROPOSAL });
    await openShapingRail();

    ask("Add practice on narrowing");

    await waitFor(() => expect(messages()).toHaveLength(2));
    const reply = messages()[1];
    expect(reply.dataset.proposal).toBe("true");
    // The card's interior is AL-331's; what AL-330 owns is that the payload
    // survives the stream and lands on the message the card reads from.
    const card = screen.getByTestId("shaping-rail-proposal");
    expect(card.textContent).toContain(PROPOSAL.summary);
  });

  it("[AL-330] renders a persisted proposal from a returning thread", async () => {
    seedShapingConversation(PATH_ID, [
      { role: "learner", content: "Add practice on narrowing" },
      { role: "tutor", content: "Here's what I'd add.", proposal: PROPOSAL, resolution: "applied" },
    ]);
    await openShapingRail();

    const card = await screen.findByTestId("shaping-rail-proposal");
    // Resolution comes off the wire derived (TDD §4) — the card never guesses it.
    expect(card.dataset.resolution).toBe("applied");
  });

  it("[AL-330] a reply with no proposal is an ordinary turn (declined edit)", async () => {
    configureShaping({ replyDeltas: ["I can't reorder lessons, but I can add or revise."] });
    await openShapingRail();

    ask("Reorder unit 2");

    await waitFor(() => expect(messages()).toHaveLength(2));
    expect(messages()[1].dataset.proposal).toBeUndefined();
    expect(screen.queryByTestId("shaping-rail-proposal")).toBeNull();
  });
});

describe("Shaping rail — header controls", () => {
  it("[AL-330] new conversation confirms first, then DELETEs the shaping thread", async () => {
    seedShapingConversation(PATH_ID, [{ role: "learner", content: "What's missing?" }]);
    await openShapingRail();
    await waitFor(() => expect(messages()).toHaveLength(1));

    fireEvent.click(screen.getByTestId("shaping-rail-new-conversation"));
    // Destructive and not undoable: it must never sit under one tap.
    expect(shapingClearCount()).toBe(0);
    fireEvent.click(screen.getByTestId("shaping-rail-new-conversation-confirm"));

    await waitFor(() => expect(shapingClearCount()).toBe(1));
    await waitFor(() => expect(messages()).toHaveLength(0));
  });

  it("[AL-330] cancelling new conversation clears nothing", async () => {
    seedShapingConversation(PATH_ID, [{ role: "learner", content: "What's missing?" }]);
    await openShapingRail();

    fireEvent.click(screen.getByTestId("shaping-rail-new-conversation"));
    fireEvent.click(screen.getByTestId("shaping-rail-new-conversation-cancel"));

    expect(shapingClearCount()).toBe(0);
    await waitFor(() => expect(messages()).toHaveLength(1));
  });

  it("[AL-330] offers a change-history button — the record is on the path, not the thread", async () => {
    await openShapingRail();

    // AL-331 hangs the read-only sheet off this control; AL-330 owns the header.
    expect(screen.getByTestId("shaping-rail-change-history")).toBeTruthy();
  });

  it("[AL-330] renders no model picker for a non-admin", async () => {
    await openShapingRail();

    expect(screen.queryByTestId("shaping-rail-model-picker")).toBeNull();
  });

  it("[AL-330] rides an admin's per-message model override on the send", async () => {
    await openShapingRail(adminShapingSession);

    const picker = screen.getByTestId("shaping-rail-model-picker") as HTMLSelectElement;
    fireEvent.change(picker, { target: { value: ADMIN_MODEL_ALLOWLIST[0] } });
    ask("What's missing?");

    await waitFor(() => expect(shapingSendBodies()).toHaveLength(1));
    expect(shapingSendBodies()[0]).toEqual({
      content: "What's missing?",
      source: "typed",
      model: ADMIN_MODEL_ALLOWLIST[0],
    });
  });

  it("[AL-330] omits the `model` key entirely when the picker is on the server default", async () => {
    // Sending the key at all is a `403` for a non-admin, and a no-op override
    // for an admin — absent, never null (docs/api.md).
    await openShapingRail(adminShapingSession);
    ask("What's missing?");

    await waitFor(() => expect(shapingSendBodies()).toHaveLength(1));
    expect(shapingSendBodies()[0]).not.toHaveProperty("model");
  });
});

// A path switch through the sidebar goes `/paths/A` -> `/paths/B` on the **same
// route**: only the `pathId` param changes, so TanStack Router re-renders
// `PathView` rather than remounting it. Nothing unmounts, so nothing resets by
// itself — every case here would pass trivially on a remount, and every one of
// them fails without the hook resetting on `pathId`.
//
// The rule they pin: **a different path is a different conversation.** The rail
// returns to rest (closed, empty, idle, no override) and the abandoned stream is
// hung up on, exactly as leaving the route entirely would.
describe("Shaping rail — a path switch is a different conversation", () => {
  /** Switch to `OTHER_PATH_ID` through the switcher and reopen the rail there. */
  async function switchPathAndReopen(): Promise<void> {
    fireEvent.click(await sidebarPathItem(OTHER_PATH_ID));
    // The rail closed itself on the way over; the mark is the way back in.
    fireEvent.click(await screen.findByTestId("shaping-rail-mark"));
    await screen.findByTestId("shaping-rail");
    await waitFor(() =>
      expect(screen.getByTestId("shaping-rail-context-chip").textContent).toContain(
        "Rust ownership",
      ),
    );
  }

  it("[AL-330] hangs up the in-flight reply and shows none of it on the next path", async () => {
    seedOtherReadyPath();
    configureShaping({ hang: true, replyDeltas: ["Two short lessons "] });
    await openShapingRail();

    const abortsBefore = shapingAbortedSendCount();
    ask("Add practice on narrowing");
    await screen.findByTestId("shaping-rail-stop");
    await waitFor(() =>
      expect(screen.getByTestId("shaping-rail-streaming").textContent).toContain(
        "Two short lessons",
      ),
    );

    fireEvent.click(await sidebarPathItem(OTHER_PATH_ID));

    // The same discard an unmount does: the learner walked away from the turn.
    await waitFor(() => expect(shapingAbortedSendCount()).toBe(abortsBefore + 1));
    // The rail is closed on arrival — the mark is what is offered instead.
    await screen.findByTestId("shaping-rail-mark");
    expect(screen.queryByTestId("shaping-rail")).toBeNull();

    fireEvent.click(screen.getByTestId("shaping-rail-mark"));
    await screen.findByTestId("shaping-rail");
    // No live bubble carried over, and the composer is empty and ready.
    expect(screen.queryByTestId("shaping-rail-streaming")).toBeNull();
    expect(composer().value).toBe("");
    expect(composer().disabled).toBe(false);
    await screen.findByTestId("shaping-rail-empty");

    // And a late `done` for the abandoned stream appends nothing, on either
    // path's thread.
    finishShapingStream();
    await waitFor(() => expect(messages()).toHaveLength(0));
  });

  it("[AL-330] carries no failed turn, question or model override across the switch", async () => {
    seedOtherReadyPath();
    configureShaping({
      failWith: { code: "upstream_error", message: "The tutor is unavailable right now." },
    });
    await openShapingRail(adminShapingSession);

    const picker = screen.getByTestId("shaping-rail-model-picker") as HTMLSelectElement;
    fireEvent.change(picker, { target: { value: ADMIN_MODEL_ALLOWLIST[0] } });
    ask("What's missing?");
    await screen.findByTestId("shaping-rail-error");
    // Mid-confirmation too: the destructive prompt is about *this* thread.
    fireEvent.click(screen.getByTestId("shaping-rail-new-conversation"));
    await screen.findByTestId("shaping-rail-new-conversation-confirm");

    configureShaping({ failWith: null, replyDeltas: ["Rust has a different gap."] });
    await switchPathAndReopen();

    // The other path's failure is not this path's failure.
    expect(screen.queryByTestId("shaping-rail-error")).toBeNull();
    expect(composer().value).toBe("");
    expect(screen.queryByTestId("shaping-rail-new-conversation-confirm")).toBeNull();
    expect(screen.getByTestId("shaping-rail-new-conversation")).toBeTruthy();
    // The admin's per-message override is a choice about one ask, not a mode.
    expect((screen.getByTestId("shaping-rail-model-picker") as HTMLSelectElement).value).toBe("");

    ask("What's missing here?");
    await waitFor(() => expect(shapingSendBodies()).toHaveLength(2));
    expect(shapingSendBodies()[1]).not.toHaveProperty("model");
    await waitFor(() => expect(messages()).toHaveLength(2));
  });

  it("[AL-330] shows the arriving path's own thread, never the one left behind", async () => {
    seedOtherReadyPath();
    seedShapingConversation(PATH_ID, [
      { role: "learner", content: "What's missing?" },
      { role: "tutor", content: "Nothing on `unknown`." },
    ]);
    await openShapingRail();
    await waitFor(() => expect(messages()).toHaveLength(2));

    await switchPathAndReopen();

    // Threads are cached per path, so this one has never been shaped at all.
    await screen.findByTestId("shaping-rail-empty");
    expect(messages()).toHaveLength(0);
  });
});
