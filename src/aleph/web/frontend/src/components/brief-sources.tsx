import { useEffect, useRef } from "react";
import type { Source } from "../lib/api";
import { formatBriefDate } from "../lib/beats";

/**
 * A Source URL safe to put in `href`: absolute `http:` or `https:` with a host,
 * else null (Public profiles TDD §6, SR 9). Source URLs come from retrieval, so
 * a `javascript:` or relative value must never become a link.
 */
export function safeSourceHref(url: string): string | null {
  try {
    const parsed = new URL(url);
    if ((parsed.protocol === "http:" || parsed.protocol === "https:") && parsed.hostname) {
      return parsed.href;
    }
  } catch {
    // Not an absolute URL.
  }
  return null;
}

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
 *
 * **The caller MUST key this component on the Brief's own id** (code-review
 * FIX 3/5, `routes/briefs.$briefId.tsx`). `firedRef`/the disconnected
 * observer only reset on a real unmount+remount — this component has no way
 * to notice "the Brief changed" on its own, since `sources` and
 * `onFirstVisible` are exactly the kind of props that legitimately change on
 * every render. A parent route that re-renders in place across navigation
 * (rather than remounting) MUST force the remount itself via `key`, or this
 * "exactly once" guard becomes "at most once, ever" the moment a second
 * Brief reuses the same instance.
 */
export function BriefSources({
  sources,
  onFirstVisible,
}: {
  sources: Source[];
  /** Omitted on a public Brief, which never pings (Public profiles TDD §6). */
  onFirstVisible?: () => void;
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
          onFirstVisibleRef.current?.();
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
      className="mt-12 rounded-lg bg-elevated px-5 pb-2 pt-5"
    >
      <h2 className="font-serif text-xl font-medium">Sources</h2>
      {/* `list-none` (code-review FIX 7): makes "deliberately unnumbered"
          (PRD Appendix A) an explicit, testable CSS decision on this
          element, rather than something that happened to be true only
          because nothing here ever prints a `source.position` prefix as
          text — a test asserting the *intent* should assert on this class,
          not on `textContent` never containing a marker it was never going
          to contain regardless of list-style. */}
      <ol className="mt-3 list-none">
        {sources.map((source) => (
          <li
            key={source.position}
            data-testid="brief-source"
            className="min-w-0 border-b border-divider py-3 last:border-b-0"
          >
            <SourceTitle source={source} />
            {/* Publisher and date beside the title (PRD §3), truncated so a
                long publisher name can't push the row past 390px. */}
            <p className="min-w-0 truncate text-sm leading-6 text-mist">
              {source.publisher} · {formatBriefDate(source.published_on)}
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}

const SOURCE_TITLE =
  "flex min-h-[44px] flex-col justify-center text-[15px] font-semibold leading-6 text-porcelain";

function SourceTitle({ source }: { source: Source }) {
  const href = safeSourceHref(source.url);
  if (href === null) {
    // Shown, never linked: the learner can still read what was cited.
    return (
      <p className={SOURCE_TITLE}>
        <span>{source.title}</span>
        <span className="break-all text-sm font-normal text-mist">{source.url}</span>
      </p>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={`${SOURCE_TITLE} underline-offset-[3px] hover:underline hover:decoration-teal focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal`}
    >
      {source.title}
    </a>
  );
}
