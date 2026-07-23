import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { AlephGlyph } from "../components/aleph-logo";
import { sessionQueryOptions } from "../lib/auth";

export const Route = createFileRoute("/")({
  component: Home,
});

// Signed-in landing shell. The "Your paths" switcher and the onboarding flow it
// links to are built in AL-061+; this proves the authenticated shell renders in
// the Nocturne system and holds the seam those surfaces plug into.
function Home() {
  const session = useQuery(sessionQueryOptions);
  const firstName = session.data?.authenticated
    ? session.data.user.display_name.split(" ")[0]
    : "learner";

  return (
    <main className="mx-auto w-full max-w-[480px] px-4 py-8">
      <p className="kicker">Your paths</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        Welcome back, {firstName}.
      </h1>
      <p className="mt-3 text-base leading-6 text-mist">
        Name a topic and Aleph drafts a learning path you can work through, lesson by lesson.
      </p>

      <section className="mt-8 rounded-lg border border-divider bg-surface p-6 text-center shadow-sm">
        <div className="flex justify-center">
          <AlephGlyph size="md" />
        </div>
        <h2 className="mt-4 text-lg font-semibold">No paths yet</h2>
        <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
          Start your first path — pick something you want to learn and choose how much you already
          know.
        </p>
        <button
          type="button"
          className="mt-5 inline-flex w-full items-center justify-center rounded-md bg-teal px-4 py-3 text-sm font-semibold text-night transition-colors hover:bg-teal-bright"
        >
          New path
        </button>
      </section>
    </main>
  );
}
