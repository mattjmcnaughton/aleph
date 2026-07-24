import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  type LessonAttempt,
  type LessonDetail,
  PATHS_QUERY_PREFIX,
  type QuickCheck,
  attemptLesson,
  completeLesson,
  generateLesson,
  isLessonViewTerminal,
  isNotFound,
  lessonQueryKey,
  lessonQueryOptions,
} from "../lib/api";
import {
  CheckIcon,
  LockIcon,
  PRIMARY_CTA,
  PRIMARY_CTA_BASE,
  RetryNotices,
  SECONDARY_CTA,
  Spinner,
  StateCard,
} from "../components/state-card";
import { makePollingRefetchInterval } from "../lib/polling";
import { useRetryGeneration } from "../lib/use-retry-generation";

export const Route = createFileRoute("/lessons/$lessonId")({
  component: LessonView,
});

const lessonViewPollConfig = {
  isTerminal: isLessonViewTerminal,
  // A deep link to a missing lesson (404) is terminal — stop polling so we don't
  // spawn an endless chain of backend resumes against a lesson that can't resolve.
  isErrorTerminal: isNotFound,
};

// How long the view waits on a reachable-but-unresolving lesson before it stops
// spinning and degrades to a recovery notice (C1, PRD §5.6). The documented
// dead-end (docs/api.md): completing past a `failed` head strands its successor
// `available` + `ungenerated` forever — `GET`/`generate` no-op because the head,
// not this lesson, is the chain head. Rather than spin indefinitely, we point the
// learner back to the path where the failed head's retry lives. ~45s ≈ the shared
// 2s→5s backoff over roughly a dozen polls.
const GENERATION_STALL_MS = 45_000;

// The lesson view (§8, TDD): the Read passage → Quick check → Outcome/explanation
// → Mark-complete surface for one lesson. It renders straight from a single
// `GET /lessons/{id}` payload. Answer-hiding (W6): the payload carries the keyed
// answer + explanation ONLY inside `attempt`, which is null until the learner
// records an Attempt, so a pre-Attempt render has no correct answer to leak. The
// Attempt is first-wins and its Outcome is formative and non-gating — the learner
// proceeds regardless. Content keeps generating on demand, so the view polls
// through the shared helper until generation is terminal (`isLessonViewTerminal`).
//
// A learner can also deep-link onto a non-ready lesson (a reload mid-generation,
// a bookmarked link). Those states render minimally so the learner never
// dead-ends: a generating spinner, a failed+retry surface (W8), a locked notice.
function LessonView() {
  const { lessonId } = Route.useParams();
  const queryClient = useQueryClient();

  // Degrade an eternally-unresolving generation to a recovery notice (C1). Once
  // stalled we stop polling too, so we don't keep spawning backend resumes for a
  // lesson only the path's failed head can unblock.
  const [stalled, setStalled] = useState(false);

  const lessonQuery = useQuery({
    ...lessonQueryOptions(lessonId),
    refetchInterval: stalled ? false : makePollingRefetchInterval(lessonViewPollConfig),
  });

  const generate = useRetryGeneration({ mutationFn: generateLesson, queryKey: lessonQueryKey });

  const attemptMutation = useMutation({
    mutationFn: ({ id, index }: { id: string; index: number }) => attemptLesson(id, index),
    // Fold the reveal into the cached detail so everything derives from one
    // source of truth (matches revealed-on-return, where GET already carries it).
    // Cancel any in-flight GET first (C8): a poll that started before the Attempt
    // and resolves after it would carry `attempt: null` and briefly un-reveal.
    onSuccess: async (attempt, { id }) => {
      await queryClient.cancelQueries({ queryKey: lessonQueryKey(id) });
      queryClient.setQueryData<LessonDetail>(lessonQueryKey(id), (old) =>
        old ? { ...old, attempt } : old,
      );
    },
  });

  const completeMutation = useMutation({
    mutationFn: (id: string) => completeLesson(id),
    // Same in-flight-GET guard as the Attempt (C8): a late poll must not revert
    // the unlock_state we just wrote.
    onSuccess: async (result, id) => {
      await queryClient.cancelQueries({ queryKey: lessonQueryKey(id) });
      queryClient.setQueryData<LessonDetail>(lessonQueryKey(id), (old) =>
        old ? { ...old, unlock_state: result.unlock_state } : old,
      );
      // Completion also moves state the *path* surfaces own: the rail's unlock
      // states (this lesson complete, the next one unlocked) and both progress
      // readouts. Nothing else would correct them — the path poll stops once
      // everything visible is terminal, and its cached payload stays fresh for
      // `staleTime` (30s), so a learner tapping "Back to your path" would find
      // the lesson un-ticked and the next one still locked (W1's closing beat,
      // caught by the AL-090 journeys). Invalidating the shared `["paths", …]`
      // prefix covers the detail and the switcher list in one; it is not
      // awaited because marking those queries stale is synchronous and no
      // refetch needs to land before the completed state renders.
      void queryClient.invalidateQueries({ queryKey: PATHS_QUERY_PREFIX });
    },
  });

  const detail = lessonQuery.data;

  // The reachable-but-unresolving window: an unlocked lesson still short of a
  // terminal generation state. Arm a one-shot timer while we sit here; if it
  // fires before generation resolves, degrade (C1). A poll flipping to
  // generated/failed clears `awaitingGeneration`, which tears the timer down.
  const awaitingGeneration =
    detail !== undefined &&
    detail.unlock_state !== "locked" &&
    detail.generation_state !== "generated" &&
    detail.generation_state !== "failed";

  useEffect(() => {
    if (!awaitingGeneration) return;
    const timer = setTimeout(() => setStalled(true), GENERATION_STALL_MS);
    return () => clearTimeout(timer);
  }, [awaitingGeneration]);

  return (
    <main data-testid="lesson-view" className="mx-auto w-full max-w-[480px] px-4 py-8">
      {detail === undefined ? (
        lessonQuery.isError ? (
          <UnavailableState />
        ) : (
          <LoadingState />
        )
      ) : detail.unlock_state === "locked" ? (
        <LockedState pathId={detail.path_id} />
      ) : detail.generation_state === "failed" ? (
        <FailedState
          message={detail.generation_error ?? undefined}
          onRetry={() => generate.retry(detail.id)}
          retrying={generate.retrying}
          retryRateLimited={generate.rateLimited}
          retryErrored={generate.errored}
        />
      ) : detail.generation_state !== "generated" ? (
        stalled ? (
          <StalledState pathId={detail.path_id} />
        ) : (
          <GeneratingState />
        )
      ) : (
        <ReadyLesson
          detail={detail}
          onAttempt={(index) => attemptMutation.mutate({ id: detail.id, index })}
          attempting={attemptMutation.isPending}
          attemptErrored={attemptMutation.isError}
          onComplete={() => completeMutation.mutate(detail.id)}
          completing={completeMutation.isPending}
          completeErrored={completeMutation.isError}
        />
      )}

      {/* Stable id seam the path-view + e2e suites key on (keep this testid). */}
      <p data-testid="lesson-view-id" className="mt-10 font-mono text-xs text-slate">
        {lessonId}
      </p>
    </main>
  );
}

