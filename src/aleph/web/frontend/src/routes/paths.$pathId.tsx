import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  PATHS_LIST_QUERY_KEY,
  type PathDetail,
  type PathLesson,
  type PathUnit,
  isNotFound,
  isPathViewTerminal,
  pathQueryKey,
  pathQueryOptions,
  retryPath,
  updatePathTitle,
} from "../lib/api";
import { PRIMARY_CTA, PlayIcon, RetryNotices, Spinner, StateCard } from "../components/state-card";
import { Breadcrumbs } from "../components/breadcrumbs";
import { LessonMarker, UNLOCK_STATE_LABEL } from "../components/lesson-marker";
import { ShapingMark, ShapingRail } from "../components/shaping/shaping-rail";
import { useShapingRail } from "../components/shaping/use-shaping-rail";
import { Sidebar, SwitcherSection } from "../components/sidebar";
import { Workspace } from "../components/workspace";
import { PATH_TITLE_MAX_LENGTH } from "../lib/onboarding";
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
//
// **This route is also the shaping rail's mount** (Phase 2B AL-330, D14): the
// rail tree's third presentation, in `Workspace`'s rail slot, gated on the
// `shaping` flag and on the outline being `ready` (PRD §5.1 — there must be a
// structure to shape). Every non-`ready` branch below therefore renders with no
// rail and no mark, which falls out of `pathReady` rather than being restated
// per branch.
function PathView() {
  const { pathId } = Route.useParams();
  const navigate = useNavigate();

  const pathQuery = useQuery({
    ...pathQueryOptions(pathId),
    refetchInterval: makePollingRefetchInterval(pathViewPollConfig),
  });

  const retry = useRetryGeneration({ mutationFn: retryPath, queryKey: pathQueryKey });

  const detail = pathQuery.data;

  const shaping = useShapingRail({
    pathId,
    title: detail?.title ?? "",
    pathReady: detail?.status === "ready",
  });

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
      tutorRail={shaping.open ? <ShapingRail shaping={shaping} /> : null}
      railTestid="shaping-rail-column"
    >
      {detail ? <Breadcrumbs current={detail.title} /> : null}

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

      {/* The way in, and the way back from the header's collapse. It renders
          itself only when the rail is closed *and* the surface exists at all. */}
      <ShapingMark shaping={shaping} />
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
          {/* `key={detail.id}`: `/paths/A` -> `/paths/B` via the sidebar switcher
              re-renders this route rather than remounting it (same hazard
              `use-shaping-rail.ts`'s `currentPathRef` comment documents), so
              without a key `PathTitle`'s in-progress `draft`/`editing` state
              would survive the switch — open a rename on A, switch to B while
              still editing, press Save, and it PATCHes B with A's typed title.
              The key forces React to remount (and reset that state) on every
              path change instead of hand-rolling a reset effect. */}
          <PathTitle key={detail.id} pathId={detail.id} title={detail.title} />
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
 * The h1: the learner-editable title, inline-editable in place (§5.5-adjacent —
 * the h1 itself, not a separate settings surface). Never the topic: `title` is
 * display-only and always populated (the server applies the fallback), so this
 * is the one place on the path view that writes back to the server.
 *
 * Non-optimistic, matching `use-delete-path`'s house rule: the h1 keeps showing
 * the last-known title (the `title` prop, straight from the cached query) until
 * the PATCH actually succeeds — nothing is guessed at.
 */
function PathTitle({ pathId, title }: { pathId: string; title: string }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);

  const mutation = useMutation({
    mutationFn: updatePathTitle,
    onSuccess: (updated) => {
      // Write the full detail straight into the poll cache — same shape as the
      // `GET` it replaces — then reach the switcher row too, exactly the way a
      // completion reaches both surfaces off one server round trip.
      queryClient.setQueryData(pathQueryKey(pathId), updated);
      queryClient.invalidateQueries({ queryKey: PATHS_LIST_QUERY_KEY });
      setEditing(false);
    },
    // Deliberately no onError handling beyond `mutation.isError`: the input
    // stays open with whatever the learner typed (never lost), and the inline
    // error below is driven straight off mutation state.
  });

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  function startEditing() {
    mutation.reset();
    setDraft(title);
    setEditing(true);
  }

  function cancel() {
    mutation.reset();
    setEditing(false);
  }

  function save() {
    const trimmed = draft.trim();
    if (!trimmed || mutation.isPending) return;
    mutation.mutate({ pathId, title: trimmed });
  }

  if (!editing) {
    return (
      <div className="flex items-start gap-1.5">
        <h1 className="mt-2 min-w-0 text-3xl font-semibold leading-tight tracking-tight">
          {title}
        </h1>
        <button
          type="button"
          onClick={startEditing}
          aria-label="Rename path"
          data-testid="path-title-edit"
          className="mt-2 shrink-0 rounded-md p-1.5 text-slate transition-colors hover:text-porcelain focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal"
        >
          <PencilIcon />
        </button>
      </div>
    );
  }

  return (
    <form
      className="mt-2 max-w-[28rem]"
      onSubmit={(event) => {
        event.preventDefault();
        save();
      }}
    >
      <label htmlFor="path-title-input" className="sr-only">
        Path title
      </label>
      <input
        id="path-title-input"
        ref={inputRef}
        type="text"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          // Handled explicitly rather than relying on the form's native submit-
          // on-Enter: jsdom does not implement implicit form submission, and an
          // explicit handler is also the one place Escape's cancel can live.
          if (event.key === "Escape") {
            event.preventDefault();
            cancel();
          } else if (event.key === "Enter") {
            event.preventDefault();
            save();
          }
        }}
        maxLength={PATH_TITLE_MAX_LENGTH}
        autoComplete="off"
        data-testid="path-title-input"
        className="w-full rounded-md border border-teal bg-surface px-3 py-1.5 text-2xl font-semibold leading-tight tracking-tight text-porcelain focus:outline-none"
      />
      <div className="mt-2 flex gap-2">
        <button
          type="submit"
          disabled={mutation.isPending || draft.trim().length === 0}
          data-testid="path-title-save"
          className="rounded-md bg-teal px-4 py-1.5 text-sm font-semibold text-night transition-colors hover:bg-teal-bright disabled:cursor-not-allowed disabled:opacity-50"
        >
          {mutation.isPending ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={cancel}
          data-testid="path-title-cancel"
          className="rounded-md border border-divider px-4 py-1.5 text-sm font-semibold text-mist transition-colors hover:text-porcelain"
        >
          Cancel
        </button>
      </div>
      {mutation.isError ? (
        <p
          role="alert"
          data-testid="path-title-error"
          className="mt-2 text-sm leading-6 text-danger"
        >
          Couldn't save that name. Check your connection and try again.
        </p>
      ) : null}
    </form>
  );
}

function PencilIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" aria-hidden="true">
      <path
        d="M11.4 2.4l2.2 2.2-7.7 7.7H3.5v-2.4l7.9-7.5z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
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
