// The storybook's mock "world": one realistic learner's paths, lessons, tutor
// threads, Beats and Briefs, seeded through the same in-memory MSW fakes the unit
// tests use (`src/mocks/`). Stories call `seedWorld()` (or a narrower seeder) in
// their `beforeEach`, after `.storybook/preview.tsx` has reset every fake.
//
// Keep it plausible: designers judge typography and density on this data, so
// titles, passage lengths and counts should look like a real week of use, not
// like "Lorem ipsum" or "Test 1". Never put this module in the app bundle.

import { HttpResponse, http } from "msw";
import {
  API_V1_BASE,
  type AuthSession,
  type PathUnit,
  type ProgressSummary,
  type Reasoning,
} from "../lib/api";
import { seedBeat, seedBrief } from "../mocks/beats";
import { learnerUser } from "../mocks/handlers";
import { seedLesson } from "../mocks/lessons";
import { seedPath } from "../mocks/paths";
import { configureProgress } from "../mocks/progress";
import { seedChanges, seedShapingConversation } from "../mocks/shaping";
import { seedConversation } from "../mocks/tutor";

// --- Session ----------------------------------------------------------------

/** Every launched surface on, so a story shows the app as a daily user sees it. */
export const worldSession: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: {
    ...learnerUser,
    display_name: "Matt McNaughton",
    username: "matt",
    email: "matt@example.com",
    feature_flags: { tutor: true, streaks: true, analyst: true, flow: true, shaping: true },
  },
};

export const signedOutWorldSession: AuthSession = {
  authenticated: false,
  provider: "keycloak",
  user: null,
};

export function sessionHandler(session: AuthSession) {
  return http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session));
}

// --- Paths --------------------------------------------------------------------

export const IDS = {
  rust: "p-rust-ownership",
  k8s: "p-kubernetes",
  bayes: "p-bayesian-stats",
  generating: "p-generating",
  failed: "p-failed",
  rustCurrentLesson: "l-rust-5",
  rustDoneLesson: "l-rust-2",
  rustLockedLesson: "l-rust-7",
  k8sFirstLesson: "l-k8s-1",
  bayesVisualLesson: "l-bayes-3",
  beat: "b-ai-infra",
  brief: "br-ai-infra-3",
} as const;

type Lesson = PathUnit["lessons"][number];

function lesson(
  id: string,
  title: string,
  position: number,
  unlock: Lesson["unlock_state"],
  generation: Lesson["generation_state"] = unlock === "locked" ? "ungenerated" : "generated",
): Lesson {
  return {
    id,
    title,
    position_in_path: position,
    generation_state: generation,
    unlock_state: unlock,
  };
}

/** Mid path, two units, the kind of path that sits at the top of Home. */
const RUST_UNITS: PathUnit[] = [
  {
    id: "u-rust-1",
    title: "Ownership from first principles",
    lessons: [
      lesson("l-rust-1", "Why Rust has no garbage collector", 1, "complete"),
      lesson("l-rust-2", "Moves, copies and the stack", 2, "complete"),
      lesson("l-rust-3", "Borrowing: shared and mutable references", 3, "complete"),
      lesson("l-rust-4", "The borrow checker's three rules", 4, "complete"),
    ],
  },
  {
    id: "u-rust-2",
    title: "Lifetimes in practice",
    lessons: [
      lesson(IDS.rustCurrentLesson, "Reading lifetime annotations", 5, "available"),
      lesson("l-rust-6", "Lifetimes in structs", 6, "locked", "generated"),
      lesson(IDS.rustLockedLesson, "Elision and when it fails", 7, "locked"),
      lesson("l-rust-8", "'static, honestly", 8, "locked"),
    ],
  },
  {
    id: "u-rust-3",
    title: "Smart pointers",
    lessons: [
      lesson("l-rust-9", "Box and heap allocation", 9, "locked"),
      lesson("l-rust-10", "Rc, Arc and shared ownership", 10, "locked"),
      lesson("l-rust-11", "Interior mutability with RefCell", 11, "locked"),
    ],
  },
];

