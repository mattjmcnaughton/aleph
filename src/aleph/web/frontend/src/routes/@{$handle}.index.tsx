import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { ActivityStrip } from "../components/activity-strip";
import { ListRow, RowChevron, RowTitle } from "../components/list-row";
import { JoinFooter, OwnerAvatar, PublicPage, firstName } from "../components/public-profile";
import { SectionHeader } from "../components/section-header";
import { StreakChip } from "../components/streak-chip";
import { StreakLine } from "../components/streak-line";
import { type PublicProfile, publicProfileQueryOptions } from "../lib/api";
import { cadenceLabel } from "../lib/beats";
import { levelLabel } from "../lib/onboarding";

// A learner's Public profile (PRD §3 frame 3): readable without signing in.
export const Route = createFileRoute("/@{$handle}/")({
  component: ProfileRoute,
});

function ProfileRoute() {
  const { handle } = Route.useParams();
  const query = useQuery(publicProfileQueryOptions(handle));
  return <PublicPage query={query}>{(profile) => <Profile profile={profile} />}</PublicPage>;
}

function memberSince(date: string): string {
  return new Date(`${date}T00:00:00`).toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
  });
}

function Profile({ profile }: { profile: PublicProfile }) {
  const learning = profile.paths.filter((path) => path.total === 0 || path.done < path.total);
  const finished = profile.paths.filter((path) => path.total > 0 && path.done === path.total);
  return (
    <main className="page" data-testid="public-profile">
      <div className="flex items-center gap-4">
        <OwnerAvatar name={profile.display_name} />
        <div className="min-w-0">
          <h1 className="page-title max-lg:text-2xl">{profile.display_name}</h1>
          <p className="text-sm text-mist">
            @{profile.handle} · learning on Aleph since {memberSince(profile.member_since)}
          </p>
        </div>
      </div>

      {profile.activity ? (
        <div className="mt-7 flex items-center justify-between gap-6">
          <StreakLine
            summary={{
              today: "",
              current_streak: profile.activity.current_streak,
              best_streak: profile.activity.best_streak,
              completed_today: 0,
              activity: profile.activity.activity,
              paths: [],
            }}
          />
          <ActivityStrip activity={profile.activity.activity} />
        </div>
      ) : null}

      {learning.length > 0 ? (
        <>
          <SectionHeader
            kicker="Learning"
            spacing="mt-8"
            summary={`${learning.length} ${learning.length === 1 ? "path" : "paths"}`}
          />
          <ul>
            {learning.map((path) => (
              <li key={path.id}>
                <PathRow handle={profile.handle} path={path} />
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {finished.length > 0 ? (
        <>
          <SectionHeader kicker="Finished" spacing="mt-8" />
          <ul className="opacity-70">
            {finished.map((path) => (
              <li key={path.id}>
                <PathRow handle={profile.handle} path={path} />
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {profile.beats.length > 0 ? (
        <>
          <SectionHeader
            kicker="Beats"
            spacing="mt-8"
            summary={`Topics ${firstName(profile.display_name)} keeps watch on`}
          />
          <ul>
            {profile.beats.map((beat) => (
              <li key={beat.id}>
                <ListRow
                  testid="public-beat-row"
                  main={
                    <Link
                      to="/@{$handle}/beats/$beatId"
                      params={{ handle: profile.handle, beatId: beat.id }}
                      className="block"
                    >
                      <RowTitle title={beat.topic} titleTestid="public-beat-topic">
                        {beat.latest_brief_title ? (
                          <p className="line-clamp-1 text-sm text-mist">
                            Latest: {beat.latest_brief_title}
                          </p>
                        ) : null}
                      </RowTitle>
                    </Link>
                  }
                  meta={
                    <p className="mt-1 text-sm text-mist lg:mt-0">{cadenceLabel(beat.cadence)}</p>
                  }
                  status={
                    <span className="hidden lg:inline">
                      {beat.brief_count} {beat.brief_count === 1 ? "Brief" : "Briefs"}
                    </span>
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
        </>
      ) : null}

      {profile.paths.length === 0 && profile.beats.length === 0 ? (
        <p className="mt-8 text-sm text-mist">Nothing shared yet.</p>
      ) : null}

      <JoinFooter owner={profile.display_name} />
    </main>
  );
}

function PathRow({ handle, path }: { handle: string; path: PublicProfile["paths"][number] }) {
  const percent = path.total ? Math.round((path.done / path.total) * 100) : 0;
  const finished = path.total > 0 && path.done === path.total;
  return (
    <ListRow
      testid="public-path-row"
      main={
        <Link to="/@{$handle}/paths/$pathId" params={{ handle, pathId: path.id }} className="block">
          <RowTitle title={path.title} titleTestid="public-path-title">
            <p className="text-sm text-mist">{levelLabel(path.level)}</p>
            <StreakChip days={path.current_streak} />
          </RowTitle>
        </Link>
      }
      meta={
        <div className="mt-3 flex items-center gap-3 lg:mt-0 lg:block">
          <div
            aria-hidden="true"
            className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-porcelain/10"
          >
            <span className="block h-full bg-porcelain/45" style={{ width: `${percent}%` }} />
          </div>
          <p className="shrink-0 text-sm text-mist lg:mt-2">
            {path.done} of {path.total} lessons
          </p>
        </div>
      }
      status={<span className="hidden lg:inline">{finished ? "Complete" : "In progress"}</span>}
      actions={
        <span aria-hidden="true" className="shrink-0 pt-2 text-slate lg:pt-0">
          <RowChevron />
        </span>
      }
    />
  );
}
