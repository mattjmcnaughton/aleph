import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  type PathDetail,
  type PathLesson,
  type PathUnit,
  isNotFound,
  isPathViewTerminal,
  isRateLimited,
  pathQueryKey,
  pathQueryOptions,
  retryPath,
} from "../lib/api";
import { makePollingRefetchInterval } from "../lib/polling";

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
  const queryClient = useQueryClient();

  const pathQuery = useQuery({
    ...pathQueryOptions(pathId),
    refetchInterval: makePollingRefetchInterval(pathViewPollConfig),
  });

  const retryMutation = useMutation({
    mutationFn: (id: string) => retryPath(id),
    // Reset (not invalidate) so the poll restarts at the 2s cadence rather than
    // resuming at the 5s ceiling (mirrors onboarding's retry, see lib/polling).
    onSuccess: (_created, id) => queryClient.resetQueries({ queryKey: pathQueryKey(id) }),
  });

  const detail = pathQuery.data;

  // Surface a retry that didn't land (mirrors onboarding's FailedState): a
  // rate-limited retry gets the daily-cap notice, any other error a generic one.
  const retryRateLimited = retryMutation.isError && isRateLimited(retryMutation.error);
  const retryErrored = retryMutation.isError && !retryRateLimited;

  function openLesson(lessonId: string) {
    navigate({ to: "/lessons/$lessonId", params: { lessonId } });
  }

  return (
    <main data-testid="path-view" className="mx-auto w-full max-w-[480px] px-4 py-8">
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
          onRetry={() => retryMutation.mutate(detail.id)}
          retrying={retryMutation.isPending}
          retryRateLimited={retryRateLimited}
          retryErrored={retryErrored}
        />
      ) : detail.status !== "ready" ? (
        <GeneratingState />
      ) : (
        <ReadyPath detail={detail} onOpenLesson={openLesson} />
      )}
    </main>
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

  return (
    <>
      <p className="kicker">Path</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">{detail.topic}</h1>

      <div className="mt-4">
        {/* Decorative bar; the text below is the accessible progress readout. */}
        <div aria-hidden="true" className="h-[7px] overflow-hidden rounded-full bg-porcelain/10">
          <span className="block h-full bg-teal" style={{ width: `${percent}%` }} />
        </div>
        <p data-testid="path-progress" className="mt-2 text-sm text-mist">
          {complete} of {total} {total === 1 ? "lesson" : "lessons"} complete
        </p>
      </div>

      {allComplete ? <CompleteBanner /> : null}

      <ol data-testid="path-rail" className="mt-8 space-y-8">
        {detail.units.map((unit, index) => (
          <UnitBlock key={unit.id} unit={unit} index={index} onOpenLesson={onOpenLesson} />
        ))}
      </ol>
    </>
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

/** Screen-reader label per unlock state (the marker icon is aria-hidden). */
const UNLOCK_STATE_LABEL: Record<PathLesson["unlock_state"], string> = {
  complete: "Complete",
  available: "Available",
  locked: "Locked",
};

function LessonMarker({ state }: { state: PathLesson["unlock_state"] }) {
  if (state === "complete") {
    return (
      <span
        aria-hidden="true"
        className="grid h-6 w-6 shrink-0 place-items-center rounded-full border border-divider text-teal"
      >
        <CheckIcon />
      </span>
    );
  }
  if (state === "locked") {
    return (
      <span
        aria-hidden="true"
        className="grid h-6 w-6 shrink-0 place-items-center rounded-full border border-divider text-slate"
      >
        <LockIcon />
      </span>
    );
  }
  return (
    <span
      aria-hidden="true"
      className="grid h-6 w-6 shrink-0 place-items-center rounded-full border border-teal text-teal"
    >
      <PlayIcon />
    </span>
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
    <section
      data-testid="path-generating"
      aria-live="polite"
      className="mt-4 rounded-lg border border-divider bg-surface p-6 text-center shadow-sm"
    >
      <div
        className="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-divider border-t-teal"
        aria-hidden="true"
      />
      <h1 className="mt-4 text-lg font-semibold">Drafting your path…</h1>
      <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
        Aleph is generating an outline of units and lessons. This page updates itself when it's
        ready.
      </p>
    </section>
  );
}

function RefusedState({ message }: { message?: string }) {
  return (
    <section
      data-testid="path-refused"
      data-variant="refusal"
      className="mt-4 rounded-lg border border-iris-700 bg-iris-900 p-6 text-center shadow-sm"
    >
      <h1 className="text-lg font-semibold text-iris-300">This topic is out of scope.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-porcelain">
        {message ??
          "Aleph can't build a path on this topic. Try a different topic and we'll draft one."}
      </p>
    </section>
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
    <section
      data-testid="path-failed"
      data-variant="error"
      aria-live="assertive"
      className="mt-4 rounded-lg border border-danger-border/60 bg-danger-bg p-6 text-center shadow-sm"
    >
      <h1 className="text-lg font-semibold text-danger">Generation didn't finish.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-porcelain">
        We couldn't draft this path. Retry when you're ready.
      </p>
      {retryRateLimited ? (
        <p
          data-testid="path-retry-ratelimit"
          className="mx-auto mt-4 max-w-[24rem] text-sm leading-6 text-danger"
        >
          You've reached today's limit for new paths. Try again tomorrow.
        </p>
      ) : null}
      {retryErrored ? (
        <p
          data-testid="path-retry-error"
          className="mx-auto mt-4 max-w-[24rem] text-sm leading-6 text-danger"
        >
          That retry didn't go through. Check your connection and try again.
        </p>
      ) : null}
      <button
        type="button"
        onClick={onRetry}
        disabled={retrying}
        className="mt-6 inline-flex w-full items-center justify-center rounded-md bg-teal px-4 py-3 text-sm font-semibold text-night transition-colors hover:bg-teal-bright disabled:cursor-not-allowed disabled:opacity-50"
      >
        {retrying ? "Retrying…" : "Try again"}
      </button>
    </section>
  );
}

function UnavailableState() {
  return (
    <section
      data-testid="path-unavailable"
      className="mt-4 rounded-lg border border-divider bg-surface p-6 text-center shadow-sm"
    >
      <h1 className="text-lg font-semibold">We couldn't load this path.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        It may have been deleted, or something went wrong. Head back to your paths and try again.
      </p>
    </section>
  );
}

// --- Icons (inline; no icon dependency) -------------------------------------

function CheckIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" aria-hidden="true">
      <path
        d="M3.5 8.5l3 3 6-6.5"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" aria-hidden="true">
      <rect x="3.5" y="7" width="9" height="6" rx="1.2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M5.5 7V5.5a2.5 2.5 0 0 1 5 0V7" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 16 16" className="h-3 w-3" fill="currentColor" aria-hidden="true">
      <path d="M5 3.5v9l7-4.5-7-4.5z" />
    </svg>
  );
}
