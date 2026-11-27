/// <reference types="msw-storybook-addon/csf3" />
import type { Meta, StoryObj } from "@storybook/react-vite";
import { HttpResponse, http } from "msw";
import { expect, userEvent, within } from "storybook/test";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { writeFlow } from "../lib/flow";
import { EXAMPLE_SUMMARY, configureAccount } from "../mocks/account";
import { configureBeats, resetBeats, seedBeat } from "../mocks/beats";
import { EXAMPLE_IDEA, EXAMPLE_QUESTIONS, configurePathDrafts } from "../mocks/path-drafts";
import { configurePaths, resetPaths } from "../mocks/paths";
import { configureProgress, zeroActivity } from "../mocks/progress";
import { AppAt } from "./app-at";
import {
  BROKEN_VISUAL_SOURCE,
  FAILED_LESSON_ID,
  GENERATING_LESSON_ID,
  IDS,
  WORLD_TODAY,
  seedFailedLesson,
  seedGeneratingLesson,
  PARTIAL_REASONING,
  SOLID_REASONING,
  seedPathEdgeStates,
  seedReasoningLesson,
  seedVisualLesson,
  seedWorld,
  sessionHandler,
  signedOutWorldSession,
} from "./world";

// Every screen of the app, rendered by the real router against the seeded world
// (`./world.ts`). Pick a viewport from the toolbar; the default is an iPhone.
// Add a story here for each new route or state a design needs to be judged on.

const meta = {
  title: "Screens",
  component: AppAt,
  beforeEach: () => {
    seedWorld();
  },
} satisfies Meta<typeof AppAt>;

export default meta;
type Story = StoryObj<typeof meta>;

// --- Home ---------------------------------------------------------------------

export const Home: Story = { args: { url: "/" } };

export const HomeWithEdgeStates: Story = {
  name: "Home — generating and failed paths",
  args: { url: "/" },
  beforeEach: () => {
    seedPathEdgeStates();
  },
};

export const HomeFirstRun: Story = {
  name: "Home — first run (no paths)",
  args: { url: "/" },
  // Runs after the meta's `seedWorld()`: empty the world back out.
  beforeEach: () => {
    resetPaths();
    resetBeats();
    configureProgress({
      summary: {
        today: WORLD_TODAY,
        current_streak: 0,
        best_streak: 0,
        completed_today: 0,
        activity: zeroActivity(WORLD_TODAY),
        paths: [],
      },
    });
  },
};

// --- Sign-in & creation ---------------------------------------------------------

export const Login: Story = {
  args: { url: "/login" },
  beforeEach: ({ msw }) => {
    msw.use(sessionHandler(signedOutWorldSession));
  },
};

export const NewPath: Story = {
  name: "New path — the form",
  args: { url: "/new" },
  play: async ({ canvasElement }) => {
    await fillTheFormMyself(within(canvasElement));
  },
};

// --- New path, drafted from an idea (CONTEXT.md: *Path draft*) -------------------
//
// A bare `/new` opens on the idea step. Each story clicks through the real flow
// against the Path drafts fakes (`mocks/path-drafts.ts`), so every frame is the
// shipped component on the mockup's Postgres example.

type Canvas = ReturnType<typeof within>;

async function fillTheFormMyself(canvas: Canvas) {
  await userEvent.click(
    await canvas.findByRole("button", { name: "I'll fill in the form myself" }),
  );
}

async function typeIdea(canvas: Canvas) {
  const idea = await canvas.findByRole("textbox", { name: "Start from an idea" });
  await userEvent.clear(idea);
  // `paste` rather than `type`: one input event, not one per character.
  await userEvent.click(idea);
  await userEvent.paste(EXAMPLE_IDEA);
}

async function toDraft(canvas: Canvas) {
  await typeIdea(canvas);
  await userEvent.click(canvas.getByRole("button", { name: "Draft it for me" }));
  await canvas.findByTestId("path-draft");
}

async function toInterview(canvas: Canvas) {
  await toDraft(canvas);
  await userEvent.click(canvas.getByRole("button", { name: "Sharpen it: ask me a few questions" }));
  await canvas.findByRole("heading", { name: EXAMPLE_QUESTIONS[0].prompt });
}

async function toSharpened(canvas: Canvas) {
  await toInterview(canvas);
  for (const [index, question] of EXAMPLE_QUESTIONS.entries()) {
    await canvas.findByRole("heading", { name: question.prompt });
    const role = question.multi ? "checkbox" : "radio";
    await userEvent.click(canvas.getByRole(role, { name: question.options[index === 1 ? 1 : 0] }));
    if (question.multi) {
      await userEvent.click(canvas.getByRole(role, { name: question.options[1] }));
    }
    const last = index === EXAMPLE_QUESTIONS.length - 1;
    await userEvent.click(canvas.getByRole("button", { name: last ? "Redraft" : "Next" }));
  }
  await canvas.findByText("Aleph's draft, sharpened");
}

