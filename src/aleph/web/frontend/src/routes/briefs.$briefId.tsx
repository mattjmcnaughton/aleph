import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import {
  BEATS_LIST_QUERY_KEY,
  type ReadPingMarker,
  briefQueryOptions,
  pingBriefRead,
} from "../lib/api";
import { Breadcrumbs } from "../components/breadcrumbs";
import { BriefSources } from "../components/brief-sources";
import { BuildsOnLine } from "../components/builds-on-line";
import { Markdown } from "../components/markdown";
import { StateCard } from "../components/state-card";
import { formatBriefDate } from "../lib/beats";
import { useFeatureFlag } from "../lib/feature-flags";

export const Route = createFileRoute("/briefs/$briefId")({
  component: BriefView,
});

// The Brief reading surface (PRD §3, TDD §8): the lesson view's near-identical
// sibling — title, date, `Builds on Brief #N`, the Markdown body through
// `markdown.tsx` UNTOUCHED (the security boundary for model-written text,
// PRD §2/§7.1 — this route imports it and configures nothing new), then the
// Sources block. No Quick check, no completion, no polling: a Brief is
// immutable once published (CONTEXT.md: Brief), so there is nothing here to
// wait on the way a lesson's `generation_state` or a Beat's `research_state`
// is (TDD §8: "no dedicated poll").
//
// **No optimistic writes anywhere in this route** (TDD §7): neither read
// ping is a number the learner watches increment, so optimism would buy
// nothing and cost a divergence — both mutations below only invalidate.
function BriefView() {
  const { briefId } = Route.useParams();
  const analystEnabled = useFeatureFlag("analyst");
  const queryClient = useQueryClient();

  const briefQuery = useQuery(briefQueryOptions(briefId, analystEnabled));
  const detail = briefQuery.data;

  // The read ping (D11, TDD §6/§9): `opened` on mount, `sources` once on
  // first visibility of the Sources block. One mutation serves both markers.
  //
  // Invalidates the `["beats"]` PREFIX (code-review FIX 1, correcting an
  // AL-531 defect), not `beatQueryKey(beatId)` = `["beats", beatId]` alone.
  // TanStack Query's `invalidateQueries` matches by prefix: a query key must
  // START WITH the filter key to match. `["beats"]` is a prefix of BOTH
  // itself and `["beats", beatId]`, so this one call reaches the list query
  // (`BEATS_LIST_QUERY_KEY`, the *only* carrier of `unread_count` —
  // `BeatSummaryDTO`) AND the rail's own detail query — but the reverse is
  // false: `["beats", beatId]` is never a prefix of the shorter `["beats"]`,
  // so invalidating only the detail key (the original, defective code) can
  // never reach the list no matter what `beatId` is. TDD §7 states the
  // purpose verbatim — "the unread count and the rail's read state move in
  // the same interaction" — and the list is the unread count's only source,
  // so reaching it is not over-invalidation, it is the point. `beatId` is no
  // longer threaded through the mutation's variables at all (unlike the
  // original code): the invalidation target is the fixed `["beats"]` prefix
  // regardless of which Beat this Brief belongs to, so there is nothing
  // per-call left to carry.
  const readPing = useMutation({
    mutationFn: ({ marker }: { marker: ReadPingMarker }) => pingBriefRead(briefId, marker),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: BEATS_LIST_QUERY_KEY });
    },
  });
  const pingRead = readPing.mutate;

  // `opened`, once the Brief actually loads and turns out to have a body —
  // not a bare mount effect (code-review FIX 2, see below), and not fired
  // for a Skipped id's `detail` either.
  //
  // TanStack Router re-renders this route with new params rather than
  // remounting it on Brief-to-Brief navigation (the `Builds on Brief #N`
  // link), so a per-instance flag latches on the first Brief and never
  // re-arms for the next one — the same reasoning `BriefSources` needed a
  // `key` for (code-review FIX 3).
  //
  // **A `Set`, not a single `string | null` (code-review FIX 5).** The
  // original single-value ref only ever remembered the MOST RECENTLY fired
  // id, which reads as "once per Brief id" right up until a learner follows
  // `Builds on Brief #N` (b5 -> b4) and then navigates BACK to a Brief
  // (b4 -> b5) already in the TanStack Query cache: `openedFiredForRef`
  // holds b4's id, not b5's, so the guard's `=== detail.id` check is false
  // for b5 all over again and `opened` fires a SECOND time for a Brief this
  // session already pinged — inert server-side (first-write-wins,
  // `mark_read`), but it breaks the "exactly once" the docstring/tests both
  // claim, and inflates requests on exactly the navigation `Builds on Brief
  // #N` exists to encourage. A `Set` tracks every id this component
  // instance has EVER fired for, in this session, matching the stated
  // intent verbatim rather than only the most recent visit.
  const openedFiredForRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!detail || detail.body_markdown === null || openedFiredForRef.current.has(detail.id)) {
      return;
    }
    openedFiredForRef.current.add(detail.id);
    pingRead({ marker: "opened" });
  }, [detail, pingRead]);

  // A direct/deep link with the flag off (D12: the whole surface is a
  // router-level gate server-side, `404` on every route) — the
  // `beats.$beatId.tsx` FIX 3 dead-end shape, restated here: without this,
  // `briefQueryOptions`' `skipToken` means `briefQuery.data` never resolves
  // and `isError` never flips true, so `LoadingState` below would render
  // "Loading your Brief…" forever instead of a real dead end.
  if (!analystEnabled) {
    return (
      <main className="mx-auto w-full max-w-[480px] px-4 py-8">
        <StateCard testid="brief-unavailable">
          <h2 className="text-lg font-semibold">This Brief isn't available.</h2>
          <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
            Head back home — there's nothing to read from here.
          </p>
        </StateCard>
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-[480px] px-4 py-8">
      {detail ? <Breadcrumbs current={detail.title ?? "Brief"} root="Your beats" /> : null}

      {detail === undefined ? (
        briefQuery.isError ? (
          <UnavailableState />
        ) : (
          <LoadingState />
        )
      ) : detail.body_markdown === null ? (
        // A Skipped entry's id, or a not-yet-published row — the rail never
        // links here (hand-off item 2), but the API resolves the id anyway
        // (`BriefDetailDTO` nulls these fields for exactly that case), so a
        // deep link degrades gracefully instead of rendering an empty page.
        <UnavailableState />
      ) : (
        <>
          <p className="kicker">
            Brief #{detail.number} · {formatBriefDate(detail.published_on)}
          </p>
          <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
            {detail.title}
          </h1>
          <BuildsOnLine buildsOn={detail.builds_on} />

          {/* The body is model-written text; it goes through `markdown.tsx`
              exactly as a lesson's Read passage does, and this route
              configures no second rendering or sanitization path. */}
          <Markdown testid="brief-body" className="mt-6 text-base">
            {detail.body_markdown}
          </Markdown>

          {/* `key={detail.id}` (code-review FIX 3/5) — this route re-renders
              in place across a Brief-to-Brief navigation rather than
              remounting (see `openedFiredForRef`'s own note above), and
              `<BriefSources>` carried no key at all before this fix. Its own
              `firedRef`/disconnected-observer guard only resets on a real
              unmount+remount, so without a key that is tied to the Brief
              actually being shown, navigating to a Brief already sitting in
              the TanStack Query cache (no `undefined` gap for `detail` to
              pass through) reuses the same component instance with
              `firedRef` still `true` and an observer that already
              disconnected — permanently suppressing the `sources` ping for
              every cached Brief reached this way. Keying on `detail.id`
              forces React to tear down and rebuild a fresh instance (fresh
              `firedRef`, fresh observer) exactly when the Brief being shown
              changes, the same fix `openedFiredForRef` above needed and got
              via keying its own ref by id instead. */}
          <BriefSources
            key={detail.id}
            sources={detail.sources}
            onFirstVisible={() => pingRead({ marker: "sources" })}
          />
        </>
      )}
    </main>
  );
}

function LoadingState() {
  return (
    <>
      <p className="kicker">Brief</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        Loading your Brief…
      </h1>
    </>
  );
}

function UnavailableState() {
  return (
    <StateCard testid="brief-unavailable" spacing="mt-6">
      <h2 className="text-lg font-semibold">We couldn't load this Brief.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        It may have been deleted, or something went wrong. Head back to your Beats and try again.
      </p>
    </StateCard>
  );
}
