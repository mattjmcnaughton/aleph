import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  pathsListQueryOptions,
  progressSummaryQueryOptions,
  reviewSummaryQueryOptions,
} from "../lib/api";
import { Breadcrumbs } from "../components/breadcrumbs";
import { FlowSegments, segments } from "../components/flow/flow-bar";
import { FlowDrafts } from "../components/flow/flow-drafts";
import { PRIMARY_CTA_BASE, SECONDARY_CTA, StateCard } from "../components/state-card";
import { StreakLine } from "../components/streak-line";
import { Workspace } from "../components/workspace";
import { useFeatureFlag } from "../lib/feature-flags";
import { type FlowCompletion, type FlowRecord, useFlow } from "../lib/flow";
import { markFlowCelebrated, shouldCelebrateFlow } from "../lib/flow-celebration";
import { prefersReducedMotion, useCountUp } from "../lib/motion";
import { useSettings } from "../lib/settings";

export const Route = createFileRoute("/flow/done")({
  component: FlowReceipt,
});

/** Names 1-9 in words (mock screen 04's own rule); 10+ falls back to digits. */
const COUNT_WORDS = [
  "Zero",
  "One",
  "Two",
  "Three",
  "Four",
  "Five",
  "Six",
  "Seven",
  "Eight",
  "Nine",
];
function countHeadline(n: number): string {
  const word = n >= 0 && n < COUNT_WORDS.length ? COUNT_WORDS[n] : String(n);
  return `${word} ${n === 1 ? "lesson" : "lessons"}, back to back.`;
}

function endedKicker(endedReason: FlowRecord["endedReason"]): string {
  if (endedReason === "ended") return "Flow ended";
  if (endedReason === "dry") return "Flow ran dry";
  return "Flow complete";
}

/** One row per distinct path, in the order the flow first touched it. */
interface LandedRow {
  pathId: string;
  count: number;
}
function landedRows(completed: FlowCompletion[]): LandedRow[] {
  const rows: LandedRow[] = [];
  for (const completion of completed) {
    const existing = rows.find((row) => row.pathId === completion.pathId);
    if (existing) {
      existing.count += 1;
    } else {
      rows.push({ pathId: completion.pathId, count: 1 });
    }
  }
  return rows;
}

/** The gap between one block of the receipt rising in and the next. */
const RISE_STEP_MS = 110;
/** When the stat tiles start counting: after the tiles themselves have risen. */
const COUNT_LEAD_MS = 420;
const COUNT_STEP_MS = 160;

