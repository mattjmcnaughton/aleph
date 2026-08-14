import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { AlephGlyph } from "../components/aleph-logo";
import {
  type PathStatus,
  type PathStreak,
  type PathSummary,
  beatsListQueryOptions,
  isNotFound,
  isPathListTerminal,
  pathsListQueryOptions,
  progressSummaryQueryOptions,
  reviewSummaryQueryOptions,
} from "../lib/api";
import { ActivityStrip } from "../components/activity-strip";
import { BeatCard } from "../components/beat-card";
import { ContinueCard, pickResumeTarget } from "../components/continue-card";
import { ListRow, type RowVariant, RowTitle, RowActions } from "../components/list-row";
import { type NewMenuItem, NewMenu } from "../components/new-menu";
import { CardsSection } from "../components/review/cards-section";
import { DueTodayCard } from "../components/review/due-today-card";
import { ReviewChip } from "../components/review/review-chip";
import { SectionHeader } from "../components/section-header";
import { PRIMARY_CTA_BASE, StateCard } from "../components/state-card";
import { StreakChip } from "../components/streak-chip";
import { StreakLine } from "../components/streak-line";
import { Workspace } from "../components/workspace";
import { sessionQueryOptions } from "../lib/auth";
import { useFeatureFlag } from "../lib/feature-flags";
import { levelLabel } from "../lib/onboarding";
import { makePollingRefetchInterval } from "../lib/polling";
import { type DeletePath, useDeletePath } from "../lib/use-delete-path";

export const Route = createFileRoute("/")({
  component: Home,
});

const pathsListPollConfig = {
  isTerminal: isPathListTerminal,
  // Same contract the path and lesson views wire up: an error that can never
  // resolve stops the loop instead of re-firing a doomed fetch every 5s.
  isErrorTerminal: isNotFound,
};

// How long the list keeps watching a row that never resolves before it stops
// polling (the AL-063 precedent, `GENERATION_STALL_MS`) — ~45s is the shared
// 2s→5s backoff over roughly a dozen polls. Nothing on screen changes: each row
// links to its own path view, and that is where retry and recovery live. This
// only stops an otherwise-eternal 5s poll behind a permanently stuck row.
const LIST_STALL_MS = 45_000;

/** Where focus goes next: one row's Delete button, or the New path CTA (C3). */
type FocusTarget = { row: string } | "new-path";

/** Stable DOM id per row's Delete button — the focus targets (C3). */
function deleteButtonId(pathId: string): string {
  return `delete-path-${pathId}`;
}

/** The two things a learner can start, in the order the menu offers them. */
const NEW_PATH_ITEM: NewMenuItem = {
  to: "/new",
  label: "New path",
  description: "Learn a topic, lesson by lesson.",
  // The name this control has always had, kept so every surface that reaches
  // for home's primary CTA — including the delete flow's focus fallback below
  // — still finds it when the menu collapses back to a single button.
  testid: "new-path-button",
};

const NEW_BEAT_ITEM: NewMenuItem = {
  to: "/beats/new",
  label: "Deploy analyst",
  description: "Keep watch on a topic that's still moving.",
  testid: "new-beat-menu-item",
};

/** The DOM ids the two collapsible section headers disclose (`aria-controls`). */
const PATHS_REGION_ID = "home-paths-section";
const BEATS_REGION_ID = "home-beats-section";

/** A collapsed section still says what is inside it (the row grammar's voice). */
function countSummary(count: number, noun: string): string {
  return `${count} ${count === 1 ? noun : `${noun}s`}`;
}

