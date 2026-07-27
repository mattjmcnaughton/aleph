import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  type PathDetail,
  type PathLesson,
  type PathUnit,
  isNotFound,
  isPathViewTerminal,
  pathQueryKey,
  pathQueryOptions,
  retryPath,
} from "../lib/api";
import { PRIMARY_CTA, PlayIcon, RetryNotices, Spinner, StateCard } from "../components/state-card";
import { Breadcrumbs } from "../components/breadcrumbs";
import { LessonMarker, UNLOCK_STATE_LABEL } from "../components/lesson-marker";
import { Sidebar, SwitcherSection } from "../components/sidebar";
import { Workspace } from "../components/workspace";
import { makePollingRefetchInterval } from "../lib/polling";
import { useRetryGeneration } from "../lib/use-retry-generation";

export const Route = createFileRoute("/paths/$pathId")({
  component: PathView,
});

const pathViewPollConfig = {
  isTerminal: isPathViewTerminal,
  // A deep link to a missing path (404) is terminal — stop polling so we don't
  // spawn an endless chain of backend resumes against a path that can't resolve.
  isErrorTerminal: isNotFound,
};

// The path view (§5.4, TDD §8): the units/lessons rail for one path. It renders
// straight from a single `GET /paths/{id}` payload — every rail state is derived
// from each lesson's `unlock_state` (complete / available / locked), and the
// header progress is the complete-lesson count over the total. Content keeps
// generating after the outline is `ready` (on-demand + prefetch), so the view
// polls through the shared helper until the outline is terminal AND no lesson is
// still generating (`isPathViewTerminal`).
//
// A learner can also deep-link here onto a non-ready outline (a shared/bookmarked
// link, or a reload mid-generation). Those states render minimally so the learner
// never dead-ends — onboarding (AL-061) owns the rich topic-preserving versions.
function PathView() {
  const { pathId } = Route.useParams();
  const navigate = useNavigate();

  const pathQuery = useQuery({
    ...pathQueryOptions(pathId),
    refetchInterval: makePollingRefetchInterval(pathViewPollConfig),
  });

  const retry = useRetryGeneration({ mutationFn: retryPath, queryKey: pathQueryKey });

  const detail = pathQuery.data;

  function openLesson(lessonId: string) {
    navigate({ to: "/lessons/$lessonId", params: { lessonId } });
  }

  return (
    <Workspace
      testid="path-view"
      width="path"
      sidebar={
        <Sidebar>
          <SwitcherSection currentPathId={pathId} />
        </Sidebar>
      }
    >
      {detail ? <Breadcrumbs current={detail.topic} /> : null}

      {detail === undefined ? (
        pathQuery.isError ? (
          <UnavailableState />
        ) : (
          <LoadingState />
        )
      ) : detail.status === "refused" ? (
        <RefusedState message={detail.refusal_message ?? undefined} />
      ) : detail.status === "failed" ? (
        <FailedState
          onRetry={() => retry.retry(detail.id)}
          retrying={retry.retrying}
          retryRateLimited={retry.rateLimited}
          retryErrored={retry.errored}
        />
      ) : detail.status !== "ready" ? (
        <GeneratingState />
      ) : (
        <ReadyPath detail={detail} onOpenLesson={openLesson} />
      )}
    </Workspace>
  );
}

// --- Ready path: header + rail ----------------------------------------------

