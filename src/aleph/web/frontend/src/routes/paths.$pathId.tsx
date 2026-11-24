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
import { TeachingStyleCard } from "../components/teaching-style-card";
import { PRIMARY_CTA, PlayIcon, RetryNotices, Spinner, StateCard } from "../components/state-card";
import { Breadcrumbs } from "../components/breadcrumbs";
import { CopyCredit } from "../components/public-profile";
import { LessonMarker, UNLOCK_STATE_LABEL } from "../components/lesson-marker";
import { ShapingMark, ShapingRail } from "../components/shaping/shaping-rail";
import { useShapingRail } from "../components/shaping/use-shaping-rail";
import { type OutlineUnitView, mergeProposalIntoOutline } from "../lib/shaping";
import type { Proposal } from "../lib/tutor-stream";
import { Sidebar, SwitcherSection } from "../components/sidebar";
import { Workspace } from "../components/workspace";
import { useFeatureFlag } from "../lib/feature-flags";
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
  const flowEnabled = useFeatureFlag("flow");

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
      {detail?.source && detail.status !== "ready" ? (
        <CopyCredit kind="path" source={{ ...detail.source, id: detail.source.path_id }} />
      ) : null}

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
        <ReadyPath
          detail={detail}
          onOpenLesson={openLesson}
          ghostProposal={shaping.ghostProposal}
          flowEnabled={flowEnabled}
        />
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
  ghostProposal,
  flowEnabled,
}: {
  detail: PathDetail;
  onOpenLesson: (lessonId: string) => void;
  /**
   * The pending Proposal previewed as **ghost rows** (Phase 2B AL-331, D14), or
   * null. Merged client-side into the rail below — and *only* into the rail:
   * every count on this page (the progress readout, the continue card, the
   * complete banner) is computed from `detail.units`, because a ghost is a
   * proposal and progress is a fact. Applying is what turns one into the other.
   */
  ghostProposal: Proposal | null;
  /** Flow's client-only gate (flow TDD D10) — the "Start a flow" link. */
  flowEnabled: boolean;
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
      <PathTitle key={detail.id} pathId={detail.id} title={detail.title} />
      {detail.source ? (
        <CopyCredit kind="path" source={{ ...detail.source, id: detail.source.path_id }} />
      ) : null}

      {/* Progress sits directly under the title: it is the first thing a
          returning learner wants to know about a path. */}
      <div className="mt-5 flex items-center gap-4">
        <div
          aria-hidden="true"
          className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-porcelain/10"
        >
          <span className="block h-full rounded-full bg-teal" style={{ width: `${percent}%` }} />
        </div>
        <p data-testid="path-progress" className="shrink-0 text-sm text-mist">
          {complete} of {total} {total === 1 ? "lesson" : "lessons"} complete
        </p>
        <p data-testid="path-progress-percent" aria-hidden="true" className="sr-only">
          {percent}%
        </p>
      </div>

      {allComplete ? (
        <CompleteBanner />
      ) : continueLesson && continueUnit ? (
        <>
          <ContinueCard
            lesson={continueLesson}
            unit={continueUnit}
            total={total}
            started={complete > 0}
          />
          {flowEnabled ? (
            <Link
              to="/flow/new"
              search={{ path: detail.id }}
              data-testid="flow-path-link"
              className="mt-1 inline-flex min-h-11 items-center px-5 text-sm font-semibold text-porcelain transition-colors hover:text-teal-bright lg:px-6"
            >
              Start a flow
            </Link>
          ) : null}
        </>
      ) : null}

      {/* `key` remounts the card per path, like the title above. */}
      <TeachingStyleCard key={detail.id} detail={detail} />

      <ol data-testid="path-rail" className="mt-10 space-y-10">
        {numberUnits(mergeProposalIntoOutline(detail.units, ghostProposal)).map(
          ({ unit, number }) => (
            <UnitBlock key={unit.id} unit={unit} number={number} onOpenLesson={onOpenLesson} />
          ),
        )}
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
        <h1 className="min-w-0 page-title">{title}</h1>
        <button
          type="button"
          onClick={startEditing}
          aria-label="Rename path"
          data-testid="path-title-edit"
          className="-mr-2 grid h-11 w-11 shrink-0 place-items-center rounded-md text-slate transition-colors hover:text-porcelain"
        >
          <PencilIcon />
        </button>
      </div>
    );
  }

  return (
    <form
      className="max-w-[28rem]"
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
        className="w-full rounded-md border border-teal bg-surface px-3 py-1.5 font-serif text-2xl font-medium leading-tight text-porcelain focus:outline-none"
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
    <svg viewBox="0 0 16 16" width="15" height="15" fill="none" aria-hidden="true">
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
 * Pair each unit with the number the rail prints on it — **counted over the real
 * units only**.
 *
 * A ghost unit is a proposal, so it takes no number (it has no place in the
 * path's ordering until Apply gives it one) and, just as importantly, it must
 * not consume one: numbering off the merged array's index would renumber every
 * real unit below a ghost — the second unit of a two-unit path reading "Unit 03"
 * because a proposal is sitting above it. Consent has to come first for the
 * numbering as much as for the rows.
 *
 * With no proposal every unit is real, so this is exactly `index + 1` and the
 * rendered rail is unchanged (W21).
 */
function numberUnits(units: OutlineUnitView[]): Array<{
  unit: OutlineUnitView;
  /** Null for a ghost unit — it is labelled "Proposed unit" instead. */
  number: number | null;
}> {
  let real = 0;
  return units.map((unit) => {
    if (unit.ghost) return { unit, number: null };
    real += 1;
    return { unit, number: real };
  });
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
  // Shown at every width. It used to be desktop-only, which left a phone (the
  // primary client) with no Continue action on a path at all: the learner had
  // to find the one highlighted row in the list.
  return (
    <div
      data-testid="path-continue"
      data-started={started || undefined}
      className="mt-6 rounded-lg border border-divider bg-surface p-5 lg:flex lg:items-center lg:justify-between lg:gap-6 lg:p-6"
    >
      <div className="min-w-0">
        <p className="text-sm leading-5 text-mist">
          {started ? "Pick up where you left off" : "Start your path"}
        </p>
        <p className="mt-1.5 font-serif text-xl font-medium leading-snug tracking-[-0.01em]">
          {lesson.title}
        </p>
        <p className="mt-1 text-sm text-mist">
          {unit.title} — lesson {lesson.position_in_path} of {total}
        </p>
      </div>
      <Link
        to="/lessons/$lessonId"
        params={{ lessonId: lesson.id }}
        data-testid="path-continue-link"
        className="mt-4 inline-flex min-h-11 w-full shrink-0 items-center justify-center gap-2 rounded-md bg-teal px-5 text-[15px] font-semibold text-night transition-colors hover:bg-teal-bright lg:mt-0 lg:w-auto"
      >
        {started ? "Continue" : "Start"}
        <PlayIcon />
      </Link>
    </div>
  );
}

function UnitBlock({
  unit,
  number,
  onOpenLesson,
}: {
  unit: OutlineUnitView;
  /** Its place among the *real* units, or null for a ghost (`numberUnits`). */
  number: number | null;
  onOpenLesson: (lessonId: string) => void;
}) {
  const real = unit.lessons.filter((row) => row.kind === "real").map((row) => row.lesson);
  const unitComplete =
    real.length > 0 && real.every((lesson) => lesson.unlock_state === "complete");
  const inProgress = real.some((lesson) => lesson.unlock_state === "available");

  return (
    <li data-testid={unit.ghost ? "path-rail-ghost-unit" : undefined}>
      <div className="flex items-baseline justify-between gap-4">
        <p className={unit.ghost ? "kicker text-iris-300" : "kicker"}>
          {number === null ? "Proposed unit" : `Unit ${number}`}
        </p>
        {inProgress ? (
          <span className="text-[13px] font-medium text-teal">In progress</span>
        ) : unitComplete ? (
          <span className="text-[13px] text-slate">Complete</span>
        ) : null}
      </div>
      <h2
        className={`mt-1 font-serif text-xl font-medium leading-snug tracking-[-0.01em]${unit.ghost ? " text-iris-300" : ""}`}
      >
        {unit.title}
      </h2>
      {/* A ruled list, not a stack of boxed buttons: the syllabus reads as a
          table of contents, and only the lesson the learner is on is marked. */}
      <ul className="mt-3 border-t border-divider">
        {unit.lessons.map((row) =>
          row.kind === "ghost" ? (
            <li key={row.key} className="border-b border-divider">
              <GhostRow title={row.title} />
            </li>
          ) : (
            <li key={row.lesson.id} className="border-b border-divider">
              <LessonRow lesson={row.lesson} revising={row.revising} onOpen={onOpenLesson} />
            </li>
          ),
        )}
      </ul>
    </li>
  );
}

function GhostRow({ title }: { title: string }) {
  return (
    <div
      data-testid="path-rail-ghost"
      className="flex min-h-[52px] w-full items-center gap-3 bg-iris/[0.07] px-2 py-3 text-left text-[15px] text-porcelain shadow-[inset_2px_0_0_theme(colors.iris.DEFAULT)]"
    >
      <span
        aria-hidden="true"
        className="grid h-6 w-6 shrink-0 place-items-center rounded-full border border-dashed border-iris text-iris"
      >
        <span className="text-xs leading-none">+</span>
      </span>
      <span className="sr-only">Proposed: </span>
      <span className="min-w-0 flex-1 font-medium">{title}</span>
      <span className="shrink-0 text-xs text-iris-300">Proposed</span>
    </div>
  );
}

const LESSON_ROW_BASE =
  "flex min-h-[52px] w-full items-center gap-3 px-2 py-3 text-left text-[15px] leading-snug transition-colors";

const LESSON_ROW_STATE: Record<PathLesson["unlock_state"], string> = {
  complete: "text-mist hover:bg-surface hover:text-porcelain",
  available:
    "bg-teal/[0.08] font-semibold text-porcelain shadow-[inset_2px_0_0_theme(colors.teal.DEFAULT)] hover:bg-teal/[0.13]",
  locked: "cursor-not-allowed text-slate",
};

function LessonRow({
  lesson,
  revising,
  onOpen,
}: {
  lesson: PathLesson;
  /** A pending **Revision** names this lesson — the "will be revised" marker. */
  revising: boolean;
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
      data-revising={revising ? "true" : undefined}
      onClick={() => onOpen(lesson.id)}
      disabled={locked}
      // The marker is iris while a Revision is pending on this row: the lesson
      // is still real and still openable (a proposal changes nothing), but the
      // path rail says an offer is standing on it.
      className={`${LESSON_ROW_BASE} ${
        revising
          ? "bg-iris/[0.07] text-porcelain shadow-[inset_2px_0_0_theme(colors.iris.DEFAULT)]"
          : LESSON_ROW_STATE[lesson.unlock_state]
      }`}
    >
      <LessonMarker state={lesson.unlock_state} />
      <span className="sr-only">{UNLOCK_STATE_LABEL[lesson.unlock_state]}: </span>
      <span className="min-w-0 flex-1">{lesson.title}</span>
      {revising ? (
        <span data-testid="path-rail-revising" className="shrink-0 text-xs text-iris-300">
          Will be revised
        </span>
      ) : generating ? (
        <span className="shrink-0 text-xs text-slate">Preparing</span>
      ) : null}
    </button>
  );
}

function CompleteBanner() {
  return (
    <section
      data-testid="path-complete"
      className="mt-6 rounded-lg border border-divider bg-surface p-5"
    >
      <h2 className="font-serif text-xl font-medium text-porcelain">Path complete.</h2>
      <p className="mt-2 max-w-[32rem] text-sm leading-6 text-mist">
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
      <h1 className="page-title text-mist">Loading your path…</h1>
    </>
  );
}

function GeneratingState() {
  return (
    <StateCard testid="path-generating" ariaLive="polite">
      <Spinner />
      <h1 className="mt-4 text-xl leading-snug">Drafting your path…</h1>
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
      <h1 className="text-xl leading-snug text-iris-300">This topic is out of scope.</h1>
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
      <h1 className="text-xl leading-snug text-danger">Generation didn't finish.</h1>
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
      <h1 className="text-xl leading-snug">We couldn't load this path.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        It may have been deleted, or something went wrong. Head back to your paths and try again.
      </p>
    </StateCard>
  );
}
