import { Link } from "@tanstack/react-router";

interface PathCrumb {
  id: string;
  topic: string;
}

interface BreadcrumbsProps {
  current: string;
  path?: PathCrumb;
}

/**
 * Stable hierarchy navigation for signed-in surfaces.
 *
 * The trail is intentionally independent of browser history: every ancestor is
 * a real route, so it still works after a refresh or deep link. Long generated
 * labels truncate visually on a phone while their full text remains available
 * to assistive technology and in the native title tooltip.
 */
export function Breadcrumbs({ current, path }: BreadcrumbsProps) {
  return (
    <nav aria-label="Breadcrumb" className="mb-6 min-w-0">
      <ol className="flex min-w-0 items-center gap-2 font-mono text-[11px] tracking-[0.08em]">
        <li className="shrink-0">
          <Link
            to="/"
            className="rounded-sm text-slate transition-colors hover:text-teal focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal"
          >
            Your paths
          </Link>
        </li>

        <Separator />

        {path ? (
          <>
            <li className="min-w-0 max-w-[42%]">
              <Link
                to="/paths/$pathId"
                params={{ pathId: path.id }}
                title={path.topic}
                className="block truncate rounded-sm text-slate transition-colors hover:text-teal focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal"
              >
                {path.topic}
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
