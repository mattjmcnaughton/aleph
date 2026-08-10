import { useEffect, useRef } from "react";
import type { Source } from "../lib/api";
import { formatBriefDate } from "../lib/beats";

/**
 * The Sources block (PRD §3, CONTEXT.md: Source): "the part a learner should
 * be able to check us on", so it is a **first-class region of the page** —
 * body text size, an `elevated` surface, publisher and date beside each
 * title, the URL a real link — never a footnote in small grey type.
 *
 * Deliberately unnumbered (PRD Appendix A: "numbers would imply the inline
 * markers §7.1 defers") — `source.position` orders the list (the API's own
 * order) but is never printed.
 *
 * Fires `onFirstVisible` **exactly once**, on first visibility, via an
 * `IntersectionObserver` on the block's own container (TDD §8/§9 — this is
 * PRD §5's **Depth of read** signal). Two guards make "exactly once" hold
 * even across a block that scrolls in and out of view repeatedly: the
 * observer disconnects itself the instant it sees an intersecting entry, and
 * a `firedRef` boolean blocks re-entry even if a stray callback still landed
 * before the disconnect took effect. `onFirstVisible` is read through a ref
 * (`onFirstVisibleRef`) rather than the effect's own dependency array, so a
 * caller passing a fresh closure every render — the ordinary React shape —
 * never tears down and rebuilds the observer mid-flight.
 */
export function BriefSources({
  sources,
  onFirstVisible,
}: {
  sources: Source[];
  onFirstVisible: () => void;
}) {
  const containerRef = useRef<HTMLElement | null>(null);
  const firedRef = useRef(false);
  const onFirstVisibleRef = useRef(onFirstVisible);
  onFirstVisibleRef.current = onFirstVisible;

  useEffect(() => {
    const el = containerRef.current;
    // No Sources block to observe (a route that somehow reaches here with
    // none — never a published Brief in practice, D8/§5.5) — nothing to wire
    // up, and no ping to fire for a region that does not exist.
    if (!el || firedRef.current) return;
    // jsdom carries no IntersectionObserver by default; a test suite that
    // wants the ping stubs the global. Its absence degrades to "never fires"
    // rather than a crash.
    if (typeof IntersectionObserver === "undefined") return;

    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting && !firedRef.current) {
          firedRef.current = true;
          onFirstVisibleRef.current();
          observer.disconnect();
          break;
        }
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
    // Deps intentionally `[]`: `containerRef`/`firedRef` are refs (stable
    // identity), and `onFirstVisible` is read through `onFirstVisibleRef`
    // above rather than depended on here — see the docstring.
  }, []);

  if (sources.length === 0) return null;

  return (
    <section
      ref={containerRef}
      data-testid="brief-sources"
      aria-label="Sources"
      className="mt-10 rounded-lg border border-divider bg-elevated p-5"
    >
      <p className="kicker">Sources</p>
      <ol className="mt-4 space-y-4">
        {sources.map((source) => (
          <li key={source.position} data-testid="brief-source" className="min-w-0">
            <a
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex min-h-[44px] flex-col justify-center text-base font-semibold leading-6 text-porcelain underline-offset-2 hover:text-teal hover:underline"
            >
              {source.title}
            </a>
            {/* Publisher and date beside the title (PRD §3), truncated so a
                long publisher name can't push the row past 390px. */}
            <p className="mt-1 min-w-0 truncate text-sm leading-6 text-mist">
              {source.publisher} · {formatBriefDate(source.published_on)}
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}
