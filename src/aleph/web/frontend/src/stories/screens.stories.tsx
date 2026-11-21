/// <reference types="msw-storybook-addon/csf3" />
import type { Meta, StoryObj } from "@storybook/react-vite";
import { HttpResponse, http } from "msw";
import { expect, userEvent, within } from "storybook/test";
import { API_V1_BASE } from "../lib/api";
import { writeFlow } from "../lib/flow";
import { configureBeats, resetBeats, seedBeat } from "../mocks/beats";
import { configurePaths, resetPaths } from "../mocks/paths";
import { configureProgress, zeroActivity } from "../mocks/progress";
import { AppAt } from "./app-at";
import {
  FAILED_LESSON_ID,
  GENERATING_LESSON_ID,
  IDS,
  WORLD_TODAY,
  seedFailedLesson,
  seedGeneratingLesson,
  seedPathEdgeStates,
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

export const NewPath: Story = { name: "New path", args: { url: "/new" } };

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
