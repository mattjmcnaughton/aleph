import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { LessonMarker } from "../components/lesson-marker";
import { CopyCard, PublicPage, SharedBy, firstName } from "../components/public-profile";
import { SectionHeader } from "../components/section-header";
import { type PublicPath, publicPathQueryOptions } from "../lib/api";
import { levelLabel } from "../lib/onboarding";

// A shared path (PRD §3 frame 4): how the owner started it, their outline and
// their progress, and the Copy card. Never a lesson's content.
export const Route = createFileRoute("/@{$handle}/paths/$pathId")({
  component: SharedPathRoute,
});

function SharedPathRoute() {
  const { handle, pathId } = Route.useParams();
  const query = useQuery(publicPathQueryOptions(handle, pathId));
  return <PublicPage query={query}>{(path) => <SharedPath path={path} />}</PublicPage>;
}

function SharedPath({ path }: { path: PublicPath }) {
  const owner = firstName(path.owner.display_name);
  const inputs = [
    { label: "Topic", value: path.topic },
    { label: "Level", value: levelLabel(path.level) },
    { label: "Guidance", value: path.guidance },
    { label: "Teaching style", value: path.teaching_style },
  ];
  return (
    <main className="page" data-testid="public-path">
      <p className="text-sm text-mist">
        <Link
          to="/@{$handle}"
          params={{ handle: path.owner.handle }}
          className="text-porcelain hover:text-teal-bright"
        >
          {path.owner.display_name}
        </Link>
      </p>
      <h1 className="page-title mt-2">{path.title}</h1>
      <div className="mt-3">
        <SharedBy owner={path.owner} />
      </div>
      <p className="mt-2 text-sm text-mist">
        {levelLabel(path.level)} · {path.total} lessons in {path.units.length}{" "}
        {path.units.length === 1 ? "unit" : "units"} · {path.done} complete
      </p>

      <CopyCard
        kind="path"
        owner={path.owner}
        itemId={path.id}
        title="Learn this yourself"
        body={`Start your own path from what ${owner} asked for. You can change any of it first, and Aleph drafts a new outline for you.`}
        action="Start this path"
      />

      <SectionHeader kicker={`How ${owner} started it`} spacing="mt-9" />
      <dl className="mt-1" data-testid="public-path-inputs">
        {inputs
          .filter((input) => input.value)
          .map((input) => (
            <div key={input.label} className="border-b border-divider py-3">
              <dt className="kicker">{input.label}</dt>
              <dd className="mt-0.5 whitespace-pre-line text-[15px] leading-6">{input.value}</dd>
            </div>
          ))}
      </dl>

      <SectionHeader kicker={`${owner}'s outline`} spacing="mt-9" />
      {path.units.map((unit, index) => (
        <section key={`${index}-${unit.title}`} className="mt-5">
          <p className="kicker">Unit {index + 1}</p>
          <p className="font-serif text-lg font-medium">{unit.title}</p>
          <ul className="mt-2">
            {unit.lessons.map((lesson, lessonIndex) => (
              <li
                key={`${lessonIndex}-${lesson.title}`}
                className="flex min-h-[44px] items-center gap-3 border-b border-divider py-2"
              >
                {/* The owner's position is not the visitor's, so it is not
                    teal: a check for done, a hollow ring for the rest. */}
                {lesson.complete ? (
                  <LessonMarker state="complete" size="sm" />
                ) : (
                  <span aria-hidden="true" className="grid h-5 w-5 shrink-0 place-items-center">
                    <span className="h-2 w-2 rounded-full ring-1 ring-faint" />
                  </span>
                )}
                <span className={lesson.complete ? "text-mist" : "text-porcelain"}>
                  {lesson.title}
                  <span className="sr-only">{lesson.complete ? " (complete)" : ""}</span>
                </span>
                {lesson.current ? (
                  <span className="ml-auto shrink-0 text-xs text-mist">{owner} is here</span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
      <p className="mt-5 text-sm leading-6 text-mist">
        Visitors see the outline, not the lessons. Your copy gets its own outline, drafted from the
        inputs above, so it may not match this one.
      </p>
    </main>
  );
}
