import { Link } from "@tanstack/react-router";
import type { BuildsOn } from "../lib/api";
import { formatBriefDate } from "../lib/beats";

/**
 * `Builds on Brief #N` (PRD §3): the product claim of Brief continuity made
 * visible, and a real link to the previous published Brief — never a stored
 * edge (D1: `builds_on` is a derived `number < :n` read), so this component
 * only ever renders what the API resolved.
 *
 * `buildsOn === null` for Brief #1 (nothing precedes it) and for every
 * Skipped entry (which has no `number` of its own to search below) — this
 * renders **no line at all** in that case, never an empty or disabled one.
 */
export function BuildsOnLine({ buildsOn }: { buildsOn: BuildsOn | null }) {
  if (buildsOn === null) return null;
  return (
    <p className="mt-2">
      <Link
        to="/briefs/$briefId"
        params={{ briefId: buildsOn.id }}
        data-testid="builds-on-line"
        className="inline-flex min-h-[44px] items-center text-sm text-teal underline underline-offset-2 transition-colors hover:text-teal-bright"
      >
        Builds on Brief #{buildsOn.number} ({formatBriefDate(buildsOn.published_on)})
      </Link>
    </p>
  );
}