function ReadyPath({
  detail,
  onOpenLesson,
}: {
  detail: PathDetail;
  onOpenLesson: (lessonId: string) => void;
}) {
  const lessons = detail.units.flatMap((unit) => unit.lessons);
  const total = lessons.length;
  const complete = lessons.filter((lesson) => lesson.unlock_state === "complete").length;
  const allComplete = total > 0 && complete === total;
  const percent = total === 0 ? 0 : Math.round((complete / total) * 100);

  // The continue card's subject (mock #2b): the one lesson the learner can open
  // right now, plus the unit holding it (for the "Unit — lesson N of total"
  // byline). Exactly one lesson is ever `available` under the single total order
  // progression enforces, so `find` is the whole selection. Undefined only when
  // nothing is available at all — a complete path, which `CompleteBanner`
  // already covers, or an outline whose first lesson has yet to unlock.
  const continueLesson = lessons.find((lesson) => lesson.unlock_state === "available");
  const continueUnit = continueLesson
    ? detail.units.find((unit) => unit.lessons.some((lesson) => lesson.id === continueLesson.id))
    : undefined;

  return (
    <>
      <div className="lg:flex lg:items-end lg:justify-between lg:gap-8">
        <div className="min-w-0">
          <p className="kicker">Path</p>
          <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
            {detail.topic}
          </h1>
        </div>
        {/* The percentage alone, and only at `lg` (mock #2b). Deliberately not
            the "n of m complete" sentence too: `path-progress` below already
            carries it at every width, and printing it twice on one screen reads
            as a mistake. `aria-hidden` because this is the same number said a
            second way — `path-progress` stays the one announced readout. */}
        <p
          data-testid="path-progress-percent"
          aria-hidden="true"
          className="hidden shrink-0 font-mono text-[28px] leading-none text-teal lg:block"
        >
          {percent}
          <span className="text-base text-mist">%</span>
        </p>
      </div>

      <div className="mt-4">
        {/* Decorative bar; the text below is the accessible progress readout. */}
        <div aria-hidden="true" className="h-[7px] overflow-hidden rounded-full bg-porcelain/10">
          <span className="block h-full bg-teal" style={{ width: `${percent}%` }} />
        </div>
        <p data-testid="path-progress" className="mt-2 text-sm text-mist">
          {complete} of {total} {total === 1 ? "lesson" : "lessons"} complete
        </p>
      </div>

      {allComplete ? (
        <CompleteBanner />
      ) : continueLesson && continueUnit ? (
        <ContinueCard
          lesson={continueLesson}
          unit={continueUnit}
          total={total}
          started={complete > 0}
        />
      ) : null}

      <ol
        data-testid="path-rail"
        className="mt-8 space-y-8 lg:grid lg:grid-cols-2 lg:gap-x-8 lg:gap-y-7 lg:space-y-0"
      >
        {detail.units.map((unit, index) => (
          <UnitBlock key={unit.id} unit={unit} index={index} onOpenLesson={onOpenLesson} />
        ))}
      </ol>
    </>
  );
}

/**
 * The mock #2b continue panel — desktop-only, one obvious next action instead
 * of scanning the two-up rail for the available lesson. `started` picks the
 * kicker: the mock's "Pick up where you left off" is a claim about the past, so
 * a path whose first lesson is still its available one gets the honest opener
 * instead.
 */
function ContinueCard({
  lesson,
  unit,
  total,
  started,
}: {
  lesson: PathLesson;
  unit: PathUnit;
  total: number;
  started: boolean;
}) {
  return (
    <div
      data-testid="path-continue"
      data-started={started || undefined}
      className="mt-6 hidden items-center justify-between gap-6 rounded-lg border border-teal bg-teal/10 px-6 py-5 lg:flex"
    >
      <div className="min-w-0">
        <p className="font-mono text-[11px] font-medium uppercase tracking-kicker text-teal">
          {started ? "Pick up where you left off" : "Start your path"}
        </p>
        <p className="mt-1.5 text-lg font-semibold leading-snug">{lesson.title}</p>
        <p className="mt-1 text-sm text-mist">
          {unit.title} — lesson {lesson.position_in_path + 1} of {total}
        </p>
      </div>
      <Link
        to="/lessons/$lessonId"
        params={{ lessonId: lesson.id }}
        data-testid="path-continue-link"
        className="inline-flex shrink-0 items-center gap-2 rounded-md bg-teal px-5 py-3 text-sm font-semibold text-night transition-colors hover:bg-teal-bright"
      >
        Continue
        <PlayIcon />
      </Link>
    </div>
  );
}

function UnitBlock({
  unit,
  index,
  onOpenLesson,
}: {
  unit: PathUnit;
  index: number;
  onOpenLesson: (lessonId: string) => void;
}) {
  const unitComplete =
    unit.lessons.length > 0 && unit.lessons.every((lesson) => lesson.unlock_state === "complete");
  const inProgress = unit.lessons.some((lesson) => lesson.unlock_state === "available");

  return (
    <li>
      <div className="flex items-baseline justify-between">
        <p className="kicker">Unit {String(index + 1).padStart(2, "0")}</p>
        {inProgress ? (
          <span className="font-mono text-[11px] uppercase tracking-kicker text-teal">
            In progress
          </span>
        ) : unitComplete ? (
          <span className="font-mono text-[11px] uppercase tracking-kicker text-slate">
            Complete
          </span>
        ) : null}
      </div>
      <h2 className="mt-1 text-lg font-semibold leading-snug">{unit.title}</h2>

      <ul className="mt-3 space-y-2">
        {unit.lessons.map((lesson) => (
          <li key={lesson.id}>
            <LessonRow lesson={lesson} onOpen={onOpenLesson} />
          </li>
        ))}
      </ul>
    </li>
  );
}