const K8S_UNITS: PathUnit[] = [
  {
    id: "u-k8s-1",
    title: "The control loop",
    lessons: [
      lesson(IDS.k8sFirstLesson, "Desired state vs. actual state", 1, "available"),
      lesson("l-k8s-2", "Pods are cattle", 2, "locked"),
      lesson("l-k8s-3", "Deployments and ReplicaSets", 3, "locked"),
    ],
  },
  {
    id: "u-k8s-2",
    title: "Networking",
    lessons: [
      lesson("l-k8s-4", "Services and cluster DNS", 4, "locked"),
      lesson("l-k8s-5", "Ingress without the magic", 5, "locked"),
    ],
  },
];

const BAYES_UNITS: PathUnit[] = [
  {
    id: "u-bayes-1",
    title: "Thinking in distributions",
    lessons: [
      lesson("l-bayes-1", "Priors are assumptions you write down", 1, "complete"),
      lesson("l-bayes-2", "Likelihood, intuitively", 2, "complete"),
      lesson("l-bayes-3", "Updating: the posterior", 3, "complete"),
      lesson("l-bayes-4", "Credible intervals vs. confidence intervals", 4, "complete"),
    ],
  },
];

export function seedPaths(): void {
  seedPath({
    id: IDS.rust,
    topic: "Rust ownership and lifetimes",
    title: "Rust ownership & lifetimes",
    level: "some_experience",
    guidance: "I write Go at work and keep fighting the borrow checker on side projects.",
    teachingStyle: "Short passages, lots of small code examples, compare to Go where it helps.",
    units: RUST_UNITS,
    lastActivityAt: "2026-11-21T08:10:00Z",
  });
  seedPath({
    id: IDS.k8s,
    topic: "Kubernetes for application developers",
    level: "new_to_it",
    units: K8S_UNITS,
    lastActivityAt: "2026-11-18T19:30:00Z",
  });
  seedPath({
    id: IDS.bayes,
    topic: "Bayesian statistics",
    level: "some_experience",
    units: BAYES_UNITS,
    lastActivityAt: "2026-11-02T12:00:00Z",
  });
}

/** One path still generating its outline and one whose outline failed. */
export function seedPathEdgeStates(): void {
  seedPath({
    id: IDS.generating,
    topic: "The history of the printing press",
    level: "new_to_it",
    // Large enough that the outline never resolves while someone looks at it.
    pollsRemaining: 1_000_000,
  });
  seedPath({
    id: IDS.failed,
    topic: "Category theory for programmers",
    level: "work_in_it",
    resolution: "failed",
  });
}

// --- Lessons ------------------------------------------------------------------

export const LIFETIMES_PASSAGE = `A **lifetime** is the compiler's name for *how long a reference is
guaranteed to point at valid data*. You have been using lifetimes all along —
the borrow checker infers almost all of them. Annotations only appear when the
compiler cannot work out, from a function's signature alone, which input a
returned reference borrows from.

## The classic example

\`\`\`rust
fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {
    if x.len() > y.len() { x } else { y }
}
\`\`\`

Read \`'a\` as a promise: *the returned reference lives no longer than the
shorter of \`x\` and \`y\`.* It does not make anything live longer; it only
describes a relationship the compiler then checks at every call site.

> If you come from Go, the closest intuition is escape analysis run in reverse:
> instead of the compiler moving a value to the heap so a pointer stays valid,
> it refuses to compile code where the pointer could outlive the value.

## What the annotation does *not* do

- It does not change when anything is dropped.
- It does not allocate, copy, or extend a borrow.
- It is checked entirely at compile time; there is no runtime cost.

\`\`\`mermaid
flowchart LR
  A[x: &'a str] --> L{longest}
  B[y: &'a str] --> L
  L --> R[&'a str]
\`\`\`

| Situation | Need an annotation? |
| --- | --- |
| One reference in, reference out | No — elision handles it |
| Method returning a borrow of \`self\` | No |
| Two references in, reference out | **Yes** |
`;

