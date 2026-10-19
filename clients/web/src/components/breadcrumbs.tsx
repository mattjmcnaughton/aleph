import { Link } from "@tanstack/react-router";

interface PathCrumb {
  id: string;
  /** The learner-editable display label — the topic is never shown here. */
  title: string;
}

interface BreadcrumbsProps {
  current: string;
  path?: PathCrumb;
  /**
   * The root crumb's label (code-review FIX 11). Defaults to `"Your paths"`,
   * every path surface's existing reading — a Beat surface passes
   * `"Your beats"` instead, since a Beat is not a path and the one
   * navigational element that expresses hierarchy must not say otherwise
   * (PRD §4.10). Both root crumbs link to `/`: there is no dedicated Beats
   * list route, home is where "Your beats" already lives (TDD §8).
   */
  root?: string;
}

/**
 * Stable hierarchy navigation for signed-in surfaces.
 *
 * The trail is intentionally independent of browser history: every ancestor is
 * a real route, so it still works after a refresh or deep link. Long generated
 * labels truncate visually on a phone while their full text remains available
 * to assistive technology and in the native title tooltip.
 */
export function Breadcrumbs({ current, path, root = "Your paths" }: BreadcrumbsProps) {
  return (
    <nav aria-label="Breadcrumb" className="mb-6 min-w-0">
      <ol className="flex min-w-0 items-center gap-2 font-mono text-[11px] tracking-[0.08em]">
        <li className="shrink-0">
          <Link
            to="/"
            className="rounded-sm text-slate transition-colors hover:text-teal focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal"
          >
            {root}
          </Link>
        </li>

        <Separator />

        {path ? (
          <>
            <li className="min-w-0 max-w-[42%]">
              <Link
                to="/paths/$pathId"
                params={{ pathId: path.id }}
                title={path.title}
                className="block truncate rounded-sm text-slate transition-colors hover:text-teal focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal"
              >
                {path.title}
              </Link>
            </li>
            <Separator />
          </>
        ) : null}

        <li className="min-w-0 flex-1">
          <span aria-current="page" title={current} className="block truncate text-mist">
            {current}
          </span>
        </li>
      </ol>
    </nav>
  );
}

function Separator() {
  return (
    <li aria-hidden="true" className="shrink-0 text-teal/60">
      ›
    </li>
  );
}