export const NewPathIdea: Story = {
  name: "New path — idea",
  args: { url: "/new" },
  play: async ({ canvasElement }) => {
    await typeIdea(within(canvasElement));
  },
};

export const NewPathDrafting: Story = {
  name: "New path — drafting",
  args: { url: "/new" },
  beforeEach: () => {
    configurePathDrafts({ holdDrafts: true });
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await typeIdea(canvas);
    await userEvent.click(canvas.getByRole("button", { name: "Draft it for me" }));
    await expect(await canvas.findByTestId("path-draft-working")).toBeInTheDocument();
  },
};

export const NewPathDraft: Story = {
  name: "New path — Aleph's draft",
  args: { url: "/new" },
  play: async ({ canvasElement }) => {
    await toDraft(within(canvasElement));
  },
};

export const NewPathInterview: Story = {
  name: "New path — interview",
  args: { url: "/new" },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await toInterview(canvas);
    await userEvent.click(canvas.getByRole("radio", { name: EXAMPLE_QUESTIONS[0].options[0] }));
    await userEvent.click(canvas.getByRole("button", { name: "Next" }));
    await canvas.findByRole("heading", { name: EXAMPLE_QUESTIONS[1].prompt });
  },
};

export const NewPathSharpened: Story = {
  name: "New path — sharpened draft",
  args: { url: "/new" },
  play: async ({ canvasElement }) => {
    await toSharpened(within(canvasElement));
  },
};

export const NewPathFilledFromDraft: Story = {
  name: "New path — form filled from the draft",
  args: { url: "/new" },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await toSharpened(canvas);
    await userEvent.click(canvas.getByRole("button", { name: "Use this draft" }));
    await expect(await canvas.findByTestId("draft-filled-notice")).toBeInTheDocument();
  },
};

export const NewPathDraftError: Story = {
  name: "New path — draft failed",
  args: { url: "/new" },
  beforeEach: () => {
    configurePathDrafts({ draftFailures: 1 });
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await typeIdea(canvas);
    await userEvent.click(canvas.getByRole("button", { name: "Draft it for me" }));
    await expect(await canvas.findByTestId("path-draft-error")).toBeInTheDocument();
  },
};

export const NewPathRateLimited: Story = {
  name: "New path — daily limit reached",
  args: { url: "/new?topic=Distributed%20systems" },
  beforeEach: () => {
    configurePaths({ rateLimited: true });
  },
  // The limit is only reported once the learner submits.
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole("button", { name: "Build my path" }));
    await expect(await canvas.findByText(/today's limit/)).toBeInTheDocument();
  },
};

export const NewBeat: Story = { name: "New Beat", args: { url: "/beats/new" } };

// --- Path -----------------------------------------------------------------------

export const PathInProgress: Story = {
  name: "Path — in progress",
  args: { url: `/paths/${IDS.rust}` },
};

export const PathShaping: Story = {
  name: "Path — shaping rail open (pending Proposal)",
  args: { url: `/paths/${IDS.rust}` },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByTestId("shaping-rail-mark"));
    await expect(await canvas.findByTestId("shaping-rail")).toBeInTheDocument();
  },
};

export const PathFresh: Story = { name: "Path — fresh", args: { url: `/paths/${IDS.k8s}` } };

export const PathComplete: Story = {
  name: "Path — complete",
  args: { url: `/paths/${IDS.bayes}` },
};

export const PathGenerating: Story = {
  name: "Path — outline generating",
  args: { url: `/paths/${IDS.generating}` },
  beforeEach: () => {
    seedPathEdgeStates();
  },
};

export const PathFailed: Story = {
  name: "Path — outline failed",
  args: { url: `/paths/${IDS.failed}` },
  beforeEach: () => {
    seedPathEdgeStates();
  },
};

// --- Lesson -----------------------------------------------------------------------

export const Lesson: Story = {
  name: "Lesson — ready (tutor thread seeded)",
  args: { url: `/lessons/${IDS.rustCurrentLesson}` },
};

export const LessonTutorOpen: Story = {
  name: "Lesson — tutor open",
  args: { url: `/lessons/${IDS.rustCurrentLesson}` },
  // The rail opens from its mark: a sheet on a phone, a docked column at `lg`.
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByTestId("tutor-rail-mark"));
    await expect(await canvas.findByTestId("tutor-rail")).toBeInTheDocument();
  },
};

