import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { type PathSummary, pathsListQueryOptions } from "../lib/api";
import { Breadcrumbs } from "../components/breadcrumbs";
import { pickResumeTarget } from "../components/continue-card";
import { PRIMARY_CTA, StateCard } from "../components/state-card";
import { Workspace } from "../components/workspace";
import { useFeatureFlag } from "../lib/feature-flags";
import {
  FLOW_LENGTHS,
  type FlowOrder,
  type FlowRecord,
  NoEligiblePathError,
  startFlow,
} from "../lib/flow";
import { eligiblePaths, lessonsLeft, pickNext } from "../lib/flow-order";

export const Route = createFileRoute("/flow/new")({
  // `?path=` preselects a scope (from a path view's "Start a flow" link, D14);
  // `?length=`/`?order=`/`?paths=` are *Go again*'s prefill (§5.4) — all
  // optional, all read once at mount, never re-read live off the URL.
  validateSearch: (
    search: Record<string, unknown>,
  ): { path?: string; length?: string; order?: string; paths?: string } => ({
    path: typeof search.path === "string" ? search.path : undefined,
    length: typeof search.length === "string" ? search.length : undefined,
    order: typeof search.order === "string" ? search.order : undefined,
    paths: typeof search.paths === "string" ? search.paths : undefined,
  }),
  component: FlowSetup,
});

/** `?length=` -> a Flow length. "open" (and anything unrecognized) is the
 *  segmented control's own default, 5 — matching the mock's default press. */
function lengthFromSearch(value: string | undefined): number | null {
  if (value === "open") return null;
  const n = Number(value);
  return (FLOW_LENGTHS as readonly number[]).includes(n) ? n : 5;
}

function orderFromSearch(value: string | undefined): FlowOrder {
  return value === "random" || value === "serial" ? value : "interleave";
}

