// The demo account behind the README screenshots: one learner a few weeks in,
// with three paths, a Beat with an unread Brief, a tutor conversation and a
// pending Proposal. Everything is seeded into the app's own MSW fakes
// (`src/mocks/`), so the screenshots show the real UI over contract-shaped data
// with no backend, account or model.

import type { PathUnit, ProgressSummary } from "../../src/lib/api";
import { seedBeat, seedBrief } from "../../src/mocks/beats";
import { learnerUser } from "../../src/mocks/handlers";
import { seedLesson } from "../../src/mocks/lessons";
import { seedPath } from "../../src/mocks/paths";
import { configureProgress } from "../../src/mocks/progress";
import { seedShapingConversation } from "../../src/mocks/shaping";
import { seedConversation } from "../../src/mocks/tutor";

export const demoUser = {
  ...learnerUser,
  display_name: "Maya Chen",
  username: "maya",
  email: "maya@example.com",
  feature_flags: { tutor: true, shaping: true, streaks: true, analyst: true, flow: true },
};

function isoDay(offsetDays: number): string {
  const day = new Date();
  day.setDate(day.getDate() + offsetDays);
  const local = new Date(day.getTime() - day.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

function lessonId(path: number, n: number): string {
  return `${path}0000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
}

function unitId(path: number, n: number): string {
  return `${path}1000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
}

type Row = [title: string, state: "complete" | "available" | "locked"];

function outline(path: number, units: Array<[string, Row[]]>): PathUnit[] {
  let position = 0;
  return units.map(([title, rows], u) => ({
    id: unitId(path, u + 1),
    title,
    lessons: rows.map(([lessonTitle, state]) => {
      position += 1;
      return {
        id: lessonId(path, position),
        title: lessonTitle,
        position_in_path: position,
        generation_state: state === "locked" ? "ungenerated" : "generated",
        unlock_state: state,
      };
    }),
  }));
}

export const HEALTHCARE_PATH_ID = "a0000000-0000-4000-8000-000000000001";
const RUST_PATH_ID = "b0000000-0000-4000-8000-000000000002";
const WINE_PATH_ID = "c0000000-0000-4000-8000-000000000003";
export const BEAT_ID = "d0000000-0000-4000-8000-000000000004";
export const BRIEF_ID = "e0000000-0000-4000-8000-000000000003";
/** The healthcare path's available lesson — the one the lesson shots open. */
export const LESSON_ID = lessonId(1, 4);

const HEALTHCARE_UNITS = outline(1, [
  [
    "Who pays for care",
    [
      ["Premiums, deductibles and coinsurance", "complete"],
      ["Where employer coverage comes from", "complete"],
      ["Medicare, Medicaid and the rest", "complete"],
    ],
  ],
  [
    "Following the money",
    [
      ["List prices vs. negotiated rates", "available"],
      ["Reading an Explanation of Benefits", "locked"],
      ["How hospitals get paid per stay", "locked"],
    ],
  ],
  [
    "Where the system strains",
    [
      ["Surprise bills and the No Surprises Act", "locked"],
      ["Why drug prices are so hard to see", "locked"],
    ],
  ],
]);

const READ_PASSAGE = `Every hospital keeps a **chargemaster**: a master list of prices for
everything it does, from an aspirin to an MRI. Almost nobody pays those prices.

Insurers negotiate their own **allowed amounts** with each hospital, usually
far below the list price. Your bill starts from the list price, but what you
owe is calculated from the allowed amount.

\`\`\`mermaid
flowchart LR
  A["List price<br/>$4,200"] --> B["Allowed amount<br/>$1,350"]
  B --> C["Insurer pays<br/>$1,080"]
  B --> D["You pay<br/>$270"]
\`\`\`

So why keep a list price at all? It anchors negotiations, it is what
uninsured patients are billed before any discount, and some contracts are
written as a percentage *of* it.

> The number at the top of the bill is the opening bid, not the price.

Your deductible and coinsurance, from the last unit, are both applied to the
**allowed amount**, never to the list price.`;

export function seedDemo(): void {
  const today = isoDay(0);

  seedPath({
    id: HEALTHCARE_PATH_ID,
    topic: "How US healthcare is paid for",
    level: "some_experience",
    guidance: "I'm switching jobs into health tech and want the money flows to make sense.",
    teachingStyle: "Use real dollar amounts in examples.",
    units: HEALTHCARE_UNITS,
    lastActivityAt: new Date().toISOString(),
  });
  seedPath({
    id: RUST_PATH_ID,
    topic: "Rust ownership and borrowing",
    level: "some_experience",
    units: outline(2, [
      [
        "Ownership",
        [
          ["Who owns a value", "complete"],
          ["Moves and copies", "complete"],
        ],
      ],
      [
        "Borrowing",
        [
          ["Shared references", "available"],
          ["Mutable references", "locked"],
          ["Lifetimes, gently", "locked"],
        ],
      ],
    ]),
    lastActivityAt: new Date(Date.now() - 86_400_000).toISOString(),
  });
  seedPath({
    id: WINE_PATH_ID,
    topic: "Tasting wine with confidence",
    level: "new_to_it",
    units: outline(3, [
      [
        "Learning to taste",
        [
          ["Look, swirl, smell, sip", "available"],
          ["Naming what you smell", "locked"],
          ["Acidity, tannin and body", "locked"],
        ],
      ],
    ]),
  });

  seedLesson({
    id: LESSON_ID,
    path_id: HEALTHCARE_PATH_ID,
    title: "List prices vs. negotiated rates",
    position_in_path: 4,
    position_in_unit: 1,
    unlock_state: "available",
    readPassage: READ_PASSAGE,
    stem: "Your plan has a 20% coinsurance and you've met your deductible. A scan lists at $4,200 and your insurer's allowed amount is $1,350. What do you owe?",
    options: ["$840", "$270", "$1,350", "$0"],
    correctIndex: 1,
    explanation:
      "Coinsurance applies to the allowed amount: 20% of $1,350 is $270. The $4,200 list price never enters the math once your plan has negotiated a rate.",
    attemptSelectedIndex: 1,
  });

  seedConversation(LESSON_ID, [
    {
      role: "learner",
      content: "If nobody pays the list price, who actually gets billed $4,200?",
    },
    {
      role: "tutor",
      content:
        "Mostly **uninsured patients**, at least on the first bill. Many hospitals then apply a self-pay discount or financial-assistance policy, so even they rarely pay the full amount.\n\nThe list price also matters for **out-of-network care**: with no negotiated rate, the hospital may bill from its chargemaster. That gap is exactly what the No Surprises Act, later in this path, was written to close.",
    },
    { role: "learner", content: "Quiz me on this" },
    {
      role: "tutor",
      content: "Try this one before moving on.",
      tutor_check: {
        stem: "Which number is your coinsurance calculated from?",
        options: ["The list price", "The allowed amount", "What the insurer pays"],
        correct_index: 1,
        explanation: "Cost sharing is always applied to the allowed amount.",
        answered_index: null,
      },
    },
  ]);

  seedShapingConversation(HEALTHCARE_PATH_ID, [
    {
      role: "learner",
      content: "I keep getting confused by my own bills. Can we practise on real ones?",
    },
    {
      role: "tutor",
      content:
        "Good idea. Here's a short unit right after the money-flow lessons, so you read real documents once you know what the numbers mean.",
      proposal: {
        summary: "Add a 2-lesson unit on reading your own bills",
        operations: [
          {
            insert_at_position: 7,
            lessons: [
              { title: "Line by line: a real hospital bill" },
              { title: "Spotting errors and asking for an itemized bill" },
            ],
            rationale: "Applies the allowed-amount math to documents you'll actually receive.",
            estimated_minutes: 10,
            new_unit: {
              title: "Reading your own bills",
              summary: "Practice on real statements.",
            },
          },
        ],
      },
      resolution: "pending",
    },
  ]);

  const activity = Array.from({ length: 49 }, (_, i) => {
    const daysAgo = 48 - i;
    const active = daysAgo < 12 || [14, 15, 17, 20, 21, 24, 28, 29, 33, 40].includes(daysAgo);
    return { date: isoDay(-daysAgo), count: active ? 1 + ((daysAgo * 7) % 3) : 0 };
  });
  const summary: ProgressSummary = {
    today,
    current_streak: 12,
    best_streak: 12,
    completed_today: 1,
    activity,
    paths: [
      { path_id: HEALTHCARE_PATH_ID, current_streak: 6, best_streak: 6, completed_today: 1 },
      { path_id: RUST_PATH_ID, current_streak: 0, best_streak: 4, completed_today: 0 },
    ],
  };
  configureProgress({ summary });

  seedBeat({
    id: BEAT_ID,
    topic: "AI coding agents and developer tooling",
    level: "work_in_it",
    anchorWeekday: 0,
    entries: [
      {
        kind: "published",
        id: BRIEF_ID,
        number: 3,
        publishedOn: isoDay(-1),
        title: "Coding agents move from the editor into CI",
      },
      {
        kind: "skipped",
        id: "e0000000-0000-4000-8000-000000000099",
        publishedOn: isoDay(-8),
        skipLine: "Nothing material since Brief #2.",
      },
      {
        kind: "published",
        id: "e0000000-0000-4000-8000-000000000002",
        number: 2,
        publishedOn: isoDay(-15),
        title: 'Review bots learn to say "I\'m not sure"',
        readAt: new Date().toISOString(),
      },
      {
        kind: "published",
        id: "e0000000-0000-4000-8000-000000000001",
        number: 1,
        publishedOn: isoDay(-22),
        title: "Where coding agents stand today",
        readAt: new Date().toISOString(),
      },
    ],
  });
  seedBrief({
    id: BRIEF_ID,
    beatId: BEAT_ID,
    number: 3,
    publishedOn: isoDay(-1),
    title: "Coding agents move from the editor into CI",
    buildsOn: { id: "e0000000-0000-4000-8000-000000000002", number: 2, publishedOn: isoDay(-15) },
    bodyMarkdown: `**What changed:** two of the major CI platforms now run coding agents as a pipeline
step: a failing build can open a pull request with a proposed fix, with no one at a
keyboard [1].

**Why it matters for you:** the agent's fix goes through the same review and checks as
anyone else's, so your test suite is now the main thing between a plausible patch and
production. Teams in early pilots report that flaky tests cost them more than the agents
saved [2].

**What didn't change:** agents still cannot merge on their own on either platform, and
the "I'm not sure" review behaviour from Brief #2 is unchanged.

**Watch next:** whether pricing moves to per-fix rather than per-seat [3].`,
    sources: [
      {
        position: 1,
        publisher: "Pipeline Weekly",
        title: "Agents as a CI step: what shipped this week",
        published_on: isoDay(-3),
        url: "https://example.com/agents-in-ci",
      },
      {
        position: 2,
        publisher: "The Build Log",
        title: "Early pilots find flaky tests are the bottleneck",
        published_on: isoDay(-2),
        url: "https://example.com/flaky-tests-agents",
      },
      {
        position: 3,
        publisher: "DevTools Ledger",
        title: "Per-seat or per-fix? How agent pricing could change",
        published_on: isoDay(-2),
        url: "https://example.com/agent-pricing",
      },
    ],
  });
}