// The receipt (flow TDD §5.5, mock screen 04): what the learner did, where it
// landed, the drafts they skipped past — nothing new is counted here (D6's
// settled call), every stat below is a summary of records that already exist.
//
// A flow that reached its length lands with a celebration (§5.5, "How it
// lands"): the strip the learner watched fill closes, cell by cell; the stat
// tiles count up; the rest of the receipt prints in a beat at a time. Three
// rules, borrowed whole from the path seal (`components/path-complete.tsx`):
// in place, never over; earned, not congratulated (an ended or dry flow gets
// the receipt standing still); and motion is never the message — every
// element's resting state is its final state, so under
// `prefers-reduced-motion` the receipt simply exists, complete.
function FlowReceipt() {
  const navigate = useNavigate();
  const flowEnabled = useFeatureFlag("flow");
  const flow = useFlow();

  // Decided once, on the render that arrived here, and remembered so the same
  // flow's receipt stands still on a revisit (lib/flow-celebration.ts).
  const [celebrate] = useState(() => flow !== null && shouldCelebrateFlow(flow));
  // Runs on mount only: the flow it marks is the one `celebrate` was decided
  // for, and marking is a write, so it is not done in the state initializer
  // (which StrictMode calls twice).
  // biome-ignore lint/correctness/useExhaustiveDependencies: see comment above.
  useEffect(() => {
    if (celebrate && flow !== null) markFlowCelebrated(flow);
  }, []);

  // The count-up is the one piece of the celebration CSS cannot carry, so it
  // reads the preference itself; everything else is a `motion-safe:` class.
  const animateCounts = celebrate && !prefersReducedMotion();
  const completedAll = flow?.completed ?? [];
  const lessonsShown = useCountUp(completedAll.length, animateCounts, COUNT_LEAD_MS);
  const pathsShown = useCountUp(
    new Set(completedAll.map((c) => c.pathId)).size,
    animateCounts,
    COUNT_LEAD_MS + COUNT_STEP_MS,
  );
  const correctShown = useCountUp(
    completedAll.filter((c) => c.outcome === "correct").length,
    animateCounts,
    COUNT_LEAD_MS + 2 * COUNT_STEP_MS,
  );
  const streaksEnabled = useFeatureFlag("streaks");
  const flashcardsEnabled = useFeatureFlag("flashcards");
  const { auto_draft_flashcards: autoDraft } = useSettings();

  const pathsQuery = useQuery(pathsListQueryOptions);
  const progressQuery = useQuery(progressSummaryQueryOptions(streaksEnabled));
  const reviewSummaryQuery = useQuery(reviewSummaryQueryOptions(flashcardsEnabled));

  // No record at all: a direct/stale visit (the flow already ended and was
  // cleared, or never existed). Redirect rather than render a receipt for
  // nothing (§5.5: "reads the record. If none, redirect to /").
  useEffect(() => {
    if (flowEnabled && flow === null) {
      navigate({ to: "/", replace: true });
    }
  }, [flowEnabled, flow, navigate]);

  if (!flowEnabled) {
    return (
      <main className="mx-auto w-full max-w-[480px] px-4 py-8">
        <Breadcrumbs current="Flow" />
        <StateCard testid="flow-done-unavailable">
          <h2 className="text-lg font-semibold">Flow isn't available.</h2>
          <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
            Head back home — there's nothing to show here.
          </p>
          <Link to="/" className="mt-5 inline-block text-sm text-teal">
            Back home
          </Link>
        </StateCard>
      </main>
    );
  }

  if (flow === null) return null;

  const { completed } = flow;
  const withOutcome = completed.filter((c) => c.outcome !== null);

  // The receipt prints: block `i` rises `i` beats after the first. Resting
  // classes carry the final layout; only the delay is per block.
  const rise = (index: number) =>
    celebrate
      ? {
          className: "motion-safe:animate-rise-in",
          style: { animationDelay: `${index * RISE_STEP_MS}ms` },
        }
      : { className: "", style: undefined };

  const rows = landedRows(completed);
  const pathTitle = (pathId: string) =>
    pathsQuery.data?.paths.find((p) => p.id === pathId)?.title ?? "";
  const pathProgress = (pathId: string) =>
    pathsQuery.data?.paths.find((p) => p.id === pathId)?.progress;

  // "Go again · n more" (§5.5's own formula) — the previous scope, prefilled
  // onto the setup sheet rather than replayed automatically, so the learner
  // still gets one more look at the door before starting a second flow.
  const again = flow.length ?? (completed.length || 3);
  const goAgainSearch = {
    length: flow.length === null ? "open" : String(flow.length),
    order: flow.order,
    paths: flow.paths.join(","),
  };

  return (
    <Workspace testid="flow-receipt" width="lesson">
      <Breadcrumbs current="Flow" />

      {/* The strip, one last time, complete — only for a flow that reached
          its length: it is the celebration's first beat, and an ended or dry
          flow's strip would be a "3 of 5" the receipt already says in words. */}
      {flow.endedReason === "length" ? (
        <div
          data-testid="flow-receipt-strip"
          data-celebrate={celebrate ? "true" : "false"}
          className={`mb-4 flex items-center gap-2.5 ${rise(0).className}`}
          style={rise(0).style}
        >
          <span className="whitespace-nowrap font-mono text-[11.5px] font-semibold tabular-nums text-teal-bright">
            Flow · {completed.length} of {completed.length}
          </span>
          <FlowSegments states={segments(flow, completed.length)} lightUp={celebrate} />
        </div>
      ) : null}

      <div className={rise(1).className} style={rise(1).style}>
        <p className="kicker text-teal-bright">{endedKicker(flow.endedReason)}</p>
        <h1 className="mt-2 text-2xl font-semibold leading-tight tracking-tight">
          {countHeadline(completed.length)}
        </h1>
      </div>

      {/* Two columns, not three (flow-fix plan item 7), when the Quick
          checks stat is hidden below — three slots for two stats leaves a
          visibly empty cell rather than a clean row. */}
      <div
        className={`mt-5 grid gap-2 ${withOutcome.length > 0 ? "grid-cols-3" : "grid-cols-2"} ${rise(2).className}`}
        style={rise(2).style}
      >
        <Stat testid="flow-receipt-lessons" value={lessonsShown} label="lessons" />
        <Stat testid="flow-receipt-paths" value={pathsShown} label="paths" />
        {withOutcome.length > 0 ? (
          <Stat
            testid="flow-receipt-checks"
            value={`${correctShown}/${withOutcome.length}`}
            label="Quick checks"
          />
        ) : null}
      </div>

      {progressQuery.data !== undefined ? (
        <div className={`mt-5 ${rise(3).className}`} style={rise(3).style}>
          <StreakLine summary={progressQuery.data} />
        </div>
      ) : null}

      {rows.length > 0 ? (
        <div
          className={`mt-6 rounded-lg bg-surface p-4 ring-1 ring-inset ring-[#3f424d] ${rise(4).className}`}
          style={rise(4).style}
        >
          <p className="kicker">Where it landed</p>
          <ul
            data-testid="flow-receipt-ledger"
            className="mt-2 flex flex-col divide-y divide-divider"
          >
            {rows.map((row, index) => {
              const progress = pathProgress(row.pathId);
              return (
                <li
                  key={row.pathId}
                  className={`flex items-center justify-between gap-3 py-2 text-sm ${rise(5 + index).className}`}
                  style={rise(5 + index).style}
                >
                  <span className="min-w-0 truncate">{pathTitle(row.pathId)}</span>
                  <span className="shrink-0 tabular-nums text-mist">
                    {row.count} {row.count === 1 ? "lesson" : "lessons"}
                    {progress
                      ? ` · ${progress.completed_lessons} of ${progress.total_lessons}`
                      : ""}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      <div className={rise(5 + rows.length).className} style={rise(5 + rows.length).style}>
        <FlowDrafts
          completed={completed}
          flashcardsEnabled={flashcardsEnabled}
          autoDraft={autoDraft}
        />
      </div>

      <div
        className={`mt-8 flex flex-col gap-3 ${rise(6 + rows.length).className}`}
        style={rise(6 + rows.length).style}
      >
        <Link
          to="/flow/new"
          search={goAgainSearch}
          data-testid="flow-receipt-again"
          className={PRIMARY_CTA_BASE}
        >
          Go again · {again} more
        </Link>
        <Link to="/" data-testid="flow-receipt-home" className={SECONDARY_CTA}>
          Home
        </Link>
        {reviewSummaryQuery.data !== undefined && reviewSummaryQuery.data.due_count > 0 ? (
          <Link
            to="/review"
            data-testid="flow-receipt-review"
            className="text-center text-sm text-mist transition-colors hover:text-porcelain"
          >
            {reviewSummaryQuery.data.due_count} cards due · Review
          </Link>
        ) : null}
      </div>
    </Workspace>
  );
}

function Stat({ testid, value, label }: { testid: string; value: number | string; label: string }) {
  return (
    <div className="rounded-lg bg-surface p-3 text-center ring-1 ring-inset ring-[#3f424d]">
      <p data-testid={testid} className="text-2xl font-semibold tabular-nums text-teal-bright">
        {value}
      </p>
      <p className="mt-1 text-[11.5px] text-slate">{label}</p>
    </div>
  );
}