// "Your paths" — the switcher (§5.5, TDD §8), and the signed-in home route.
// Placement: the PRD calls it the "'Your paths' list / sidebar switcher" and the
// mock's phone home screen *is* that list, so it lives at `/` rather than on a
// dedicated route; every other surface already links home through the header.
//
// It renders one `GET /paths` payload: topic, level, status and the progress
// roll-up per row (W4 — each path carries its own position, and each row links
// to its own `/paths/$pathId`). "New path" re-enters onboarding (§5.1). Delete
// is destructive and not undoable, so it goes through an inline confirm (W5) —
// no browser `confirm()`, which is unusable on a phone — and removes only the
// target path, updating the list in place.
//
// **Two columns at `lg`** (design critique, theme 3). The page used to be one
// tall stack: a mobile layout rendered on a desktop, with the paths table
// stranded in the middle of 1100px and everything else queued below it. The
// work (paths, Beats) now holds the main column and the day's state (streak,
// due cards, the cards door) sits in a rail beside it. CSS only — one flex
// container, `order` swapped at the breakpoint — so below `lg` the rail's
// content is simply back above the list where it has always been. No
// `matchMedia`, no width-conditional rendering (the D12 rule `workspace.tsx`
// holds to), and crucially no second copy of the markup: this is one tree in
// two presentations, so nothing on this route can render on a phone but not a
// desktop.
function Home() {
  const session = useQuery(sessionQueryOptions);

  // Give up on a row that never resolves rather than poll behind it forever.
  const [stalled, setStalled] = useState(false);

  // Both work lists collapse (design ask). Open is the default and the state is
  // per-visit — deliberately not persisted: a section that is still shut when
  // you come back tomorrow reads as "your paths are gone", which is the one
  // impression this screen must never give. Collapsing is for getting one list
  // out of the way while you work in the other, and that is a visit-length job.
  const [pathsOpen, setPathsOpen] = useState(true);
  const [beatsOpen, setBeatsOpen] = useState(true);

  const pathsQuery = useQuery({
    ...pathsListQueryOptions,
    refetchInterval: stalled ? false : makePollingRefetchInterval(pathsListPollConfig),
  });

  // Streaks (Streaks TDD §8): flag-gated (`skipToken` when off — no request,
  // no rendered surface) and un-polled (§7 — nothing about a streak arrives
  // asynchronously). `progressQuery.data` flows straight into `StreakLine`/
  // `ActivityStrip`/`StreakChip` without a branch on `isError` anywhere here:
  // those components already render nothing for `undefined`, which is what
  // makes a failed summary query fail as decoration (TDD §5.4's last row)
  // rather than as a route that forgot to guard it.
  const streaksEnabled = useFeatureFlag("streaks");
  const progressQuery = useQuery(progressSummaryQueryOptions(streaksEnabled));
  const pathStreaks = new Map<string, PathStreak>(
    (progressQuery.data?.paths ?? []).map((streak) => [streak.path_id, streak]),
  );

  // The retention loop's home surfaces (Phase 3 TDD §8): the *Due today* card
  // and each row's `Review N` chip, both decoration on this route the same
  // way the streak line is — `enabled` off means `skipToken` (no flag, no
  // fetch), and both read `reviewSummaryQuery.data` with no `isError` branch
  // here, so a failed `GET /reviews/summary` fails as decoration rather than
  // taking the paths list down with it (TDD §5.6's last row).
  const flashcardsEnabled = useFeatureFlag("flashcards");
  const reviewSummaryQuery = useQuery(reviewSummaryQueryOptions(flashcardsEnabled));
  // `ReviewSummaryResponse.paths` carries counts only, never titles (TDD §6) —
  // this is what lets `DueTodayCard`'s provenance line name them anyway.
  const pathTitles = new Map<string, string>(
    (pathsQuery.data?.paths ?? []).map((path) => [path.id, path.title]),
  );
  const reviewDueByPath = new Map<string, number>(
    (reviewSummaryQuery.data?.paths ?? []).map((path) => [path.path_id, path.due_count]),
  );

  // The Beats section (Phase 6 TDD §8, AL-530): a section **beside** "Your
  // paths", never merged (PRD §4.10 — a Beat is not a path, and Aleph never
  // blurs them for the learner). `enabled` off means `skipToken` — no
  // request, no rendered section — and unlike the paths list this query is
  // never polled (TDD §7: "nothing polls the beats list"), so it is a plain
  // `useQuery` with no `refetchInterval` at all.
  const analystEnabled = useFeatureFlag("analyst");
  const beatsQuery = useQuery(beatsListQueryOptions(analystEnabled));
  const beats = beatsQuery.data?.beats;

  // Armed only while some row is still non-terminal; a poll that resolves the
  // last one flips this false and tears the timer down.
  const awaitingResolution = pathsQuery.data !== undefined && !isPathListTerminal(pathsQuery.data);

  useEffect(() => {
    if (!awaitingResolution) return;
    const timer = setTimeout(() => setStalled(true), LIST_STALL_MS);
    return () => clearTimeout(timer);
  }, [awaitingResolution]);

  // Focus must never drop to <body> in the destructive flow (C3): both the
  // confirm step and the deleted row take the focused element away with them.
  // The next home for focus is decided when that happens and applied once the
  // DOM has caught up, since the target may not exist yet at decision time.
  const newPathRef = useRef<HTMLButtonElement>(null);
  const pendingFocus = useRef<FocusTarget | null>(null);

  // Deliberately dependency-less: it has to run after *every* commit, because
  // the commit that removes a row is the one that renders the target.
  useEffect(() => {
    const target = pendingFocus.current;
    if (target === null) return;
    pendingFocus.current = null;
    const row = target === "new-path" ? null : document.getElementById(deleteButtonId(target.row));
    // Falls back to the New path CTA: the row we aimed at may be gone too
    // (a list that refetched into a different shape), and body is never right.
    (row ?? newPathRef.current)?.focus();
  });

  // Confirm step + DELETE + cache surgery all live in the hook; the route is
  // left rendering rows and deciding where focus lands. One confirm at a time,
  // so the learner never faces two armed destructive buttons.
  const deletion = useDeletePath({
    // Cancelling restores the row's own Delete button, which replaces the
    // "Keep it" button the learner is standing on.
    onCancelled: (id) => {
      pendingFocus.current = { row: id };
    },
    // The deleted row is about to vanish. `pathsQuery.data` here still describes
    // the list *including* it, which is exactly what names its successor.
    onDeleted: (id) => {
      const rows = pathsQuery.data?.paths ?? [];
      const index = rows.findIndex((path) => path.id === id);
      const next = index === -1 ? undefined : rows[index + 1];
      pendingFocus.current = next ? { row: next.id } : "new-path";
    },
  });

  const firstName = session.data?.authenticated
    ? session.data.user.display_name.split(" ")[0]
    : "learner";
  const paths = pathsQuery.data?.paths;
  // A finished path is the one path on the screen that needs nothing from the
  // learner, and it was carrying the most eye-catching progress bar on it (a
  // full teal one). Splitting the list is cheaper than styling that away, and
  // it shortens the working list too — both halves of the same complaint.
  const activePaths = paths?.filter((path) => !isFinished(path));
  const finishedPaths = paths?.filter(isFinished) ?? [];
  const resumeTarget = pickResumeTarget(paths);

  return (
    <Workspace testid="paths-switcher" width="switcher">
      <div className="lg:flex lg:items-end lg:justify-between lg:gap-8">
        <div className="min-w-0">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight">
            Welcome back, {firstName}.
          </h1>
          {/* Onboarding copy, shown once it can still teach something. A
              returning learner with paths already knows what Aleph does, and
              was being told every visit; the empty state below says the same
              thing where it is genuinely the first thing on the screen. */}
          {paths !== undefined && paths.length === 0 ? (
            <p data-testid="home-intro" className="mt-3 text-base leading-6 text-mist">
              Name a topic and Aleph drafts a learning path you can work through, lesson by lesson.
            </p>
          ) : null}
        </div>

        {/* One control for both kinds of top-level thing (a path, a Beat) —
            a menu when the analyst flag makes that a real choice, and the
            plain "New path" button it has always been when it does not. */}
        <NewMenu
          triggerRef={newPathRef}
          items={analystEnabled ? [NEW_PATH_ITEM, NEW_BEAT_ITEM] : [NEW_PATH_ITEM]}
        />
      </div>

      {/* One tap back into yesterday's work, above everything else on the page
          — and outside the two-column split below, so it stays first at every
          width rather than sliding into a rail. */}
      <ContinueCard path={resumeTarget} />

      <div className="mt-2 flex flex-col lg:mt-6 lg:flex-row lg:items-start lg:gap-8">
        {/* The day's state. `order-first`/`lg:order-last` is the whole of the
            desktop rail: on a phone this stays exactly where it has always
            been (above the paths list), and at `lg` it moves to a column
            beside the work instead of pushing it further down the page. */}
        <aside
          data-testid="today-rail"
          className="order-first lg:order-last lg:w-[260px] lg:shrink-0"
        >
          {/* Both read straight off `progressQuery.data` and render nothing
              when it is `undefined` — loading, flag off, or a failed GET all
              look the same to the paths list, which is the whole point. No
              wrapper margin when there is nothing to show: an empty decoration
              must not even cost the layout a gap. */}
          {progressQuery.data !== undefined ? (
            <div className="mt-6 lg:mt-0">
              <StreakLine summary={progressQuery.data} />
              <ActivityStrip activity={progressQuery.data.activity} />
            </div>
          ) : null}

          <DueTodayCard summary={reviewSummaryQuery.data} pathTitles={pathTitles} />

          {/* Home's one door into `/cards` (AL-410 review finding 1) — gated
              only on the `flashcards` flag, never on `due_count`: `DueTodayCard`
              above hides outright at zero due (PRD §3's own restraint for that
              card), which would otherwise leave a learner with kept cards and
              nothing due today with no in-app route to browse them, on exactly
              the day they would want one. */}
          {flashcardsEnabled ? <CardsSection summary={reviewSummaryQuery.data} /> : null}
        </aside>

        <div className="min-w-0 lg:flex-1">
          {/* Collapsible (both lists are): a learner with eight paths and a
              handful of Beats has a long page, and the section they are not
              working in today is pure scroll. Collapsed, the header still says
              how much is in there — a disclosure that hides the count as well
              as the rows makes you open it just to find out. */}
          <SectionHeader
            kicker="Your paths"
            spacing="mt-8 lg:mt-0"
            summary={!pathsOpen && paths !== undefined ? countSummary(paths.length, "path") : null}
            collapse={{
              open: pathsOpen,
              onToggle: () => setPathsOpen((wasOpen) => !wasOpen),
              controls: PATHS_REGION_ID,
              testid: "paths-section-toggle",
            }}
          />

          {/* The region element is always in the DOM and always carries the id
              its header points at — `aria-controls` must resolve whether or
              not the section is open, or the toggle references nothing exactly
              when a screen reader is being told it just closed something. Its
              *contents*, on the other hand, are genuinely unmounted rather
              than hidden with CSS: a collapsed section holds no focusable
              rows, no live regions and no delete confirms. */}
          <div id={PATHS_REGION_ID}>
            {!pathsOpen ? null : paths === undefined ? (
              pathsQuery.isError ? (
                <UnavailableState />
              ) : (
                <LoadingState />
              )
            ) : paths.length === 0 ? (
              <EmptyState />
            ) : (
              <>
                <ul
                  data-testid="paths-list"
                  className="mt-3 space-y-3 lg:space-y-0 lg:border-t lg:border-divider"
                >
                  {(activePaths ?? []).map((path) => (
                    <li key={path.id}>
                      <PathRow
                        path={path}
                        deletion={deletion}
                        streak={pathStreaks.get(path.id)}
                        reviewDue={reviewDueByPath.get(path.id)}
                      />
                    </li>
                  ))}
                </ul>

                {finishedPaths.length > 0 ? (
                  <section data-testid="finished-paths">
                    <SectionHeader kicker="Finished" spacing="mt-8" />
                    {/* Dimmed as a group rather than per row: nothing here is
                        asking for anything, and `hover:opacity-100` keeps it
                        fully legible the moment the learner goes looking. */}
                    <ul className="mt-3 space-y-3 opacity-60 transition-opacity hover:opacity-100 lg:space-y-0 lg:border-t lg:border-divider">
                      {finishedPaths.map((path) => (
                        <li key={path.id}>
                          <PathRow
                            path={path}
                            deletion={deletion}
                            streak={pathStreaks.get(path.id)}
                            reviewDue={reviewDueByPath.get(path.id)}
                          />
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}
              </>
            )}
          </div>

          {/* Beats live in a section beside "Your paths", not mixed into it —
              the same row grammar (title, a line of state) with a different
              verb (PRD §3/§4.10), now literally the same `ListRow`.
              `analystEnabled` guards the whole section, not just the query:
              flag off means no rendered surface at all, not an empty one. */}
          {analystEnabled ? (
            <section data-testid="beats-section">
              <SectionHeader
                kicker="Your beats"
                summary={
                  !beatsOpen && beats !== undefined ? countSummary(beats.length, "Beat") : null
                }
                collapse={{
                  open: beatsOpen,
                  onToggle: () => setBeatsOpen((wasOpen) => !wasOpen),
                  controls: BEATS_REGION_ID,
                  testid: "beats-section-toggle",
                }}
                action={{
                  to: "/beats/new",
                  label: "Deploy analyst",
                  testid: "deploy-analyst-button",
                }}
              />

              <div id={BEATS_REGION_ID}>
                {!beatsOpen || beats === undefined ? null : beats.length === 0 ? (
                  <p data-testid="beats-empty" className="mt-3 text-sm text-mist">
                    No Beats yet. Deploy one to keep watch on a topic that's still moving.
                  </p>
                ) : (
                  <ul
                    data-testid="beats-list"
                    className="mt-3 space-y-3 lg:space-y-0 lg:border-t lg:border-divider"
                  >
                    {beats.map((beat) => (
                      <li key={beat.id}>
                        <BeatCard beat={beat} />
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>
          ) : null}
        </div>
      </div>
    </Workspace>
  );
}

// --- One row ----------------------------------------------------------------

/** Card treatment per status — refusal is iris, failure is danger (CONTEXT). */
const ROW_VARIANT: Partial<Record<PathStatus, Exclude<RowVariant, "neutral">>> = {
  refused: "refusal",
  failed: "error",
};

/** A path with every lesson done — sorted into its own group, not the work. */
function isFinished(path: PathSummary): boolean {
  const { completed_lessons: done, total_lessons: total } = path.progress;
  return path.status === "ready" && total > 0 && done === total;
}

/**
 * The learner-facing one-liner per row. `ready` splits on progress so a finished
 * path reads as complete; the two non-ready branches keep the distinction the
 * full surfaces make — a refusal is out of scope (never "something went wrong"),
 * a failure is a generation error the learner retries from the path view.
 */
function statusLabel(path: PathSummary): string {
  switch (path.status) {
    case "refused":
      return "This topic is out of scope.";
    case "failed":
      return "Generation didn't finish. Open to retry.";
    case "ready": {
      const { completed_lessons: done, total_lessons: total } = path.progress;
      if (total > 0 && done === total) return "Complete";
      return done === 0 ? "Not started" : "In progress";
    }
    case "pending":
    case "generating":
      return "Drafting your path…";
    default: {
      // Exhaustive: a status added to `PathStatus` fails the build here rather
      // than silently reading as "Drafting your path…" on a shipped row.
      const unhandled: never = path.status;
      return unhandled;
    }
  }
}

function PathRow({
  path,
  deletion,
  streak,
  reviewDue,
}: {
  path: PathSummary;
  deletion: DeletePath;
  /** This path's row in the summary's `paths` (Streaks TDD §6); absent means
   *  zero (D5) — the caller (`Home`) already resolved the lookup, so this
   *  component never sees `path.id` and the summary side by side. */
  streak: PathStreak | undefined;
  /** This path's share of today's global queue (Phase 3 TDD §6/§8); absent
   *  means zero (D5), resolved by the caller the same way `streak` is. */
  reviewDue: number | undefined;
}) {
  // One lookup: `variant` drives the styling, and the raw (possibly undefined)
  // value is what `data-variant` exposes to tests — a neutral row carries none.
  const rowVariant = ROW_VARIANT[path.status];
  const variant = rowVariant ?? "neutral";
  const { completed_lessons: done, total_lessons: total } = path.progress;
  const percent = total === 0 ? 0 : Math.round((done / total) * 100);

  // The confirm step replaces the whole row's controls rather than sitting in
  // the actions cell: it is a paragraph plus two buttons, and squeezing that
  // into a track sized for one button wraps it into nonsense.
  const confirming = deletion.confirmingId === path.id;

  return (
    <ListRow
      testid="path-list-item"
      variant={variant}
      dataAttrs={{
        "data-path-id": path.id,
        "data-status": path.status,
        "data-variant": rowVariant,
      }}
      main={
        <Link
          to="/paths/$pathId"
          params={{ pathId: path.id }}
          data-testid="path-item-open"
          className="block min-w-0 rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal"
        >
          <RowTitle title={path.title} titleTestid="path-item-topic">
            <p
              data-testid="path-item-level"
              className="font-mono text-[11px] uppercase tracking-kicker text-slate"
            >
              {levelLabel(path.level)}
            </p>
            {/* Hidden below 2 days by the chip itself (PRD §4.3) — `?? 0`
                  covers the "absent from `paths`" case (D5) the same way. */}
            <StreakChip days={streak?.current_streak ?? 0} />
          </RowTitle>
        </Link>
      }
      meta={
        total > 0 ? (
          <div className="mt-3 lg:mt-0">
            {/* Decorative bar; the text below is the accessible readout.
                  Neutral fill, not teal (design critique, theme 4): teal was
                  marking the primary CTA, progress, the review chip, section
                  links *and* the Due-today accent all at once, so it had
                  stopped meaning anything. It marks what is actionable now;
                  progress is a fact, not an invitation. */}
            <div
              aria-hidden="true"
              className="h-[6px] overflow-hidden rounded-full bg-porcelain/10"
            >
              <span className="block h-full bg-porcelain/45" style={{ width: `${percent}%` }} />
            </div>
            <p data-testid="path-item-progress" className="mt-2 text-sm text-mist">
              {done} of {total} {total === 1 ? "lesson" : "lessons"} complete
            </p>
          </div>
        ) : null
      }
      status={<span data-testid="path-item-status">{statusLabel(path)}</span>}
      // A sibling of the row's own link, not nested inside it (a link inside
      // a link is invalid HTML and would race the row's own navigation) —
      // this one's destination is a filtered review session, Door 3 (PRD's
      // navigation map), not the path view. `ReviewChip` owns its own
      // visibility guard (absent/zero means no chip, D5).
      chip={<ReviewChip pathId={path.id} dueCount={reviewDue} />}
      actions={
        confirming ? null : (
          <>
            <RowActions>
              <button
                type="button"
                id={deleteButtonId(path.id)}
                data-testid="path-delete-button"
                aria-label={`Delete ${path.title}`}
                onClick={() => deletion.ask(path.id)}
                className="rounded-md border border-divider px-3 py-1.5 text-sm text-mist transition-colors hover:border-danger-border hover:text-danger focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal lg:border-0 lg:px-2"
              >
                Delete
              </button>
            </RowActions>
            {/* Desktop-only row chevron (mock #2c) — decorative, the whole
                  row is a link via `path-item-open`. */}
            <span aria-hidden="true" className="hidden shrink-0 text-slate lg:block">
              ›
            </span>
          </>
        )
      }
      footer={
        confirming ? (
          <DeleteConfirm
            title={path.title}
            onCancel={deletion.cancel}
            onConfirm={() => deletion.confirm(path.id)}
            deleting={deletion.isDeleting(path.id)}
            errored={deletion.isErrored(path.id)}
          />
        ) : null
      }
    />
  );
}

/**
 * The inline confirm step (§5.5): deletion is destructive and not undoable, so
 * it always costs a second, deliberate tap. Inline rather than `window.confirm`
 * — a native dialog isn't part of Nocturne and behaves badly on a phone.
 */
function DeleteConfirm({
  title,
  onCancel,
  onConfirm,
  deleting,
  errored,
}: {
  title: string;
  onCancel: () => void;
  onConfirm: () => void;
  deleting: boolean;
  errored: boolean;
}) {
  // The Delete button the learner just pressed is gone from the DOM, so focus
  // would land on <body>. It goes to the safe default instead — "Keep it", never
  // the destructive button (C3): a stray Enter must not delete a path.
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  return (
    <div className="mt-3 rounded-md border border-danger-border/60 bg-danger-bg p-3">
      <p className="text-sm leading-6 text-porcelain">
        Delete this path and its progress? This can't be undone.
      </p>
      {/*
        The live region is mounted with the confirm step, empty, and the failure
        is inserted into it later. A node that appears *carrying* aria-live is
        commonly not announced at all — the region has to already be there.
        `assertive` matches the sibling failure surfaces (onboarding, path and
        lesson views all announce generation failures assertively).
      */}
      <div aria-live="assertive">
        {errored ? (
          <p data-testid="path-delete-error" className="mt-2 text-sm leading-6 text-danger">
            We couldn't delete that path. Check your connection and try again.
          </p>
        ) : null}
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          data-testid="path-delete-confirm"
          aria-label={`Confirm deleting ${title}`}
          onClick={onConfirm}
          disabled={deleting}
          className="flex-1 rounded-md bg-danger px-3 py-2 text-sm font-semibold text-night transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {deleting ? "Deleting…" : "Delete"}
        </button>
        <button
          type="button"
          ref={cancelRef}
          data-testid="path-delete-cancel"
          onClick={onCancel}
          className="flex-1 rounded-md border border-divider px-3 py-2 text-sm font-semibold text-mist transition-colors hover:text-porcelain"
        >
          Keep it
        </button>
      </div>
    </div>
  );
}

// --- Empty / loading / error ------------------------------------------------

function EmptyState() {
  return (
    <StateCard testid="paths-empty" spacing="mt-3">
      <div className="flex justify-center">
        <AlephGlyph size="md" />
      </div>
      <h2 className="mt-4 text-lg font-semibold">No paths yet</h2>
      <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
        Start your first path — pick something you want to learn and choose how much you already
        know.
      </p>
      <Link to="/new" className={`mt-5 ${PRIMARY_CTA_BASE}`}>
        Start your first path
      </Link>
    </StateCard>
  );
}

function LoadingState() {
  return (
    <p data-testid="paths-loading" className="mt-3 text-sm text-mist">
      Loading your paths…
    </p>
  );
}

function UnavailableState() {
  return (
    <StateCard testid="paths-unavailable" spacing="mt-3">
      <h2 className="text-lg font-semibold">We couldn't load your paths.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        Something went wrong reaching Aleph. Reload the page, or start a new path in the meantime.
      </p>
    </StateCard>
  );
}
