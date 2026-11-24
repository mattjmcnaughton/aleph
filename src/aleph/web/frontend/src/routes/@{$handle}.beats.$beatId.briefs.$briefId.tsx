import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { BriefSources } from "../components/brief-sources";
import { Markdown } from "../components/markdown";
import { PublicPage, SharedBy } from "../components/public-profile";
import { type PublicBrief, publicBriefQueryOptions } from "../lib/api";
import { formatBriefDate } from "../lib/beats";

// A published Brief on a shared Beat. Reading it never marks it read, even for
// its owner: there is no read ping here at all (TDD §6).
export const Route = createFileRoute("/@{$handle}/beats/$beatId/briefs/$briefId")({
  component: SharedBriefRoute,
});

function SharedBriefRoute() {
  const { handle, beatId, briefId } = Route.useParams();
  const query = useQuery(publicBriefQueryOptions(handle, beatId, briefId));
  return <PublicPage query={query}>{(brief) => <SharedBrief brief={brief} />}</PublicPage>;
}

function SharedBrief({ brief }: { brief: PublicBrief }) {
  return (
    <main className="page" data-testid="public-brief">
      <p className="text-sm text-mist">
        <Link
          to="/@{$handle}/beats/$beatId"
          params={{ handle: brief.owner.handle, beatId: brief.beat_id }}
          className="text-porcelain hover:text-teal-bright"
        >
          {brief.beat_topic}
        </Link>
      </p>
      <p className="kicker mt-4">
        Brief #{brief.number} · {formatBriefDate(brief.published_on)}
      </p>
      <h1 className="page-title mt-2">{brief.title}</h1>
      <div className="mt-3">
        <SharedBy owner={brief.owner} />
      </div>
      <Markdown className="mt-8 text-base leading-7" testid="public-brief-body" plainFences>
        {brief.body_markdown}
      </Markdown>
      <BriefSources sources={brief.sources} />
    </main>
  );
}
