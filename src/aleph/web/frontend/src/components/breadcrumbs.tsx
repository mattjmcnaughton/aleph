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
  // On a phone the trail is the way back: the ancestors only, at a real touch
  // height. The current page is dropped there (`hidden sm:block`) because the
  // page title directly beneath says the same thing, and squeezing three
  // truncated labels into 390px left none of them readable.
  return (
    <nav aria-label="Breadcrumb" className="-mt-2 mb-4 min-w-0 lg:mb-6">
      <ol className="flex min-w-0 items-center gap-1.5 text-sm">
        <li className="flex shrink-0 items-center">
          <BackChevron />
          <Link to="/" className={CRUMB_LINK}>
            {root}
          </Link>
        </li>

        {path ? (
          <>
            <Separator />
            <li className="min-w-0 sm:max-w-[42%]">
              <Link
                to="/paths/$pathId"
                params={{ pathId: path.id }}
                title={path.title}
                className={`${CRUMB_LINK} max-w-full`}
              >
                <span className="truncate">{path.title}</span>
              </Link>
            </li>
          </>
        ) : null}

        <Separator currentOnly />
        <li className="hidden min-w-0 flex-1 sm:block">
          <span aria-current="page" title={current} className="block truncate text-slate">
            {current}
          </span>
        </li>
      </ol>
    </nav>
  );
}

const CRUMB_LINK =
  "inline-flex min-h-11 items-center rounded-sm text-mist transition-colors hover:text-porcelain";

/** Phone only: the trail reads as "back to", so it leads with a chevron. */
function BackChevron() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      aria-hidden="true"
      className="-ml-1 mr-1 shrink-0 text-slate sm:hidden"
    >
      <path
        d="M10 3.5L5.5 8l4.5 4.5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Separator({ currentOnly = false }: { currentOnly?: boolean }) {
  return (
    <li
      aria-hidden="true"
      className={`shrink-0 text-faint ${currentOnly ? "hidden sm:block" : ""}`}
    >
      /
    </li>
  );
}