export function seedLessons(): void {
  seedLesson({
    id: IDS.rustCurrentLesson,
    path_id: IDS.rust,
    title: "Reading lifetime annotations",
    position_in_path: 5,
    position_in_unit: 1,
    readPassage: LIFETIMES_PASSAGE,
    stem: "In `fn longest<'a>(x: &'a str, y: &'a str) -> &'a str`, what does `'a` promise?",
    options: [
      "The returned reference lives no longer than the shorter-lived of x and y",
      "x and y are kept alive until the returned reference is dropped",
      "The returned string is copied onto the heap",
      "x and y must be string literals",
    ],
    correctIndex: 0,
    explanation:
      "A lifetime annotation describes a relationship the compiler checks; it never extends how long a value lives.",
  });
  seedLesson({
    id: IDS.rustDoneLesson,
    path_id: IDS.rust,
    title: "Moves, copies and the stack",
    position_in_path: 2,
    position_in_unit: 2,
    unlock_state: "complete",
    readPassage:
      "Assigning a `String` to a new variable **moves** it: the old binding is no longer usable.\n\nTypes that implement `Copy` — integers, `bool`, `char`, and tuples of them — are duplicated instead, because copying them is as cheap as moving a pointer.",
    stem: "After `let b = a;` where `a: String`, what happens if you use `a`?",
    options: ["It is a compile error", "It prints an empty string", "It panics at runtime"],
    correctIndex: 0,
    explanation: "`a` was moved into `b`; the compiler rejects any later use of `a`.",
    attemptSelectedIndex: 1,
  });
  seedLesson({
    id: IDS.rustLockedLesson,
    path_id: IDS.rust,
    title: "Elision and when it fails",
    position_in_path: 7,
    position_in_unit: 3,
    unlock_state: "locked",
    resolution: "ungenerated",
  });
  seedLesson({
    id: IDS.k8sFirstLesson,
    path_id: IDS.k8s,
    title: "Desired state vs. actual state",
    readPassage:
      "Kubernetes is, underneath everything, a set of **control loops**. You declare the state you want; controllers repeatedly compare it with what is running and act to close the gap.\n\n```yaml\napiVersion: apps/v1\nkind: Deployment\nspec:\n  replicas: 3\n```\n\nNothing *runs* this file. A controller notices that three replicas are wanted and one exists, and starts two more.",
    stem: "What makes a Deployment's Pods come back after a node dies?",
    options: [
      "A controller notices actual state no longer matches desired state",
      "kubectl re-applies the manifest every minute",
      "The node restarts them when it reboots",
    ],
    correctIndex: 0,
    explanation: "Reconciliation, not re-applying, is the mechanism.",
  });
}

/** A lesson whose content generation failed (the lesson view's error state). */
export const FAILED_LESSON_ID = "l-rust-failed";
export function seedFailedLesson(): void {
  seedLesson({
    id: FAILED_LESSON_ID,
    path_id: IDS.rust,
    title: "Lifetimes in structs",
    position_in_path: 6,
    resolution: "failed",
  });
}

/** A lesson still generating (the lesson view's waiting state). */
export const GENERATING_LESSON_ID = "l-rust-generating";
export function seedGeneratingLesson(): void {
  seedLesson({
    id: GENERATING_LESSON_ID,
    path_id: IDS.rust,
    title: "Lifetimes in structs",
    position_in_path: 6,
    pollsRemaining: 1_000_000,
  });
}

// --- A lesson with an interactive Visual (docs/tdds/lesson-visuals.md) ---------

const BASE_RATE_VISUAL_ID = "7d3f2a10-5b8c-4e1d-9a6f-2c4b8e1f0a37";