// --- Ready lesson: Read passage → Quick check → Outcome → Mark complete -------

function ReadyLesson({
  detail,
  onAttempt,
  attempting,
  attemptErrored,
  onComplete,
  completing,
  completeErrored,
}: {
  detail: LessonDetail;
  onAttempt: (index: number) => void;
  attempting: boolean;
  attemptErrored: boolean;
  onComplete: () => void;
  completing: boolean;
  completeErrored: boolean;
}) {
  const quickCheck = detail.quick_check;
  const reveal = detail.attempt;
  const isComplete = detail.unlock_state === "complete";
  // Mark complete is offered once the learner has engaged the Quick check — an
  // Attempt is recorded (non-gating on the Outcome). The `quickCheck === null`
  // disjunct is only a defensive fallback: the contract always pairs a generated
  // lesson with a Quick check, so a null one never reaches here in practice, but
  // if it did we render passage + complete rather than dead-ending.
  const completeEligible = quickCheck === null || reveal !== null;

  return (
    <>
      <p className="kicker">Lesson</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">{detail.title}</h1>

      <div
        data-testid="lesson-read-passage"
        className="mt-5 whitespace-pre-line text-base leading-7 text-porcelain"
      >
        {detail.read_passage}
      </div>

      {quickCheck !== null ? (
        <QuickCheckBlock
          quickCheck={quickCheck}
          reveal={reveal}
          onAttempt={onAttempt}
          attempting={attempting}
          attemptErrored={attemptErrored}
        />
      ) : null}

      <div className="mt-8">
        {isComplete ? (
          <CompletedState pathId={detail.path_id} />
        ) : completeEligible ? (
          <>
            {completeErrored ? (
              <p
                data-testid="lesson-complete-error"
                aria-live="assertive"
                className="mb-4 rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-danger"
              >
                That didn't go through. Check your connection and mark complete again.
              </p>
            ) : null}
            <button
              type="button"
              data-testid="lesson-complete-button"
              onClick={onComplete}
              disabled={completing}
              className={PRIMARY_CTA}
            >
              {completing ? "Marking complete…" : "Mark complete"}
            </button>
          </>
        ) : (
          <p className="text-center text-sm leading-6 text-slate">
            Answer the Quick check to finish this lesson.
          </p>
        )}
      </div>
    </>
  );
}