const LESSON_ROW_BASE =
  "flex w-full items-center gap-3 rounded-md border px-4 py-3 text-left text-sm transition-colors";

const LESSON_ROW_STATE: Record<PathLesson["unlock_state"], string> = {
  complete: "border-divider bg-surface text-mist hover:text-porcelain hover:border-teal/40",
  available: "border-teal bg-teal/10 text-porcelain hover:bg-teal/15",
  locked: "cursor-not-allowed border-divider bg-surface/50 text-slate",
};

function LessonRow({
  lesson,
  onOpen,
}: {
  lesson: PathLesson;
  onOpen: (lessonId: string) => void;
}) {
  const locked = lesson.unlock_state === "locked";
  const generating = lesson.generation_state === "generating";

  // Native `disabled` makes a locked row inert (no click fires) AND drops it from
  // the tab order — deliberate: a locked lesson isn't actionable, so it shouldn't
  // be a focus stop. That makes the `onClick` handler unreachable when locked, so
  // it needs no guard, and `disabled` already exposes the state to AT (no
  // separate `aria-disabled`). The visual marker is decorative (aria-hidden), so
  // the sr-only label below is what conveys complete/available/locked to a
  // screen reader.
  return (
    <button
      type="button"
      data-testid={`lesson-${lesson.id}`}
      data-unlock-state={lesson.unlock_state}
      data-generation-state={lesson.generation_state}
      onClick={() => onOpen(lesson.id)}
      disabled={locked}
      className={`${LESSON_ROW_BASE} ${LESSON_ROW_STATE[lesson.unlock_state]}`}
    >
      <LessonMarker state={lesson.unlock_state} />
      <span className="sr-only">{UNLOCK_STATE_LABEL[lesson.unlock_state]}: </span>
      <span className="min-w-0 flex-1 truncate font-medium">{lesson.title}</span>
      {generating ? (
        <span className="shrink-0 font-mono text-[10px] uppercase tracking-kicker text-slate">
          Preparing
        </span>
      ) : null}
    </button>
  );
}

function CompleteBanner() {
  return (
    <section
      data-testid="path-complete"
      className="mt-6 rounded-lg border border-teal/40 bg-teal/10 p-5 text-center shadow-sm"
    >
      <h2 className="text-lg font-semibold text-porcelain">Path complete.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        You've finished every lesson. Revisit any lesson below, start another path, or keep it as a
        reference.
      </p>
    </section>
  );
}

// --- Non-ready + loading states ---------------------------------------------

function LoadingState() {
  return (
    <>
      <p className="kicker">Path</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        Loading your path…
      </h1>
    </>
  );
}

function GeneratingState() {
  return (
    <StateCard testid="path-generating" ariaLive="polite">
      <Spinner />
      <h1 className="mt-4 text-lg font-semibold">Drafting your path…</h1>
      <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
        Aleph is generating an outline of units and lessons. This page updates itself when it's
        ready.
      </p>
    </StateCard>
  );
}

function RefusedState({ message }: { message?: string }) {
  return (
    <StateCard testid="path-refused" variant="refusal" dataVariant="refusal">
      <h1 className="text-lg font-semibold text-iris-300">This topic is out of scope.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-porcelain">
        {message ??
          "Aleph can't build a path on this topic. Try a different topic and we'll draft one."}
      </p>
    </StateCard>
  );
}

function FailedState({
  onRetry,
  retrying,
  retryRateLimited,
  retryErrored,
}: {
  onRetry: () => void;
  retrying: boolean;
  retryRateLimited: boolean;
  retryErrored: boolean;
}) {
  return (
    <StateCard testid="path-failed" variant="error" dataVariant="error" ariaLive="assertive">
      <h1 className="text-lg font-semibold text-danger">Generation didn't finish.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-porcelain">
        We couldn't draft this path. Retry when you're ready.
      </p>
      <RetryNotices
        testidPrefix="path"
        rateLimited={retryRateLimited}
        errored={retryErrored}
        rateLimitMessage="You've reached today's limit for new paths. Try again tomorrow."
      />
      <button type="button" onClick={onRetry} disabled={retrying} className={`mt-6 ${PRIMARY_CTA}`}>
        {retrying ? "Retrying…" : "Try again"}
      </button>
    </StateCard>
  );
}

function UnavailableState() {
  return (
    <StateCard testid="path-unavailable">
      <h1 className="text-lg font-semibold">We couldn't load this path.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        It may have been deleted, or something went wrong. Head back to your paths and try again.
      </p>
    </StateCard>
  );
}