/** Agent-style visual source: plain JS against the kit's tokens and classes. */
const BASE_RATE_SOURCE = `<style>
  .field{display:grid;grid-template-columns:1fr auto;gap:2px 8px;margin-bottom:10px}
  .field input{grid-column:1/-1}
  b{font-variant-numeric:tabular-nums}
  .grid{display:grid;grid-template-columns:repeat(20,1fr);gap:2px;margin:4px 0 10px;max-width:360px}
  .grid i{aspect-ratio:1;border-radius:2px;background:var(--elevated)}
  .grid i.tp{background:var(--teal)} .grid i.fp{background:var(--iris)}
</style>
<div class="field"><label for="prev">How common the condition is</label><b id="prev-v"></b>
  <input id="prev" type="range" min="1" max="20" value="2"></div>
<div class="field"><label for="acc">Test accuracy</label><b id="acc-v"></b>
  <input id="acc" type="range" min="80" max="99" value="95"></div>
<div id="grid" class="grid" role="img" aria-label="200 people, marked by test result"></div>
<p class="muted" style="margin:0">Of everyone who tests positive, <b id="post" style="color:var(--porcelain)"></b>
  actually has it. <span style="color:var(--teal)">■</span> true positive
  <span style="color:var(--iris)">■</span> false positive</p>
<script>
  const $ = (id) => document.getElementById(id);
  function draw() {
    const prev = Number($("prev").value) / 100, acc = Number($("acc").value) / 100;
    const n = 200, tp = Math.round(n * prev * acc);
    const fp = Math.round(n * (1 - prev) * (1 - acc));
    const posterior = (prev * acc) / (prev * acc + (1 - prev) * (1 - acc));
    $("prev-v").textContent = $("prev").value + "%";
    $("acc-v").textContent = $("acc").value + "%";
    $("post").textContent = Math.round(100 * posterior) + "%";
    let cells = "";
    for (let i = 0; i < n; i++) cells += i < tp ? '<i class="tp"></i>' : i < tp + fp ? '<i class="fp"></i>' : "<i></i>";
    $("grid").innerHTML = cells;
  }
  $("prev").addEventListener("input", draw);
  $("acc").addEventListener("input", draw);
  draw();
  kit.ready();
</script>`;

export const BASE_RATE_VISUAL = {
  id: BASE_RATE_VISUAL_ID,
  kind: "interactive" as const,
  title: "Why a positive test is weaker evidence than it sounds",
  alt:
    "Two sliders set how common a condition is (1–20%) and how accurate the test is " +
    "(80–99%). A grid of 200 people marks true and false positives; at 2% prevalence " +
    "and 95% accuracy only about 28% of positives actually have the condition.",
  source: BASE_RATE_SOURCE,
  kit_version: 1,
};

const POSTERIOR_PASSAGE = `Bayes' rule says the posterior is the prior, *reweighted by how well each
hypothesis predicts what you saw*. The surprise is how much the prior matters.

Take a condition **2%** of people have, and a test that is **95%** accurate either
way. Out of 1,000 people, 20 have it and 19 test positive. Of the 980 who don't, 49
still test positive. So of the 68 positives, only 19 — about **28%** — have the
condition.

\`\`\`visual ${BASE_RATE_VISUAL_ID}
\`\`\`

The false positives come from the *large* healthy group, so they swamp the true
ones whenever the condition is rare. Raise prevalence to 20% and the same test's
positives are right about 83% of the time: the evidence did not change, the prior did.`;

/** The Bayes path's third lesson, with an interactive Visual. */
export function seedVisualLesson(source: string = BASE_RATE_SOURCE): void {
  seedLesson({
    id: IDS.bayesVisualLesson,
    path_id: IDS.bayes,
    title: "Updating: the posterior",
    position_in_path: 3,
    // Matches the path rail: the Bayes path is finished, Attempt recorded.
    unlock_state: "complete",
    attemptSelectedIndex: 1,
    readPassage: POSTERIOR_PASSAGE,
    stem: "A test is 99% accurate for a condition 1 in 1,000 people have. You test positive. What best describes your chance of having it?",
    options: [
      "About 99%, because that is the test's accuracy",
      "Under 10%, because false positives from the healthy majority dominate",
      "Exactly 50%, because you either have it or you don't",
    ],
    correctIndex: 1,
    explanation:
      "Out of 100,000 people, ~99 sick people test positive but ~999 healthy ones do too, so only about 9% of positives are real.",
    visuals: [{ ...BASE_RATE_VISUAL, source }],
  });
}

/** The current Rust lesson asking for a Reasoning, graded as `graded`. */
export function seedReasoningLesson(graded: Omit<Reasoning, "text">): void {
  seedLessons();
  seedLesson({
    id: IDS.rustCurrentLesson,
    path_id: IDS.rust,
    title: "Reading lifetime annotations",
    position_in_path: 5,
    position_in_unit: 1,
    readPassage: LIFETIMES_PASSAGE,
    stem: "A function takes x: &'a str and y: &'b str and returns &'a str. A caller drops y right after the call. Is the returned reference still usable?",
    options: [
      "Yes: the result is tied only to x's lifetime, so dropping y is fine",
      "No: every input must outlive any returned reference",
      "Only if y was a string literal",
      "No: the annotation keeps y alive until the result is dropped",
    ],
    correctIndex: 0,
    explanation:
      "The signature ties the result to `'a` alone, so only `x` has to outlive it; `y`'s lifetime is unrelated.",
    asksReasoning: true,
    gradedReasoning: graded,
  });
}

