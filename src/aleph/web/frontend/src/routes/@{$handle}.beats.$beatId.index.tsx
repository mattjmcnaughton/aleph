import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { ListRow, RowChevron, RowTitle } from "../components/list-row";
import { CopyCard, PublicPage, SharedBy, firstName } from "../components/public-profile";
import { SectionHeader } from "../components/section-header";
import { SECONDARY_CTA } from "../components/state-card";
import {
  type PublicBeat,
  type PublicBriefEntry,
  getPublicBeatPage,
  publicBeatQueryOptions,
} from "../lib/api";
import { cadenceLabel, formatBriefDate } from "../lib/beats";
import { levelLabel } from "../lib/onboarding";

// A shared Beat (PRD §3 frame 5): its standing orders, its Briefs (each
// readable), and the Copy card.
export const Route = createFileRoute("/@{$handle}/beats/$beatId/")({
  component: SharedBeatRoute,
});

function SharedBeatRoute() {
  const { handle, beatId } = Route.useParams();
  const query = useQuery(publicBeatQueryOptions(handle, beatId));
  return <PublicPage query={query}>{(beat) => <SharedBeat beat={beat} />}</PublicPage>;
}

function SharedBeat({ beat }: { beat: PublicBeat }) {
  const owner = firstName(beat.owner.display_name);
  const [more, setMore] = useState<PublicBriefEntry[]>([]);
  const [cursor, setCursor] = useState(beat.next_cursor);
  const loadMore = useMutation({
    mutationFn: (next: number) => getPublicBeatPage(beat.owner.handle, beat.id, next),
    onSuccess: (page) => {
      setMore((previous) => [...previous, ...page.briefs]);
      setCursor(page.next_cursor);
    },
  });
  const briefs = [...beat.briefs, ...more];

  return (
    <main className="page" data-testid="public-beat">
      <p className="text-sm text-mist">
        <Link
          to="/@{$handle}"
          params={{ handle: beat.owner.handle }}
          className="text-porcelain hover:text-teal-bright"
        >
          {beat.owner.display_name}
        </Link>
      </p>
      <h1 className="page-title mt-2">{beat.topic}</h1>
      <div className="mt-3">
        <SharedBy owner={beat.owner} />
      </div>
      <p className="mt-2 text-sm text-mist">
        {levelLabel(beat.level)} · {cadenceLabel(beat.cadence)}
      </p>
      {beat.guidance ? (
        <p className="mt-3 whitespace-pre-line text-[15px] leading-6 text-mist">{beat.guidance}</p>
      ) : null}

      <CopyCard
        kind="beat"
        owner={beat.owner}
        itemId={beat.id}
        title="Keep watch on this too"
        body={`Deploy your own analyst with ${owner}'s topic, level and guidance. It reports on the day you pick, starting from Brief #1.`}
        action="Deploy this Beat"
      />

      <SectionHeader kicker="Briefs" spacing="mt-9" />
      {briefs.length === 0 ? (
        <p className="mt-4 text-sm text-mist">No Briefs yet.</p>
      ) : (
        <ul>
          {briefs.map((brief) => (
            <li key={brief.id}>
              <ListRow
                testid="public-brief-row"
                main={
                  <Link
                    to="/@{$handle}/beats/$beatId/briefs/$briefId"
                    params={{ handle: beat.owner.handle, beatId: beat.id, briefId: brief.id }}
                    className="block"
                  >
                    <RowTitle title={brief.title} titleTestid="public-brief-title">
                      <p className="text-sm text-mist">
                        Brief #{brief.number} · {formatBriefDate(brief.published_on)} ·{" "}
                        {brief.source_count} {brief.source_count === 1 ? "source" : "sources"}
                      </p>
                    </RowTitle>
                  </Link>
                }
                actions={
                  <span aria-hidden="true" className="shrink-0 pt-2 text-slate lg:pt-0">
                    <RowChevron />
                  </span>
                }
              />
            </li>
          ))}
        </ul>
      )}
      {cursor !== null ? (
        <button
          type="button"
          className={`mt-6 ${SECONDARY_CTA}`}
          disabled={loadMore.isPending}
          onClick={() => loadMore.mutate(cursor)}
        >
          {loadMore.isPending ? "Loading…" : "Older Briefs"}
        </button>
      ) : null}
    </main>
  );
}
