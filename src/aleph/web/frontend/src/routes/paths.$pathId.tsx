import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { pathQueryOptions } from "../lib/api";

export const Route = createFileRoute("/paths/$pathId")({
  component: PathView,
});

// PLACEHOLDER (AL-062 builds the real path view). Onboarding navigates here on a
// `ready` outline, so this file exists to make that navigation land. It renders
// the topic and a `data-testid="path-view"` seam the onboarding + e2e tests key
// on; AL-062 replaces the body with the units/lessons rail, progress, and the
// complete/current/locked states.
function PathView() {
  const { pathId } = Route.useParams();
  const pathQuery = useQuery(pathQueryOptions(pathId));

  return (
    <main data-testid="path-view" className="mx-auto w-full max-w-[480px] px-4 py-8">
      <p className="kicker">Path</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        {pathQuery.data?.topic ?? "Loading your path…"}
      </h1>
      <p className="mt-3 text-base leading-6 text-mist">
        Your path is ready. The lesson rail lands here next (AL-062).
      </p>
    </main>
  );
}