export const REASONING_KEY_POINTS = [
  "The return type names only 'a, so the result borrows from x alone.",
  "So only x must outlive the result; dropping y cannot invalidate it.",
];

export const PARTIAL_REASONING: Omit<Reasoning, "text"> = {
  state: "graded",
  grade: "partial",
  feedback:
    "You're right that the result comes from x, but you didn't say why dropping y can't affect it: the signature ties the result to 'a only.",
  key_points: REASONING_KEY_POINTS,
  covered_points: [0],
  misconceptions_shown: [],
  follow_up: {
    question: "What would the signature have to say for dropping y to be a problem?",
    answer: null,
    state: null,
    grade: null,
    feedback: null,
  },
};

export const SOLID_REASONING: Omit<Reasoning, "text"> = {
  state: "graded",
  grade: "solid",
  feedback: "Exactly: the result's lifetime is 'a, which only x carries, so y is free to go.",
  key_points: REASONING_KEY_POINTS,
  covered_points: [0, 1],
  misconceptions_shown: [],
  follow_up: null,
};

/** A visual whose source throws: the learner reads its alt text instead. */
export const BROKEN_VISUAL_SOURCE = `<script>document.getElementById("missing").textContent = "x"; kit.ready();</script>`;

// --- Tutor & shaping ------------------------------------------------------------

export function seedTutor(): void {
  seedConversation(IDS.rustCurrentLesson, [
    {
      role: "learner",
      content: "Why can't the compiler just figure out which input the result borrows from?",
      lesson_title: "Reading lifetime annotations",
    },
    {
      role: "tutor",
      content:
        "It deliberately only looks at the **signature**, not the body. That keeps checking local: callers depend on the promise in the signature, so changing the body can't silently break them.\n\nIn `longest`, the body could return either `x` or `y`, so the signature has to say both are tied to the result.",
      lesson_title: "Reading lifetime annotations",
    },
    {
      role: "tutor",
      content: "Quick one to check that landed:",
      lesson_title: "Reading lifetime annotations",
      tutor_check: {
        stem: "Which does the borrow checker use to check a call to `longest`?",
        options: ["Only the function signature", "The function body", "Both, equally"],
        correct_index: 0,
        explanation: "Signatures are the contract; bodies are checked against them separately.",
        answered_index: null,
      },
    },
  ]);
}

export function seedShaping(): void {
  seedShapingConversation(IDS.rust, [
    {
      role: "learner",
      content: "Can we add something on async lifetimes? That's where I actually get stuck.",
    },
    {
      role: "tutor",
      content:
        "Good call — async is where lifetime errors get confusing, because a future holds its borrows across `.await`. I'd add two lessons after *Lifetimes in structs*:",
      proposal: {
        summary: "Add two lessons on lifetimes across .await",
        operations: [
          {
            insert_at_position: 7,
            lessons: [{ title: "Borrows that live across .await" }, { title: "Pin, briefly" }],
            rationale:
              "Async functions are the most common place lifetime errors appear in practice.",
            estimated_minutes: 18,
            new_unit: null,
          },
        ],
      },
      resolution: "pending",
    },
  ]);
  seedChanges(IDS.rust, [
    {
      id: "c-1",
      summary: "Added “'static, honestly” after “Elision and when it fails”",
      kinds: ["add_lessons"],
      status: "applied",
      applied_at: "2026-11-12T14:00:00Z",
      undone_at: null,
    },
  ]);
}

// --- Progress -------------------------------------------------------------------

export const WORLD_TODAY = "2026-11-21";

