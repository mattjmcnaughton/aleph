import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { type ReadPingMarker, beatQueryKey, briefQueryOptions, pingBriefRead } from "../lib/api";
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
  // first visibility of the Sources block. One mutation serves both markers
  // — `beatId` rides along in the mutate call's own variables rather than
  // being read from `detail` inside `onSuccess`, so the invalidation target
  // is never stale even if `detail` were somehow to change between the call
  // and its response. Invalidates exactly `["beats", beatId]` (TDD §7) — the
  // unread count and the rail's read state move in the same interaction,
  // nothing more.
  const readPing = useMutation({
    mutationFn: ({ marker }: { marker: ReadPingMarker; beatId: string }) =>
      pingBriefRead(briefId, marker),
    onSuccess: (_result, variables) => {
      void queryClient.invalidateQueries({ queryKey: beatQueryKey(variables.beatId) });
    },
  });
  const pingRead = readPing.mutate;

  // `opened`, once the Brief actually loads — not a bare mount effect, since
  // there is nothing to ping until `detail.beat_id` exists to invalidate.
  // Keyed by `detail.id` (the `draftsTriggeredForRef` precedent,
  // `routes/lessons.$lessonId.tsx`): TanStack Router re-renders this route
  // with new params rather than remounting it on Brief-to-Brief navigation
  // (the `Builds on Brief #N` link), so a per-instance flag would latch on
  // the first Brief and never re-arm for the next one. Firing exactly once
  // per Brief id also covers React's StrictMode double-invoke.
  const openedFiredForRef = useRef<string | null>(null);
  useEffect(() => {
    if (!detail || openedFiredForRef.current === detail.id) return;
    openedFiredForRef.current = detail.id;
    pingRead({ marker: "opened", beatId: detail.beat_id });
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

          <BriefSources
            sources={detail.sources}
            onFirstVisible={() => pingRead({ marker: "sources", beatId: detail.beat_id })}
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