// The setup sheet (flow TDD §5.6, mock screen 02). A route, not an overlay
// (D14) — there is no dialog/sheet primitive in the codebase, and a route is
// deep-linkable and testable through the real router like every other
// surface. `<Workspace width="lesson">`, no sidebar, no rail (§2).
function FlowSetup() {
  const navigate = useNavigate();
  const flowEnabled = useFeatureFlag("flow");
  const search = Route.useSearch();
  const pathsQuery = useQuery(pathsListQueryOptions);
  const paths = pathsQuery.data?.paths;

  const [length, setLength] = useState<number | null>(() => lengthFromSearch(search.length));
  const [order, setOrder] = useState<FlowOrder>(() => orderFromSearch(search.order));
  // `null` until the default selection is derived from a loaded paths list
  // (below) — distinct from `[]`, which is a learner having cleared every
  // pick, so the derive-once effect never runs a second time and clobbers it.
  const [selectedIds, setSelectedIds] = useState<string[] | null>(null);
  const [starting, setStarting] = useState(false);

  // Only a `ready` path has anything a flow can read (§5.1 — the sheet reads
  // `GET /paths` only, and a Beat is not a path so it never reaches this list
  // at all). A `ready` path with `next_lesson === null` is finished — listed,
  // disabled, excluded from *Select all* (§5.1, mock pin 3).
  const readyPaths = (paths ?? []).filter((p) => p.status === "ready");
  const selectablePaths = readyPaths.filter((p) => p.next_lesson !== null);

  useEffect(() => {
    if (paths === undefined || selectedIds !== null) return;
    const fromGoAgain = search.paths
      ?.split(",")
      .filter((id) => selectablePaths.some((p) => p.id === id));
    if (fromGoAgain && fromGoAgain.length > 0) {
      setSelectedIds(fromGoAgain);
      return;
    }
    if (search.path && selectablePaths.some((p) => p.id === search.path)) {
      setSelectedIds([search.path]);
      return;
    }
    const resume = pickResumeTarget(paths);
    if (resume && selectablePaths.some((p) => p.id === resume.id)) {
      setSelectedIds([resume.id]);
      return;
    }
    setSelectedIds(selectablePaths[0] ? [selectablePaths[0].id] : []);
    // Every dependency below is read fresh on every run, but the `selectedIds
    // !== null` guard above is what actually makes this "derive the initial
    // selection exactly once": once it has set state, every later run
    // (triggered by a refetch reshaping `selectablePaths`, say) returns
    // before touching it, so the learner's own picks are never overwritten.
  }, [paths, selectedIds, selectablePaths, search.paths, search.path]);

  if (!flowEnabled) {
    return (
      <main className="mx-auto w-full max-w-[480px] px-4 py-8">
        <Breadcrumbs current="Start a flow" />
        <StateCard testid="flow-new-unavailable">
          <h2 className="text-lg font-semibold">Flow isn't available.</h2>
          <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
            Head back home — there's nothing to start from here.
          </p>
          <Link to="/" className="mt-5 inline-block text-sm text-teal">
            Back home
          </Link>
        </StateCard>
      </main>
    );
  }

  const selected = selectedIds ?? [];
  // Scope order: display order of the ready-paths list, filtered to what is
  // selected — the mock's own rule (its script reads `selected()` in DOM
  // order, never click order), and simpler to reason about than tracking a
  // separate click sequence.
  const scopePaths = readyPaths.filter((p) => selected.includes(p.id));
  const scopeIds = scopePaths.map((p) => p.id);
  const left = scopePaths.reduce((sum, p) => sum + lessonsLeft(p), 0);
  const allSelected =
    selectablePaths.length > 0 && selectablePaths.every((p) => selected.includes(p.id));

  function togglePath(pathId: string) {
    setSelectedIds((current) => {
      const base = current ?? [];
      return base.includes(pathId) ? base.filter((id) => id !== pathId) : [...base, pathId];
    });
  }

  function toggleSelectAll() {
    setSelectedIds(allSelected ? [] : selectablePaths.map((p) => p.id));
  }

  const capped = length !== null && length > left;
  const n = length === null ? null : Math.min(length, left);
  const startLabel =
    scopeIds.length === 0
      ? "Pick a path to start"
      : length === null
        ? "Start flow · until I stop"
        : `Start flow · ${n} ${n === 1 ? "lesson" : "lessons"}${capped ? " (all that's left)" : ""}`;

  const firstUp = firstUpText(order, scopePaths);

  function onStart() {
    if (paths === undefined || scopeIds.length === 0 || starting) return;
    setStarting(true);
    try {
      const firstLessonId = startFlow({ length, order, paths: scopeIds }, paths);
      navigate({ to: "/lessons/$lessonId", params: { lessonId: firstLessonId } });
    } catch (error) {
      // NoEligiblePathError is a guard (lib/flow.ts) — the button above is
      // disabled whenever `scopeIds` is empty, so this should be unreachable;
      // a race with a refetch that just emptied the scope degrades to "do
      // nothing" rather than a crashed route.
      if (!(error instanceof NoEligiblePathError)) throw error;
      setStarting(false);
    }
  }

  return (
    <Workspace testid="flow-setup" width="lesson">
      <Breadcrumbs current="Start a flow" />

      {paths === undefined ? (
        pathsQuery.isError ? (
          <StateCard testid="flow-new-unavailable" variant="error">
            <h2 className="text-lg font-semibold">We couldn't load your paths.</h2>
            <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
              Reload the page and try again.
            </p>
          </StateCard>
        ) : (
          <p data-testid="flow-new-loading" className="mt-4 text-sm text-mist">
            Loading your paths…
          </p>
        )
      ) : selectablePaths.length === 0 ? (
        <StateCard testid="flow-new-empty">
          <h2 className="text-lg font-semibold">Nothing to flow yet.</h2>
          <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
            Every path is finished, or there isn't one yet. Start a new path first.
          </p>
          <Link to="/new" className={`mt-5 ${PRIMARY_CTA}`}>
            New path
          </Link>
        </StateCard>
      ) : (
        <div className="flex flex-col gap-7">
          <div>
            <p className="kicker">How many lessons</p>
            <div className="mt-3 grid grid-cols-4 gap-2">
              {FLOW_LENGTHS.map((option) => (
                <LengthButton
                  key={option}
                  value={option}
                  active={length === option}
                  onSelect={() => setLength(option)}
                />
              ))}
              <button
                type="button"
                data-testid="flow-length-open"
                aria-pressed={length === null}
                onClick={() => setLength(null)}
                className={`col-span-1 rounded-md px-2 py-3 text-[13px] font-semibold transition-colors ${
                  length === null ? "bg-teal text-night" : "bg-elevated text-mist"
                }`}
              >
                Until I stop
              </button>
            </div>
          </div>

          <div>
            <div className="flex items-baseline justify-between gap-2">
              <p className="kicker">From which paths</p>
              <div className="flex items-baseline gap-3">
                <span className="text-xs text-slate">
                  {left} {left === 1 ? "lesson" : "lessons"} left
                </span>
                <button
                  type="button"
                  data-testid="flow-select-all"
                  aria-pressed={allSelected}
                  onClick={toggleSelectAll}
                  className="text-xs font-semibold text-teal-bright hover:text-porcelain"
                >
                  {allSelected ? "Clear" : "Select all"}
                </button>
              </div>
            </div>
            <div className="mt-3 flex flex-col gap-2">
              {readyPaths.map((path) => (
                <PathPick
                  key={path.id}
                  path={path}
                  selected={selected.includes(path.id)}
                  onToggle={() => togglePath(path.id)}
                />
              ))}
            </div>
          </div>

          {scopeIds.length >= 2 ? (
            <div>
              <p className="kicker">Order</p>
              <div className="mt-3 flex flex-col gap-2">
                <OrderOption
                  testid="flow-order-interleave"
                  active={order === "interleave"}
                  label="Interleave"
                  hint="Take turns, in order"
                  onSelect={() => setOrder("interleave")}
                />
                <OrderOption
                  testid="flow-order-random"
                  active={order === "random"}
                  label="Random"
                  hint="A random path, every lesson"
                  onSelect={() => setOrder("random")}
                />
                <OrderOption
                  testid="flow-order-serial"
                  active={order === "serial"}
                  label="One path at a time"
                  hint="Finish the first, then the next"
                  onSelect={() => setOrder("serial")}
                />
              </div>
            </div>
          ) : null}

          <div className="mt-2 flex flex-col gap-3">
            <p data-testid="flow-first-up" className="text-center text-sm text-slate">
              {firstUp}
            </p>
            <button
              type="button"
              data-testid="flow-start"
              disabled={scopeIds.length === 0 || starting}
              onClick={onStart}
              className={PRIMARY_CTA}
            >
              {startLabel}
            </button>
            <Link
              to="/"
              data-testid="flow-not-now"
              className="block text-center text-sm text-mist hover:text-porcelain"
            >
              Not now
            </Link>
          </div>
        </div>
      )}
    </Workspace>
  );
}

