import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { AlephGlyph } from "../components/aleph-logo";
import {
  type PathStatus,
  type PathSummary,
  isNotFound,
  isPathListTerminal,
  pathsListQueryOptions,
} from "../lib/api";
import { PRIMARY_CTA, PRIMARY_CTA_BASE, StateCard } from "../components/state-card";
import { Workspace } from "../components/workspace";
import { sessionQueryOptions } from "../lib/auth";
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
function Home() {
  const session = useQuery(sessionQueryOptions);
  const navigate = useNavigate();

  // Give up on a row that never resolves rather than poll behind it forever.
  const [stalled, setStalled] = useState(false);

  const pathsQuery = useQuery({
    ...pathsListQueryOptions,
    refetchInterval: stalled ? false : makePollingRefetchInterval(pathsListPollConfig),
  });

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

  return (
    <Workspace testid="paths-switcher" width="switcher">
      <div className="lg:flex lg:items-end lg:justify-between lg:gap-8">
        <div className="min-w-0">
          <p className="kicker">Your paths</p>
          <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
            Welcome back, {firstName}.
          </h1>
          <p className="mt-3 text-base leading-6 text-mist">
            Name a topic and Aleph drafts a learning path you can work through, lesson by lesson.
          </p>
        </div>

        <button
          type="button"
          ref={newPathRef}
          data-testid="new-path-button"
          onClick={() => navigate({ to: "/new" })}
          className={`mt-6 ${PRIMARY_CTA} lg:mt-0 lg:w-auto lg:shrink-0`}
        >
          New path
        </button>
      </div>

      {paths === undefined ? (
        pathsQuery.isError ? (
          <UnavailableState />
        ) : (
          <LoadingState />
        )
      ) : paths.length === 0 ? (
        <EmptyState />
      ) : (
        <ul
          data-testid="paths-list"
          className="mt-8 space-y-3 lg:space-y-0 lg:border-t lg:border-divider"
        >
          {paths.map((path) => (
            <li key={path.id}>
              <PathRow path={path} deletion={deletion} />
            </li>
          ))}
        </ul>
      )}
    </Workspace>
  );
}

// --- One row ----------------------------------------------------------------

/** Card treatment per status — refusal is iris, failure is danger (CONTEXT). */
const ROW_VARIANT: Partial<Record<PathStatus, "refusal" | "error">> = {
  refused: "refusal",
  failed: "error",
};

// Phone: a full card fill per status. Desktop (`lg:`): the row becomes a
// table-ish line and the tint becomes a 2px inset bar instead (mock #2c rows
// 4-5) — `lg:shadow-none` for the ordinary row, an inset `lg:shadow-[...]` for
// refusal/error, so exactly one `lg:shadow-*` utility is ever active per row.
const ROW_TONE = {
  neutral: "border-divider bg-surface lg:shadow-none",
  refusal: "border-iris-700 bg-iris-900 lg:shadow-[inset_2px_0_0_theme(colors.iris.700)]",
  error:
    "border-danger-border/60 bg-danger-bg lg:shadow-[inset_2px_0_0_theme(colors.danger.DEFAULT)]",
} as const;

const ROW_BASE =
  "rounded-lg border p-4 shadow-sm lg:flex lg:items-center lg:gap-8 lg:rounded-none lg:border-0 lg:border-b lg:border-divider lg:bg-transparent lg:px-2 lg:py-5";

const ROW_STATUS_TONE = {
  neutral: "text-mist",
  refusal: "text-iris-300",
  error: "text-danger",
} as const;

// How wide the status column gets at `lg` (mock #2c). A neutral row's status is
// two words in a fixed column, so every row's Delete button lines up. Refusal
// and failure are whole sentences and take the flexible column the mock gives
// them instead — those rows carry no progress block, so the width is free, and
// squeezing a sentence into 110px wraps it to three ragged lines.
const ROW_STATUS_WIDTH = {
  neutral: "lg:w-[110px] lg:shrink-0",
  refusal: "lg:min-w-0 lg:flex-1",
  error: "lg:min-w-0 lg:flex-1",
} as const;

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

function PathRow({ path, deletion }: { path: PathSummary; deletion: DeletePath }) {
  // One lookup: `variant` drives the styling, and the raw (possibly undefined)
  // value is what `data-variant` exposes to tests — a neutral row carries none.
  const rowVariant = ROW_VARIANT[path.status];
  const variant = rowVariant ?? "neutral";
  const { completed_lessons: done, total_lessons: total } = path.progress;
  const percent = total === 0 ? 0 : Math.round((done / total) * 100);

  return (
    <div
      data-testid="path-list-item"
      data-path-id={path.id}
      data-status={path.status}
      data-variant={rowVariant}
      className={`${ROW_BASE} ${ROW_TONE[variant]}`}
    >
      <Link
        to="/paths/$pathId"
        params={{ pathId: path.id }}
        data-testid="path-item-open"
        className="block rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal lg:flex lg:flex-1 lg:items-center lg:gap-8"
      >
        <div className="min-w-0 lg:flex-1">
          <p data-testid="path-item-topic" className="text-base font-semibold leading-snug">
            {path.title}
          </p>
          <p
            data-testid="path-item-level"
            className="mt-1 font-mono text-[11px] uppercase tracking-kicker text-slate"
          >
            {levelLabel(path.level)}
          </p>
        </div>

        {total > 0 ? (
          <div className="lg:w-[260px] lg:shrink-0">
            {/* Decorative bar; the text below is the accessible readout. */}
            <div
              aria-hidden="true"
              className="mt-3 h-[6px] overflow-hidden rounded-full bg-porcelain/10 lg:mt-0"
            >
              <span className="block h-full bg-teal" style={{ width: `${percent}%` }} />
            </div>
            <p data-testid="path-item-progress" className="mt-2 text-sm text-mist">
              {done} of {total} {total === 1 ? "lesson" : "lessons"} complete
            </p>
          </div>
        ) : null}

        <p
          data-testid="path-item-status"
          className={`mt-1 text-sm lg:mt-0 ${ROW_STATUS_WIDTH[variant]} ${ROW_STATUS_TONE[variant]}`}
        >
          {statusLabel(path)}
        </p>
      </Link>

      {deletion.confirmingId === path.id ? (
        <DeleteConfirm
          title={path.title}
          onCancel={deletion.cancel}
          onConfirm={() => deletion.confirm(path.id)}
          deleting={deletion.isDeleting(path.id)}
          errored={deletion.isErrored(path.id)}
        />
      ) : (
        <button
          type="button"
          id={deleteButtonId(path.id)}
          data-testid="path-delete-button"
          aria-label={`Delete ${path.title}`}
          onClick={() => deletion.ask(path.id)}
          className="mt-3 rounded-md border border-divider px-3 py-1.5 text-sm text-mist transition-colors hover:border-danger-border hover:text-danger lg:mt-0 lg:shrink-0"
        >
          Delete
        </button>
      )}

      {/* Desktop-only row chevron (mock #2c) — decorative, the whole row is a
          link via `path-item-open`. */}
      <span aria-hidden="true" className="hidden shrink-0 text-slate lg:block">
        ›
      </span>
    </div>
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
    <StateCard testid="paths-empty" spacing="mt-8">
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
    <p data-testid="paths-loading" className="mt-8 text-sm text-mist">
      Loading your paths…
    </p>
  );
}

function UnavailableState() {
  return (
    <StateCard testid="paths-unavailable" spacing="mt-8">
      <h2 className="text-lg font-semibold">We couldn't load your paths.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        Something went wrong reaching Aleph. Reload the page, or start a new path in the meantime.
      </p>
    </StateCard>
  );
}
