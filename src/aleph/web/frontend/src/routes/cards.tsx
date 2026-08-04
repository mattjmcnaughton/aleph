// `/cards` (AL-410): browse every kept card, search or filter it by path,
// edit one's text inline, or drop one — the surface the Daily queue alone
// never gave a learner (it shows at most ten cards, only the ones due
// *today*, PRD §4.4/§4.8 — there was no way to find a specific card, fix a
// typo, or discard one that stopped earning its keep). Reached from the two
// entry points the plan actually specifies (`routes/index.tsx`'s own
// always-rendered link, `session-complete.tsx`) rather than a new app-bar
// item — PRD §3 is explicit that the due pill is the one piece of persistent
// navigation this phase adds, and that rule does not get relitigated for a
// second surface.

import { skipToken, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { CardRow, cardDeleteButtonId } from "../components/review/card-row";
import { StateCard } from "../components/state-card";
import { Workspace } from "../components/workspace";
import { cardsQueryOptions, listPaths, pathsListQueryOptions } from "../lib/api";
import { useFeatureFlag } from "../lib/feature-flags";
import { useDeleteCard } from "../lib/use-delete-card";

export const Route = createFileRoute("/cards")({
  // `path` rides the URL — the same "Door 3" shape `routes/review.tsx` gives
  // its own `?path=` filter (a deep link is shareable and survives a reload).
  // `q` deliberately stays component state instead (below): a search box that
  // rewrote the URL on every keystroke would spam browser history, and there
  // is no deep-link use case for "open /cards already searching for X" the
  // way there is for "open /review already scoped to this path".
  validateSearch: (search: Record<string, unknown>): { path?: string } => ({
    path: typeof search.path === "string" ? search.path : undefined,
  }),
  component: CardsPage,
});

/** How long to let typing settle before the search box re-queries
 *  (`GET /flashcards?q=…`) — long enough that ordinary typing never fires a
 *  request per keystroke, short enough that the list still reads as live. */
const SEARCH_DEBOUNCE_MS = 300;

function CardsPage() {
  const search = Route.useSearch();
  const pathId = search.path ?? null;

  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState<string | null>(null);
  useEffect(() => {
    const timer = setTimeout(
      () => setQ(qInput.trim() === "" ? null : qInput.trim()),
      SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [qInput]);

  const flashcardsEnabled = useFeatureFlag("flashcards");
  const cardsQuery = useInfiniteQuery(cardsQueryOptions(flashcardsEnabled, { pathId, q }));
  // Chip labels only — `GET /flashcards` names a card's *lesson's* path only
  // inside its citation, and only when that citation still links (D12), never
  // a bare `path_id`/title pair a filter chip could read directly. Unlike
  // `routes/index.tsx`'s own "Your paths" fetch (unconditional there — the
  // switcher needs it regardless of `flashcards`), this one only exists to
  // label filter chips on a route the flag can gate off entirely, so it rides
  // the same `skipToken` gate every other flashcards fetch on this page does
  // (AL-410 review finding 6) — the flag-off render below must never fire
  // `GET /paths` a second time on top of `routes/index.tsx`'s own.
  const pathsQuery = useQuery({
    ...pathsListQueryOptions,
    queryFn: flashcardsEnabled ? listPaths : skipToken,
  });

  const searchInputRef = useRef<HTMLInputElement>(null);

  const deletion = useDeleteCard({
    // Cancelling restores this row's own Delete button, which replaces the
    // "Keep it" button the learner is standing on (mirrors `routes/index.tsx`).
    onCancelled: (id) => {
      document.getElementById(cardDeleteButtonId(id))?.focus();
    },
    // The deleted row is gone from the next render, and nothing on `/cards`
    // stands in for "the next row" the way a switcher row's neighbour does
    // (`routes/index.tsx`'s `pendingFocus` target-picking has no equivalent
    // here worth building) — so focus goes to the one other stable control on
    // the page instead of dropping to <body> (the same C3 rule).
    onDeleted: () => {
      searchInputRef.current?.focus();
    },
  });

  // A direct/deep link with the flag off (D10: the whole surface is a
  // router-level gate server-side, `404` on every route) — the frontend's own
  // dead end, matching `routes/review.tsx`'s shape exactly. Shares its testid
  // with `UnavailableState` below (a genuine fetch failure) the same way
  // `review-unavailable` does for `routes/review.tsx`: the two can never
  // render at once, so one testid serves both.
  if (!flashcardsEnabled) {
    return (
      <Workspace testid="cards-page" width="switcher">
        <StateCard testid="cards-unavailable">
          <h1 className="text-lg font-semibold">Your cards aren't available.</h1>
          <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
            Head back home — there's nothing to browse from here.
          </p>
          <Link to="/" className="mt-5 inline-block text-sm text-teal">
            Back to your paths
          </Link>
        </StateCard>
      </Workspace>
    );
  }

  const cards = cardsQuery.data?.pages.flatMap((page) => page.cards) ?? [];
  const paths = pathsQuery.data?.paths ?? [];
  const filtered = pathId !== null || q !== null;

  return (
    <Workspace testid="cards-page" width="switcher">
      <p className="kicker">Your cards</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        Everything you've kept.
      </h1>
      <p className="mt-3 text-base leading-6 text-mist">
        Search, fix a typo, or drop a card you don't need anymore.
      </p>

      <input
        ref={searchInputRef}
        data-testid="cards-search-input"
        type="search"
        value={qInput}
        onChange={(event) => setQInput(event.target.value)}
        placeholder="Search your cards"
        aria-label="Search your cards"
        className="mt-5 w-full rounded-md border border-divider bg-elevated px-3 py-2 text-sm text-porcelain placeholder:text-slate focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal"
      />

      {/* Reuses the `review-chip` idiom (`components/review/review-chip.tsx`)
          — the same teal pill treatment — but as a filter toggle rather than
          a link to a scoped session: tapping one sets/clears `?path=` here on
          `/cards` itself. Hidden entirely with no paths yet (a fresh account
          has nothing to filter by). */}
      {paths.length > 0 ? (
        <div data-testid="cards-filter-chips" className="mt-3 flex flex-wrap gap-2">
          <FilterChip search={{}} label="All paths" active={pathId === null} />
          {paths.map((path) => (
            <FilterChip
              key={path.id}
              search={{ path: path.id }}
              label={path.title}
              active={pathId === path.id}
            />
          ))}
        </div>
      ) : null}

      <div className="mt-5">
        {cardsQuery.data === undefined ? (
          cardsQuery.isError ? (
            <UnavailableState />
          ) : (
            <LoadingState />
          )
        ) : cards.length === 0 ? (
          <EmptyState filtered={filtered} />
        ) : (
          <ul data-testid="cards-list" className="flex flex-col gap-3">
            {cards.map((card) => (
              <CardRow key={card.id} card={card} deletion={deletion} />
            ))}
          </ul>
        )}
      </div>

      {cardsQuery.hasNextPage ? (
        <button
          type="button"
          data-testid="cards-load-more"
          onClick={() => void cardsQuery.fetchNextPage()}
          disabled={cardsQuery.isFetchingNextPage}
          className="mt-4 w-full rounded-md border border-divider py-2 text-sm text-mist transition-colors hover:text-porcelain disabled:cursor-not-allowed disabled:opacity-50"
        >
          {cardsQuery.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </Workspace>
  );
}

function FilterChip({
  search,
  label,
  active,
}: {
  search: { path?: string };
  label: string;
  active: boolean;
}) {
  return (
    <Link
      to="/cards"
      search={search}
      data-testid="cards-filter-chip"
      data-active={active}
      className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold transition-colors ${
        active ? "bg-teal text-night" : "bg-elevated text-mist hover:text-porcelain"
      }`}
    >
      {label}
    </Link>
  );
}

function LoadingState() {
  return (
    <p data-testid="cards-loading" className="text-sm text-mist">
      Loading your cards…
    </p>
  );
}

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <StateCard testid="cards-empty">
      <h2 className="text-lg font-semibold">
        {filtered ? "No cards match." : "You haven't kept any cards yet."}
      </h2>
      <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
        {filtered
          ? "Try a different path, or clear the search."
          : "Finish a lesson and keep a few drafts — they'll show up here."}
      </p>
    </StateCard>
  );
}

function UnavailableState() {
  return (
    <StateCard testid="cards-unavailable">
      <h2 className="text-lg font-semibold">We couldn't load your cards.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        Something went wrong reaching Aleph. Reload the page and try again.
      </p>
      {/* The same way out `routes/review.tsx`'s own `UnavailableState` gives —
          a genuine fetch failure deserves an escape hatch, not just a retry
          instruction with nowhere to act on it (AL-410 review finding 6). */}
      <Link
        to="/"
        data-testid="cards-unavailable-back"
        className="mt-5 inline-block text-sm text-teal"
      >
        Back to your paths
      </Link>
    </StateCard>
  );
}