function LengthButton({
  value,
  active,
  onSelect,
}: {
  value: number;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={`flow-length-${value}`}
      aria-pressed={active}
      onClick={onSelect}
      className={`rounded-md px-2 py-3 text-[13px] font-semibold tabular-nums transition-colors ${
        active ? "bg-teal text-night" : "bg-elevated text-mist"
      }`}
    >
      {value}
    </button>
  );
}

function PathPick({
  path,
  selected,
  onToggle,
}: {
  path: PathSummary;
  selected: boolean;
  onToggle: () => void;
}) {
  const finished = path.next_lesson === null;
  const left = lessonsLeft(path);
  return (
    <button
      type="button"
      data-testid={`flow-path-${path.id}`}
      aria-pressed={selected}
      disabled={finished}
      onClick={onToggle}
      className={`flex items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors ${
        selected ? "bg-elevated ring-1 ring-teal/55" : "bg-elevated"
      } disabled:cursor-not-allowed disabled:opacity-45`}
    >
      <span
        aria-hidden="true"
        className={`flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-md text-[13px] font-bold ${
          selected
            ? "bg-teal text-night"
            : "bg-transparent text-transparent ring-1 ring-inset ring-faint"
        }`}
      >
        ✓
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13.5px] font-semibold text-porcelain">
          {path.title}
        </span>
        <span className="block truncate text-xs text-mist">
          {finished ? "Finished — nothing left to flow" : `Next: ${path.next_lesson?.title}`}
        </span>
      </span>
      <span className="shrink-0 text-[11.5px] tabular-nums text-slate">{left} left</span>
    </button>
  );
}

function OrderOption({
  testid,
  active,
  label,
  hint,
  onSelect,
}: {
  testid: string;
  active: boolean;
  label: string;
  hint: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={testid}
      aria-pressed={active}
      onClick={onSelect}
      className={`flex items-center gap-2.5 rounded-md px-3 py-2.5 text-left transition-colors ${
        active ? "bg-elevated ring-1 ring-teal/60" : "bg-elevated"
      }`}
    >
      <span
        aria-hidden="true"
        className={`h-4 w-4 shrink-0 rounded-full ring-1 ring-inset ${
          active ? "bg-teal ring-teal" : "ring-faint"
        }`}
      />
      <span className="text-[13px] font-semibold text-porcelain">{label}</span>
      <span className="ml-auto text-right text-[11.5px] text-mist">{hint}</span>
    </button>
  );
}

/**
 * The footer's "First up" line (§5.6). Random with two or more paths never
 * names a lesson — the draw has not happened yet — matching the mock's copy
 * rule verbatim. Otherwise it previews `pickNext`'s deterministic first pick
 * against a throwaway seed record (cursor 0, nothing completed): interleave
 * and serial never touch `rng`, so `Math.random` here is inert.
 */
function firstUpText(order: FlowOrder, scopePaths: PathSummary[]): string {
  if (scopePaths.length === 0) return "Nothing to flow yet";
  if (order === "random" && scopePaths.length > 1) {
    return "First up: whichever path the draw lands on";
  }
  const seed: FlowRecord = {
    version: 1,
    startedAt: "",
    length: null,
    order,
    paths: scopePaths.map((p) => p.id),
    cursor: 0,
    current: null,
    next: null,
    completed: [],
    endedReason: null,
  };
  const picked = pickNext(seed, eligiblePaths(scopePaths, seed.paths), Math.random);
  if (picked === null) return "Nothing to flow yet";
  const path = scopePaths.find((p) => p.id === picked.next.pathId);
  return `First up: ${path?.next_lesson?.title ?? ""}`;
}
