import { Link } from "@tanstack/react-router";
import type { BriefEntry } from "../lib/api";
import { formatBriefDate } from "../lib/beats";
import { SkippedRow } from "./skipped-row";

/**
 * The Beat rail (PRD §3, TDD §8): flat, newest first, each row dated,
 * nothing ever locked — the path rail's position and shape, minus units
 * (PRD §3: "Flat, not grouped" — no month subheadings, deferred per §7.1).
 *
 * `entries` arrives as ONE list, already interleaved and ordered by the
 * server (`dtos/beats.py`'s `BriefEntryDTO` discriminated union — TDD §6:
 * "entries is one list of both kinds, never two arrays"): this component
 * renders it in the order it arrives and never re-sorts or re-merges two
 * arrays of its own. Each row narrows on `entry.kind` rather than assuming a
 * nullable field, since a `SkippedEntry` genuinely carries no `title`/
 * `read_at` to read.
 */
export function BeatRail({ entries }: { entries: BriefEntry[] }) {
  if (entries.length === 0) {
    return (
      <p data-testid="beat-rail-empty" className="mt-8 text-sm text-mist">
        Nothing published yet.
      </p>
    );
  }
  return (
    <ol data-testid="beat-rail" className="mt-8 space-y-3">
      {entries.map((entry) =>
        entry.kind === "skipped" ? (
          <SkippedRow key={entry.id} entry={entry} />
        ) : (
          <PublishedRow key={entry.id} entry={entry} />
        ),
      )}
    </ol>
  );
}

/**
 * A published Brief's row: its number, its date, its title, and an unread
 * marker replacing the path rail's locked/available/complete (PRD §3:
 * "Read/unread replaces locked/available/complete; nothing is ever
 * locked"). Links to the Brief reading surface (AL-531) — the whole row is
 * the tap target, `min-h-[44px]` and the title's `truncate` preserved
 * unchanged from the presentational version this replaces.
 */
function PublishedRow({ entry }: { entry: Extract<BriefEntry, { kind: "published" }> }) {
  const unread = entry.read_at === null;
  return (
    <li
      data-testid="beat-rail-published"
      data-entry-id={entry.id}
      data-unread={unread || undefined}
    >
      <Link
        to="/briefs/$briefId"
        params={{ briefId: entry.id }}
        className="flex min-h-[44px] items-center gap-3 rounded-lg border border-divider bg-surface px-4 py-3 transition-colors hover:border-teal/40"
      >
        {/* Decorative unread marker; the sr-only text below is the
            accessible readout — the dot itself carries no independent
            meaning to AT. */}
        <span
          aria-hidden="true"
          className={`h-2 w-2 shrink-0 rounded-full ${unread ? "bg-teal" : "bg-transparent"}`}
        />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold leading-snug text-porcelain">
            Brief #{entry.number} · {formatBriefDate(entry.published_on)}
          </p>
          <p className="mt-0.5 truncate text-sm text-mist">{entry.title}</p>
        </div>
        <span className="sr-only">{unread ? "Unread" : "Read"}</span>
      </Link>
    </li>
  );
}