/** 49 days of plausible activity ending today: a 12-day streak with gaps before it. */
export function worldProgress(): ProgressSummary {
  const anchor = new Date(`${WORLD_TODAY}T12:00:00Z`);
  const pattern = [0, 1, 2, 0, 1, 3, 1, 0, 0, 2, 1, 1, 0, 2];
  const activity = Array.from({ length: 49 }, (_, i) => {
    const date = new Date(anchor);
    date.setUTCDate(date.getUTCDate() - (48 - i));
    const inStreak = i >= 49 - 12;
    const count = inStreak ? 1 + ((i * 7) % 3) : pattern[i % pattern.length];
    return { date: date.toISOString().slice(0, 10), count };
  });
  return {
    today: WORLD_TODAY,
    current_streak: 12,
    best_streak: 19,
    completed_today: activity[48].count,
    activity,
    paths: [
      { path_id: IDS.rust, current_streak: 12, best_streak: 12, completed_today: 2 },
      { path_id: IDS.bayes, current_streak: 0, best_streak: 4, completed_today: 0 },
    ],
  };
}

export function seedProgress(): void {
  configureProgress({ summary: worldProgress() });
}

// --- Beats & Briefs ---------------------------------------------------------------

const BRIEF_BODY = `This week's thread: **inference is getting cheaper faster than training**,
and that changes which infrastructure bets look smart.

## What happened

Two open-weight releases landed within days of each other, both claiming
frontier-class reasoning at a fraction of the serving cost. More interesting
than the benchmarks is *why*: both lean on speculative decoding and aggressive
KV-cache quantisation rather than on smaller models.

## Why it matters for you

If you run your own serving stack, memory bandwidth — not FLOPs — is now the
number to watch. Last week's Brief on batching schedulers is the other half of
this story.

## What to watch

- Whether hosted providers pass the savings on or keep the margin.
- KV-cache formats converging (or not) across runtimes.
`;

export function seedBeats(): void {
  seedBeat({
    id: IDS.beat,
    topic: "AI infrastructure",
    level: "work_in_it",
    anchorWeekday: 4,
    guidance: "Serving, inference cost and hardware — skip funding news.",
    entries: [
      {
        kind: "published",
        id: IDS.brief,
        number: 3,
        publishedOn: "2026-11-20",
        title: "Inference is getting cheaper faster than training",
        readAt: null,
      },
      {
        kind: "skipped",
        id: "br-ai-infra-skip",
        publishedOn: "2026-11-13",
        skipLine: "A quiet week: nothing new enough to be worth your time.",
      },
      {
        kind: "published",
        id: "br-ai-infra-2",
        number: 2,
        publishedOn: "2026-11-06",
        title: "Batching schedulers, explained",
        readAt: "2026-11-07T09:00:00Z",
      },
      {
        kind: "published",
        id: "br-ai-infra-1",
        number: 1,
        publishedOn: "2026-10-30",
        title: "Where the GPUs actually are",
        readAt: "2026-10-30T18:00:00Z",
      },
    ],
  });
  seedBeat({
    id: "b-climate",
    topic: "Climate tech policy",
    level: "new_to_it",
    anchorWeekday: 0,
    entries: [
      {
        kind: "published",
        id: "br-climate-1",
        number: 1,
        publishedOn: "2026-11-17",
        title: "What the new grid interconnection rules change",
        readAt: "2026-11-17T20:00:00Z",
      },
    ],
  });
  seedBrief({
    id: IDS.brief,
    beatId: IDS.beat,
    number: 3,
    publishedOn: "2026-11-20",
    title: "Inference is getting cheaper faster than training",
    bodyMarkdown: BRIEF_BODY,
    buildsOn: { id: "br-ai-infra-2", number: 2, publishedOn: "2026-11-06" },
    sources: [
      {
        position: 1,
        publisher: "The Gradient",
        title: "Speculative decoding goes mainstream",
        published_on: "2026-11-18",
        url: "https://example.com/speculative-decoding",
      },
      {
        position: 2,
        publisher: "SemiAnalysis",
        title: "KV cache quantisation in production",
        published_on: "2026-11-16",
        url: "https://example.com/kv-cache",
      },
      {
        position: 3,
        publisher: "Hacker News",
        title: "Show HN: serving a 70B model on two consumer GPUs",
        published_on: "2026-11-15",
        url: "https://example.com/show-hn",
      },
    ],
  });
}

// --- Everything -------------------------------------------------------------------

/** The whole world: what a daily learner's account looks like. */
export function seedWorld(): void {
  seedPaths();
  seedLessons();
  seedVisualLesson();
  seedTutor();
  seedShaping();
  seedProgress();
  seedBeats();
}