export const LessonTutorWidened: Story = {
  name: "Lesson — tutor widened (desktop)",
  args: { url: `/lessons/${IDS.rustCurrentLesson}` },
  globals: { viewport: { value: "desktop", isRotated: false } },
  // The docked rail is dragged wider from its left edge; the width is
  // remembered, so seeding it is the same state a learner returns to.
  beforeEach: () => {
    localStorage.setItem("aleph:rail-width", "640");
    return () => localStorage.removeItem("aleph:rail-width");
  },
  play: LessonTutorOpen.play,
};

export const LessonRevealed: Story = {
  name: "Lesson — completed, Quick check revealed",
  args: { url: `/lessons/${IDS.rustDoneLesson}` },
};

export const LessonLocked: Story = {
  name: "Lesson — locked",
  args: { url: `/lessons/${IDS.rustLockedLesson}` },
};

export const LessonGenerating: Story = {
  name: "Lesson — generating",
  args: { url: `/lessons/${GENERATING_LESSON_ID}` },
  beforeEach: () => {
    seedGeneratingLesson();
  },
};

export const LessonFailed: Story = {
  name: "Lesson — generation failed",
  args: { url: `/lessons/${FAILED_LESSON_ID}` },
  beforeEach: () => {
    seedFailedLesson();
  },
};

export const LessonWithVisual: Story = {
  name: "Lesson — with visual",
  args: { url: `/lessons/${IDS.bayesVisualLesson}` },
  beforeEach: () => {
    seedVisualLesson();
  },
};

export const LessonVisualFallback: Story = {
  name: "Lesson — visual fallback (alt text)",
  args: { url: `/lessons/${IDS.bayesVisualLesson}` },
  beforeEach: () => {
    seedVisualLesson(BROKEN_VISUAL_SOURCE);
  },
};

async function answerWithReasoning(canvasElement: HTMLElement, reasoning: string) {
  const canvas = within(canvasElement);
  await userEvent.click((await canvas.findAllByTestId("quick-check-option"))[0]);
  await userEvent.type(canvas.getByTestId("quick-check-reasoning"), reasoning);
  return canvas;
}

export const LessonReasoningAsked: Story = {
  name: "Lesson — Quick check asks why",
  args: { url: `/lessons/${IDS.rustCurrentLesson}` },
  beforeEach: () => {
    seedReasoningLesson(PARTIAL_REASONING);
  },
  play: async ({ canvasElement }) => {
    await answerWithReasoning(canvasElement, "The result only comes from x.");
  },
};

export const LessonReasoningPartial: Story = {
  name: "Lesson — reasoning partial, follow-up asked",
  args: { url: `/lessons/${IDS.rustCurrentLesson}` },
  beforeEach: () => {
    seedReasoningLesson(PARTIAL_REASONING);
  },
  play: async ({ canvasElement }) => {
    const canvas = await answerWithReasoning(canvasElement, "The result only comes from x.");
    await userEvent.click(canvas.getByTestId("quick-check-submit"));
    await expect(await canvas.findByTestId("reasoning-follow-up")).toBeInTheDocument();
  },
};

export const LessonReasoningSolid: Story = {
  name: "Lesson — reasoning solid",
  args: { url: `/lessons/${IDS.rustCurrentLesson}` },
  beforeEach: () => {
    seedReasoningLesson(SOLID_REASONING);
  },
  play: async ({ canvasElement }) => {
    const canvas = await answerWithReasoning(
      canvasElement,
      "The return type only mentions 'a, which is x's, so y can be dropped safely.",
    );
    await userEvent.click(canvas.getByTestId("quick-check-submit"));
    await expect(await canvas.findByTestId("reasoning-grade")).toBeInTheDocument();
  },
};

// --- Flow -------------------------------------------------------------------------

export const FlowSetup: Story = { name: "Flow — setup", args: { url: "/flow/new" } };

export const FlowReceipt: Story = {
  name: "Flow — receipt",
  args: { url: "/flow/done" },
  beforeEach: () => {
    writeFlow({
      version: 1,
      startedAt: "2026-11-21T07:40:00Z",
      length: 5,
      order: "interleave",
      paths: [IDS.rust, IDS.k8s],
      cursor: 0,
      current: null,
      next: null,
      completed: [
        {
          lessonId: "l-rust-3",
          pathId: IDS.rust,
          title: "Borrowing: shared and mutable references",
          outcome: "correct",
        },
        {
          lessonId: IDS.k8sFirstLesson,
          pathId: IDS.k8s,
          title: "Desired state vs. actual state",
          outcome: "correct",
        },
        {
          lessonId: "l-rust-4",
          pathId: IDS.rust,
          title: "The borrow checker's three rules",
          outcome: "incorrect",
        },
        { lessonId: "l-k8s-2", pathId: IDS.k8s, title: "Pods are cattle", outcome: "correct" },
        {
          lessonId: IDS.rustCurrentLesson,
          pathId: IDS.rust,
          title: "Reading lifetime annotations",
          outcome: "correct",
        },
      ],
      endedReason: "length",
    });
  },
};

