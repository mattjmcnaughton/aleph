// The desktop sidebar (Turn 2, mock #2a/#2b) — the left column `components/
// workspace.tsx` mounts inside its `<aside>`. Two independent sections:
// `SwitcherSection` (the "Your paths" switcher, condensed) and `OutlineSection`
// (the current path's rail, condensed). Naming: this column is the **sidebar**,
// never "the rail" — that word already means the units/lessons list inside the
// path view (`path-rail`), and the mock's own name for this column would
// collide with it.

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Children, type ReactNode, useState } from "react";
import { type PathDetail, type PathLesson, pathsListQueryOptions } from "../lib/api";
import { ChevronIcon } from "./chevron-icon";
import { LessonMarker, UNLOCK_STATE_LABEL } from "./lesson-marker";

/** The sidebar shell: stacks whichever sections a route passes, with a hairline
 *  divider between them when there are two (mock #2a — a lesson gets both). */
export function Sidebar({ children }: { children: ReactNode }) {
  const sections = Children.toArray(children).filter(Boolean);
  return (
    <div className="flex flex-col gap-6 px-4 py-6">
      {sections[0]}
      {sections.length > 1 ? <div className="h-px bg-divider" /> : null}
      {sections[1]}
    </div>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" aria-hidden="true">
      <path d="M8 3.5v9M3.5 8h9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

/**
 * The "Your paths" switcher, condensed for the sidebar (mock #2a/#2b top
 * block). Reads `pathsListQueryOptions` with no `refetchInterval` of its own —
 * this is secondary chrome, and the route that owns the page (the switcher
 * itself, or whichever path/lesson is open) already polls the list into the
 * same cache, so this component only ever reads it.
 */
export function SwitcherSection({ currentPathId }: { currentPathId?: string }) {
  const [open, setOpen] = useState(true);
  const pathsQuery = useQuery(pathsListQueryOptions);
  const paths = pathsQuery.data?.paths;

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <button
          type="button"
          data-testid="sidebar-switcher-toggle"
          aria-expanded={open}
          onClick={() => setOpen((wasOpen) => !wasOpen)}
          className="inline-flex items-center gap-1.5 font-mono text-[11px] font-medium uppercase tracking-kicker text-mist transition-colors hover:text-porcelain"
        >
          <ChevronIcon open={open} />
          Your paths
        </button>
        <Link
          to="/new"
          data-testid="sidebar-new-path"
          aria-label="New path"
          title="New path"
          className="grid h-6 w-6 place-items-center rounded-md border border-divider text-mist transition-colors hover:border-teal/50 hover:text-porcelain"
        >
          <PlusIcon />
        </Link>
      </div>

      {/* Chrome, not content: no card for loading/error/empty here — the
          switcher route (or the "Your paths" breadcrumb) is where those get
          their full surface. Delete never lives here either. A failed list
          renders nothing at all rather than a line: "No paths yet" would be a
          claim about the account, and a fetch that failed knows no such thing. */}
      {open ? (
        paths === undefined ? (
          pathsQuery.isError ? null : (
            <p className="pl-1 text-sm text-slate">Loading…</p>
          )
        ) : paths.length === 0 ? (
          <p className="pl-1 text-sm text-slate">No paths yet</p>
        ) : (
          <div className="flex flex-col gap-0.5 pl-1">
            {paths.map((path) => {
              const current = path.id === currentPathId;
              return (
                <Link
                  key={path.id}
                  to="/paths/$pathId"
                  params={{ pathId: path.id }}
                  data-testid="sidebar-path-item"
                  data-path-id={path.id}
                  data-current={current || undefined}
                  className={
                    current
                      ? // `colors.teal.DEFAULT`, not `colors.teal`: the token is an object
                        // (DEFAULT/bright/dim), and `theme()` resolves an object to nothing
                        // — Tailwind then drops the whole utility silently, with no build
                        // error and nothing for jsdom to catch. Spell the leaf.
                        "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium text-porcelain shadow-[inset_2px_0_0_theme(colors.teal.DEFAULT)]"
                      : "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-mist transition-colors hover:bg-surface hover:text-porcelain"
                  }
                >
                  <span className="min-w-0 flex-1 truncate">{path.title}</span>
                  <span
                    className={`shrink-0 font-mono text-[11px] ${current ? "text-teal" : "text-slate"}`}
                  >
                    {path.progress.completed_lessons}/{path.progress.total_lessons}
                  </span>
                </Link>
              );
            })}
          </div>
        )
      ) : null}
    </div>
  );
}

/**
 * The current path's rail, condensed for the sidebar (mock #2a lower block).
 * The header percent is computed the same way `ReadyPath` computes its own —
 * counting `unlock_state === "complete"` over every lesson, never reading
 * `detail.progress` — so the sidebar and the path view can never disagree
 * about a path that both happen to have on screen at once.
 */
export function OutlineSection({
  detail,
  activeLessonId,
}: {
  detail: PathDetail;
  activeLessonId: string;
}) {
  const lessons = detail.units.flatMap((unit) => unit.lessons);
  const total = lessons.length;
  const complete = lessons.filter((lesson) => lesson.unlock_state === "complete").length;
  const percent = total === 0 ? 0 : Math.round((complete / total) * 100);

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-semibold text-porcelain">{detail.title}</p>
        <span className="shrink-0 font-mono text-[11px] text-slate">{percent}%</span>
      </div>
      <div className="mb-5 h-[5px] overflow-hidden rounded-full bg-porcelain/10">
        <span className="block h-full bg-teal" style={{ width: `${percent}%` }} />
      </div>

      <div className="flex flex-col gap-5">
        {detail.units.map((unit, index) => {
          const holdsActive = unit.lessons.some((lesson) => lesson.id === activeLessonId);
          return (
            <div key={unit.id}>
              <p
                className={`mb-2 font-mono text-[11px] font-medium uppercase tracking-kicker ${
                  holdsActive ? "text-teal" : "text-slate"
                }`}
              >
                Unit {String(index + 1).padStart(2, "0")} · {unit.title}
              </p>
              <div className="flex flex-col gap-0.5">
                {unit.lessons.map((lesson) => (
                  <OutlineLessonRow
                    key={lesson.id}
                    lesson={lesson}
                    active={lesson.id === activeLessonId}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// `text-left` is load-bearing, not decoration: a locked row is a <button>, and
// the UA centres button text, so without it a locked lesson's title sits adrift
// of the links above it. The path view's own `LESSON_ROW_BASE` carries it for
// the same reason.
const OUTLINE_ROW_BASE = "flex items-center gap-2.5 rounded-md px-3 py-[7px] text-left text-sm";

function OutlineLessonRow({ lesson, active }: { lesson: PathLesson; active: boolean }) {
  const locked = lesson.unlock_state === "locked";
  const generating = lesson.generation_state === "generating";

  const rowClassName = active
    ? `${OUTLINE_ROW_BASE} border border-teal bg-teal/10 font-medium text-porcelain`
    : locked
      ? `${OUTLINE_ROW_BASE} cursor-not-allowed text-slate`
      : `${OUTLINE_ROW_BASE} text-mist transition-colors hover:bg-surface hover:text-porcelain`;

  const content = (
    <>
      <LessonMarker state={lesson.unlock_state} size="sm" />
      <span className="sr-only">{UNLOCK_STATE_LABEL[lesson.unlock_state]}: </span>
      <span className="min-w-0 flex-1 truncate">{lesson.title}</span>
      {locked && generating ? (
        <span className="shrink-0 font-mono text-[10px] uppercase tracking-kicker text-slate">
          Prep
        </span>
      ) : null}
    </>
  );

  // Locked is a non-interactive marker, never a link — same rule the path-view
  // rail follows (`LessonRow`): `disabled` drops it from the tab order because
  // a locked lesson isn't actionable.
  if (locked) {
    return (
      <button
        type="button"
        disabled
        data-testid="sidebar-lesson-item"
        data-lesson-id={lesson.id}
        data-unlock-state={lesson.unlock_state}
        data-active={active || undefined}
        className={rowClassName}
      >
        {content}
      </button>
    );
  }

  return (
    <Link
      to="/lessons/$lessonId"
      params={{ lessonId: lesson.id }}
      data-testid="sidebar-lesson-item"
      data-lesson-id={lesson.id}
      data-unlock-state={lesson.unlock_state}
      data-active={active || undefined}
      className={rowClassName}
    >
      {content}
    </Link>
  );
}
