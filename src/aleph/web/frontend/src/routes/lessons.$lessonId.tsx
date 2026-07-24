import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/lessons/$lessonId")({
  component: LessonView,
});

// PLACEHOLDER (AL-063 builds the real lesson view). The path view (AL-062)
// navigates here when a learner taps an available or complete lesson, so this
// file exists to make that navigation land. It renders the `lessonId` and a
// `data-testid="lesson-view"` seam the path-view + e2e tests key on; AL-063
// replaces the body with the Read passage → Quick check → outcome/explanation →
// mark-complete surface (plus its generating + failed states, TDD §8).
//
// Contract for AL-063: the route is `/lessons/$lessonId`; `lessonId` is the
// `PathLesson.id` from the path detail. AL-062 navigates for available lessons
// (start) and complete lessons (revisit); locked lessons never navigate here.
function LessonView() {
  const { lessonId } = Route.useParams();

  return (
    <main data-testid="lesson-view" className="mx-auto w-full max-w-[480px] px-4 py-8">
      <p className="kicker">Lesson</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        Your lesson lands here next.
      </h1>
      <p className="mt-3 text-base leading-6 text-mist">
        The Read passage, Quick check, and mark-complete flow arrive with AL-063.
      </p>
      <p className="mt-6 font-mono text-xs text-slate" data-testid="lesson-view-id">
        {lessonId}
      </p>
    </main>
  );
}