function QuickCheckBlock({
  quickCheck,
  reveal,
  onAttempt,
  attempting,
  attemptErrored,
}: {
  quickCheck: QuickCheck;
  reveal: LessonAttempt | null;
  onAttempt: (index: number) => void;
  attempting: boolean;
  attemptErrored: boolean;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const revealed = reveal !== null;

  return (
    <section data-testid="quick-check" className="mt-8">
      <p className="kicker">Quick check</p>
      <p data-testid="quick-check-stem" className="mt-2 text-lg font-semibold leading-snug">
        {quickCheck.stem}
      </p>

      {/* Single-select MCQ as a native radio group (styled like the onboarding
          level picker): exactly one option is checked, giving real radiogroup
          semantics for free (the sr-only legend conveys single-select). After
          the Attempt, options carry `aria-disabled` — NOT native `disabled`, so
          a screen-reader user can still tab through and inspect which was
          correct. The reveal locks the answer (first-wins); the `revealed` guard
          in onChange enforces it while keeping the inputs focusable. */}
      <fieldset className="mt-4">
        <legend className="sr-only">Choose one answer</legend>
        <div className="space-y-2">
          {quickCheck.options.map((option, index) => {
            const id = `quick-check-option-${index}`;
            const isSelected = revealed ? reveal.selected_index === index : selected === index;
            const isCorrect = revealed && reveal.correct_index === index;
            return (
              <label
                key={`${index}-${option}`}
                htmlFor={id}
                className={optionClass(isSelected, revealed, isCorrect)}
              >
                <input
                  id={id}
                  type="radio"
                  name="quick-check"
                  data-testid="quick-check-option"
                  data-correct={revealed ? isCorrect : undefined}
                  checked={isSelected}
                  aria-disabled={revealed || undefined}
                  onChange={() => {
                    if (revealed) return;
                    setSelected(index);
                  }}
                  className="sr-only"
                />
                {/* Reveal is conveyed by colour; these sr-only prefixes carry the
                    same meaning to assistive tech (C3). */}
                {revealed && isCorrect ? <span className="sr-only">Correct answer: </span> : null}
                {revealed && isSelected && !isCorrect ? (
                  <span className="sr-only">Your answer: </span>
                ) : null}
                <span className="min-w-0 flex-1">{option}</span>
                {isCorrect ? (
                  <span aria-hidden="true" className="shrink-0 text-teal">
                    <CheckIcon />
                  </span>
                ) : null}
              </label>
            );
          })}
        </div>
      </fieldset>

      {revealed ? (
        <OutcomeReveal reveal={reveal} />
      ) : (
        <>
          {attemptErrored ? (
            <p
              data-testid="quick-check-error"
              aria-live="assertive"
              className="mt-4 rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-danger"
            >
              That didn't go through. Check your connection and check your answer again.
            </p>
          ) : null}
          <button
            type="button"
            data-testid="quick-check-submit"
            onClick={() => selected !== null && onAttempt(selected)}
            disabled={selected === null || attempting}
            className={`mt-4 ${PRIMARY_CTA}`}
          >
            {attempting ? "Checking…" : "Check answer"}
          </button>
        </>
      )}
    </section>
  );
}

const OPTION_BASE =
  "flex w-full cursor-pointer items-center gap-3 rounded-md border px-4 py-3 text-left text-sm transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-teal has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-night";

function optionClass(selected: boolean, revealed: boolean, correct: boolean): string {
  // Teal highlight: the keyed-correct option after reveal, or the learner's live
  // pick before reveal (`correct` is only ever set once revealed, so the two
  // never collide). These two states share one treatment.
  if (correct || (selected && !revealed)) {
    return `${OPTION_BASE} border-teal bg-teal/10 text-porcelain`;
  }
  if (revealed && selected) {
    // The learner's pick, now shown to be wrong (the correct one is highlighted).
    return `${OPTION_BASE} border-danger-border/60 bg-danger-bg text-porcelain`;
  }
  if (revealed) {
    return `${OPTION_BASE} border-divider bg-surface/50 text-slate`;
  }
  return `${OPTION_BASE} border-divider bg-surface text-mist hover:border-teal/40 hover:text-porcelain`;
}

function OutcomeReveal({ reveal }: { reveal: LessonAttempt }) {
  const correct = reveal.outcome === "correct";
  return (
    <div
      data-testid="outcome-reveal"
      data-outcome={reveal.outcome}
      aria-live="polite"
      className={`mt-5 rounded-lg border p-5 ${
        correct ? "border-teal/40 bg-teal/10" : "border-iris-700 bg-iris-900"
      }`}
    >
      <p className={`text-sm font-semibold ${correct ? "text-teal" : "text-iris-300"}`}>
        {correct ? "Correct." : "Not quite."}
      </p>
      <p data-testid="outcome-explanation" className="mt-2 text-sm leading-6 text-porcelain">
        {reveal.explanation}
      </p>
    </div>
  );
}

function CompletedState({ pathId }: { pathId: string }) {
  return (
    <section
      data-testid="lesson-completed"
      className="rounded-lg border border-teal/40 bg-teal/10 p-5 text-center shadow-sm"
    >
      <span
        aria-hidden="true"
        className="mx-auto grid h-8 w-8 place-items-center rounded-full border border-teal text-teal"
      >
        <CheckIcon />
      </span>
      <h2 className="mt-3 text-lg font-semibold text-porcelain">Lesson complete.</h2>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        Nice work. Head back to your path to keep going.
      </p>
      <Link
        to="/paths/$pathId"
        params={{ pathId }}
        data-testid="lesson-completed-back"
        className={`mt-5 ${PRIMARY_CTA_BASE}`}
      >
        Back to your path
      </Link>
    </section>
  );
}

// --- Non-ready + loading states ---------------------------------------------

function LoadingState() {
  return (
    <>
      <p className="kicker">Lesson</p>
      <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight">
        Loading your lesson…
      </h1>
    </>
  );
}

function GeneratingState() {
  return (
    <StateCard testid="lesson-generating" ariaLive="polite">
      <Spinner />
      <h1 className="mt-4 text-lg font-semibold">Preparing your lesson…</h1>
      <p className="mx-auto mt-2 max-w-[22rem] text-sm leading-6 text-mist">
        Aleph is writing the Read passage and Quick check. This page updates itself when it's ready.
      </p>
    </StateCard>
  );
}

function StalledState({ pathId }: { pathId: string }) {
  return (
    <StateCard testid="lesson-generation-stalled" ariaLive="polite">
      <h1 className="text-lg font-semibold">This lesson isn't ready yet.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        It's taking longer than expected. Head back to your path — if an earlier lesson failed to
        generate, retrying it there unblocks this one.
      </p>
      <Link
        to="/paths/$pathId"
        params={{ pathId }}
        data-testid="lesson-stalled-back"
        className={`mt-5 ${SECONDARY_CTA}`}
      >
        Back to your path
      </Link>
    </StateCard>
  );
}

function FailedState({
  message,
  onRetry,
  retrying,
  retryRateLimited,
  retryErrored,
}: {
  message?: string;
  onRetry: () => void;
  retrying: boolean;
  retryRateLimited: boolean;
  retryErrored: boolean;
}) {
  return (
    <StateCard testid="lesson-failed" variant="error" dataVariant="error" ariaLive="assertive">
      <h1 className="text-lg font-semibold text-danger">This lesson didn't generate.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-porcelain">
        {message ?? "We couldn't write this lesson. Retry when you're ready."}
      </p>
      <RetryNotices
        testidPrefix="lesson"
        rateLimited={retryRateLimited}
        errored={retryErrored}
        rateLimitMessage="You've reached today's limit for lesson generation. Try again tomorrow."
      />
      <button
        type="button"
        data-testid="lesson-retry-button"
        onClick={onRetry}
        disabled={retrying}
        className={`mt-6 ${PRIMARY_CTA}`}
      >
        {retrying ? "Retrying…" : "Try again"}
      </button>
    </StateCard>
  );
}

function LockedState({ pathId }: { pathId: string }) {
  return (
    <StateCard testid="lesson-locked">
      <span
        aria-hidden="true"
        className="mx-auto grid h-8 w-8 place-items-center rounded-full border border-divider text-slate"
      >
        <LockIcon />
      </span>
      <h1 className="mt-3 text-lg font-semibold">This lesson is locked.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        Finish the earlier lessons first — they unlock this one in order.
      </p>
      <Link
        to="/paths/$pathId"
        params={{ pathId }}
        data-testid="lesson-locked-back"
        className={`mt-5 ${SECONDARY_CTA}`}
      >
        Back to your path
      </Link>
    </StateCard>
  );
}

function UnavailableState() {
  return (
    <StateCard testid="lesson-unavailable">
      <h1 className="text-lg font-semibold">We couldn't load this lesson.</h1>
      <p className="mx-auto mt-2 max-w-[24rem] text-sm leading-6 text-mist">
        It may have been deleted, or something went wrong. Head back to your paths and try again.
      </p>
    </StateCard>
  );
}