// --- Beats & Briefs ----------------------------------------------------------------

export const Beat: Story = { args: { url: `/beats/${IDS.beat}` } };

export const BeatResearching: Story = {
  name: "Beat — researching",
  args: { url: `/beats/${IDS.beat}` },
  beforeEach: () => {
    configureBeats({ onSuccess: "none" });
    seedBeat({
      id: IDS.beat,
      topic: "AI infrastructure",
      level: "work_in_it",
      pollsRemaining: 1_000_000,
    });
  },
};

export const Brief: Story = { args: { url: `/briefs/${IDS.brief}` } };

// --- Settings ------------------------------------------------------------------------

export const NotificationSettings: Story = {
  name: "Settings — notifications",
  args: { url: "/settings/notifications" },
  beforeEach: ({ msw }) => {
    let preferences = {
      reminder_enabled: true,
      reminder_hour: 8,
      path_ready_enabled: true,
      brief_published_enabled: false,
    };
    msw.use(
      http.get(`${API_V1_BASE}/push-config`, () => HttpResponse.json({ vapid_public_key: "demo" })),
      http.get(`${API_V1_BASE}/notification-preferences`, () => HttpResponse.json(preferences)),
      http.put(`${API_V1_BASE}/notification-preferences`, async ({ request }) => {
        preferences = (await request.json()) as typeof preferences;
        return HttpResponse.json(preferences);
      }),
    );
  },
};

// --- Account profile: About you and the Learning summary ----------------------

const summarySession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: {
    ...learnerUser,
    display_name: "Matt McNaughton",
    feature_flags: {
      tutor: true,
      streaks: true,
      analyst: true,
      flow: true,
      learning_summary: true,
    },
  },
};

export const AboutYou: Story = {
  name: "Settings — About you",
  args: { url: "/settings/about-you" },
  beforeEach: () => {
    configureAccount({
      aboutYou:
        "I'm a nurse practitioner moving into health informatics. Use examples from clinical medicine where they fit. I learn best from a worked example before the theory.",
    });
  },
};

export const AboutYouEmpty: Story = {
  name: "Settings — About you, empty",
  args: { url: "/settings/about-you" },
};

export const NewPathAboutYouApplies: Story = {
  name: "New path — About you applies",
  args: { url: "/new" },
  beforeEach: () => {
    configureAccount({ aboutYou: "Use examples from clinical medicine where they fit." });
  },
  play: async ({ canvasElement }) => {
    await fillTheFormMyself(within(canvasElement));
  },
};

export const NewPathFromSuggestion: Story = {
  name: "New path — prefilled from a suggestion",
  args: {
    url: "/new?topic=Async%20Rust%3A%20borrows%20across%20.await&level=some_experience&guidance=Assume%20ownership%20is%20known.%20Cover%20Send%2C%20'static%20and%20Pin.&teaching_style=Worked%20example%20first.",
  },
};

export const LearningSummaryReady: Story = {
  name: "Your learning — ready",
  args: { url: "/learning" },
  beforeEach: ({ msw }) => {
    msw.use(sessionHandler(summarySession));
    configureAccount({
      summary: {
        state: "ready",
        facts: { lessons_complete: 8, paths: 3, beats: 2 },
        summary: EXAMPLE_SUMMARY,
        written_at: "2026-11-21T12:00:00Z",
        min_lessons_complete: 3,
      },
    });
  },
};

export const HomeWithLearningSummary: Story = {
  name: "Home — Your learning door",
  args: { url: "/" },
  beforeEach: ({ msw }) => {
    msw.use(sessionHandler(summarySession));
    configureAccount({ pollsBeforeReady: 1 });
  },
};

export const LearningSummaryNone: Story = {
  name: "Your learning — not written yet",
  args: { url: "/learning" },
  beforeEach: ({ msw }) => {
    msw.use(sessionHandler(summarySession));
  },
};

export const LearningSummaryTooEarly: Story = {
  name: "Your learning — too early",
  args: { url: "/learning" },
  beforeEach: ({ msw }) => {
    msw.use(sessionHandler(summarySession));
    configureAccount({
      summary: {
        state: "too_early",
        facts: { lessons_complete: 1, paths: 1, beats: 0 },
        summary: null,
        written_at: null,
        min_lessons_complete: 3,
      },
    });
  },
};

export const LearningSummaryFailed: Story = {
  name: "Your learning — failed",
  args: { url: "/learning" },
  beforeEach: ({ msw }) => {
    msw.use(sessionHandler(summarySession));
    configureAccount({
      summary: {
        state: "failed",
        facts: { lessons_complete: 8, paths: 3, beats: 2 },
        summary: null,
        written_at: null,
        min_lessons_complete: 3,
      },
    });
  },
};
