# PRD — Phase 1: The generated path (MVP)

**Status:** Draft · **Owner:** solo builder · **Roadmap item:** [Phase 1](../roadmap.md#phase-1--the-generated-path-mvp)
**References:** [`README.md`](../../README.md) · [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) (ubiquitous language) · mocks: [web](../mocks/aleph-mvp-web.html), [mobile](../mocks/aleph-mvp-mobile.html)

> Companion doc: a separate **TDD** owns the technical design — model routing / multi-model
> architecture, prompts, storage schema, and hosting. This PRD stops at the product boundary.

## 1. Summary

The first vertical slice of Aleph: a self-directed adult learner names a topic and a rough
self-assessment, and the AI generates a structured learning **path** they can start immediately.
Each lesson is a short **Read passage** followed by one inline **Quick check** (a single-select
MCQ) that gives immediate, formative feedback. The learner moves through lessons linearly, marks
them complete, and their progress persists. A learner can run **multiple paths** at once and
switch between them.

This is deliberately the smallest thing that already feels like a tutor — one whole AI feature,
not a half-dozen partial ones. It establishes the data model
(**account → paths → units → lessons → quick checks**) every later phase builds on.

## 2. Context & goals

**Why this slice first.** The irreducible magic of Aleph is: *you name a topic and get a real,
personalized lesson you can learn from today.* Phase 1 is the thinnest end-to-end path to that
moment. It proves the hardest, most differentiating bet — that generated lessons are good enough
to learn from — before we invest in the tutor, retention, and adaptivity that compound it.

**Goals**
- A learner can go from "I want to learn X" to reading and passing their first lesson in one sitting.
- Generated content is accurate, scoped to the learner's stated level, and safe.
- The data model and generation pipeline are sound enough that Phases 2–5 extend rather than rework them.

**Deliberate revision of the roadmap.** The roadmap places "accounts and cross-device sync" in
*Beyond*; this PRD instead requires **accounts from day one** (auth + server-side persistence). The
learning loop's value depends on durable, per-learner data — progress, multiple paths, and the
metrics below all assume it — and adding accounts is cheap here, so we pull it forward rather than
retrofit it later. The roadmap's *Beyond* note is updated to match.

**Definition of "shipped":** see [§12 Release criteria](#12-release-criteria).

## 3. Target user & user stories

**User:** a self-directed adult learner on a phone, studying a topic of their own choosing —
from "how medical care is paid for in the US" to "Rust ownership." Motivated, time-boxed,
mobile-first, no instructor.

**Core stories**
1. *As a new learner,* I name a topic and pick a rough level, and get a structured path in return.
2. *As a learner,* I read a lesson and answer a quick check, and know immediately whether I understood it.
3. *As a learner,* I mark lessons complete and see my progress persist when I come back.
4. *As a learner,* I run more than one path at once and switch between them without losing my place.

## 4. Scope

**In scope (Phase 1 only)**
- Onboarding: capture topic + self-assessed level.
- AI path generation: outline (units → lessons), then lesson content generated on demand.
- Lesson viewer: Read passage + one single-select MCQ Quick check with immediate feedback.
- Linear progression + mark-complete + persistent progress.
- Multiple paths per account, with a switcher; delete a path (the reset mechanism).
- Accounts from day one (auth + server-side persistence).
- Defined failure/error states for every generation point, with retry (§5.6).
- Analytics instrumentation sufficient to compute the §7 metrics (§5.7).
- The **Nocturne** visual system (dark, teal, mobile-first) from the mocks.

**Non-goals (explicit — present in the mocks but later phases)**
- ❌ **Tutor chat** rail / comprehension chat — Phase 2.
- ❌ **Flashcards & spaced repetition** — Phase 3.
- ❌ **Adaptive path edits** ("Shape your path", refreshers, detours) — Phase 4.
- ❌ **Gamification** — streaks, goal rings, daily-minutes, progress stats — Phase 5.
- ❌ Regenerating or editing a generated path/lesson (see §11 risk).
- ❌ Free-text or AI-graded answers; short-answer checks.
- ❌ Rich lesson formats (diagrams, runnable code), sharing/importing paths, cross-device analytics — Beyond.

> Note on the mocks: they show the tutor rail, streaks, and stats. Those are **layout targets for
> later phases**, not Phase 1. Phase 1 renders the path, lesson, and multi-path switcher surfaces only.

## 5. Functional requirements

**5.1 Onboarding**
- Learner enters a topic (free text) and selects a level: *new to it · some experience · I work in it*.
- On submit, the system generates a path outline and lands the learner on the path view.
- A visible generating/loading state covers outline generation.

**5.2 Path generation**
- The AI produces a **path** = ordered **units**, each an ordered set of **lessons**, sized in the
  spirit of the mock (≈5 units, a handful of lessons each — bounded so a path is finishable, not endless).
- Outline is generated first; **lesson content (Read + Quick check) is generated on demand** as the
  learner reaches a lesson, with **prefetch of +N lessons ahead** to hide latency.
- Generation is scoped to the learner's stated level (a "new to it" TypeScript path differs from an
  "I work in it" one).
- **Continuity — lesson N+1 is conditioned on the actual content of lessons 1…N**, not just the
  outline. The generator sees what earlier lessons already taught so it can build on prior concepts,
  avoid re-teaching or contradicting them, and reference terms the learner has already met. Lessons
  are generated **in order** and a lesson's content is fixed once generated (there is no
  regeneration), so prefetch must respect that ordering: lesson N+1 is only prefetched once lessons
  1…N exist, giving it complete, stable prior-lesson context. (Concurrency/ordering mechanics are the
  TDD's.)
- **No regeneration in MVP:** the learner accepts the generated path as-is or starts a new one.

**5.3 Lesson viewer**
- Renders a short **Read passage**, then one **Quick check**.
- **Quick check** = single-select MCQ, 3–4 options, exactly one correct. The learner's **Attempt**
  (selected option) is graded deterministically by the app (no model call to grade).
- On Attempt: reveal the **Outcome** (correct/incorrect) + a short explanation. **Formative and
  non-gating** — the learner proceeds and can mark complete regardless of the Outcome.

**5.4 Progression & persistence**
- Lessons are taken linearly; the next unlocks as the prior completes (matches the mock's
  Complete / current / locked rail states).
- "Mark complete" persists per lesson; path/unit completion derives from lesson unlock state.
- Progress is stored server-side against the account and restored on return.
- **Path completion:** when the last lesson of the last unit is marked complete, the path is shown as
  complete on the path view and in "Your paths." No new content is generated; the learner can revisit
  lessons, start another path, or delete this one. (A dedicated "what next" experience is out of scope.)

**5.5 Multiple paths**
- A learner can create additional paths via **"New path,"** which re-runs the §5.1 onboarding capture
  (topic + level) and generates a new outline. Learners switch between paths from the "Your paths"
  list / sidebar switcher; each path remembers its own progress independently.
- A learner can **delete a path**. Deletion removes the path and its progress, and is confirmed
  before it happens (it is destructive and not undoable in MVP). This is also the **reset** mechanism:
  since there is no regenerate, a learner who is unhappy with a generated path deletes it and creates
  a new one.

**5.6 Failure & error states**
- Generation can fail (model error, timeout, refusal). Every generation point — outline at path
  creation, and lesson content on demand — has a defined learner-facing state: a clear message and a
  **retry** affordance, never a blank screen, a spinner that never resolves, or partial/garbled output.
- Outline failure keeps the learner on onboarding with their topic/level intact so retry is one tap.
- Lesson failure leaves prior progress and other paths untouched; the learner can retry the lesson or
  navigate away.
- Safety refusals are a distinct, non-error message (see §10), not a "something went wrong."

**5.7 Instrumentation**
- The app emits analytics events sufficient to compute every metric in §7: at minimum account
  created, path created, outline generated (success/failure/latency), lesson generated
  (success/failure/latency), lesson viewed, quick-check attempted (with outcome), lesson marked
  complete, path completed, path deleted — each stamped with account, path, lesson, and timestamp.
- **Session** = activity with no gap longer than 30 minutes. **Day** = a calendar day in the
  learner's local timezone. These definitions are shared with CONTEXT.md.

## 6. AI system design (product view)

**Data model.** `account → paths → units → lessons → quick checks`.
- **account** — the authenticated learner (day one).
- **path** — topic + level + generated outline + progress.
- **unit** — ordered grouping within a path.
- **lesson** — Read passage + one Quick check; has a **generation state** (ungenerated → generated)
  and an **unlock state** (locked → available → complete), which are orthogonal.
- **quick check** — the MCQ artifact: stem, options, correct option, explanation.
- **attempt** — a learner's answer to a quick check (selected option); its **outcome** is correct or
  incorrect.

**Generation strategy.** Two-step: (1) generate the outline once at path creation; (2) generate each
lesson's content on demand as it's reached, prefetching +N ahead. Lesson generation is conditioned
on the path topic, level, unit, the lesson's place in the sequence, **and the full content of all
prior lessons (1…N)** — so the path reads as one continuous course that builds on itself rather than
a set of independent generations. How prior-lesson context is carried (full text vs. running summary)
and its token-budget implications are owned by the TDD.

**Model architecture.** Assume a **multi-model** setup (e.g. a stronger pass for structure, a faster
pass for per-lesson content). **Exact routing/tiering is specified in the TDD**, not here.

## 7. Success metrics

**North star — Activation rate.**
> The **% of new accounts that become *activated* within 7 days of signup**, where *activated* =
> completed **more than 3 lessons** (≥ 4), each with a recorded Quick-check **Attempt**.

Rationale for the shape:
- **A rate, not a count.** A raw count only ever rises with time and can't tell you whether the
  product improved; a windowed rate (denominator = new accounts in the cohort) can.
- **7-day window.** Long enough to allow a return visit, short enough to be a fast feedback loop.
- **Why >3 lessons.** It's past the one-lesson novelty spike and requires on-demand generation of
  *later* lessons to have worked — a deliberate proxy for "the loop held up," not a calibrated value.
  Revisit once we have real distribution data.
- **Attempt required.** Because "mark complete" is non-gating and cheap, we count a lesson only if the
  learner actually attempted its Quick check — so four reflexive taps don't read as activation.
- **Per account, not per path.** 4 lessons on one path and 2+2 across two are *not* the same signal;
  the north star counts lessons completed on a **single path**. (Breadth across paths is a supporting
  metric, not the north star.)

**Supporting metrics** (diagnose *why* the north star moves)
- **First-lesson activation:** % of new accounts that complete ≥ 1 lesson (with an Attempt) in their first session.
- **Path start rate:** % of generated paths where the learner starts lesson 1 (proxy for
  outline quality on first try — we have no regenerate, so a bad outline shows up here).
- **Lesson-to-lesson continuation:** % of completed lessons followed by starting the next.
- **Return:** % of activated learners who come back on a second distinct day.
- **Breadth:** % of activated learners running 2+ paths.

**Guardrail / counter-metrics** (things we must *not* break chasing the north star)
- **Quick-check correctness rate** in a sane band — near-100% signals trivial questions; very low
  signals broken/mis-keyed questions.
- **Generation failure / latency:** rate of failed generations and p95 lesson-generation wait.
- **Generation cost:** average model cost **per generated path** and **per activated learner**.
  Continuity (§6) makes later lessons more expensive, so this must be watched, not assumed cheap.
- **Eval pass rate** (see §9) stays above the release gate.

> All of the above are computable from the §5.7 instrumentation; "session" and "day" are defined there.

## 8. Core workflows (E2E)

These double as the end-to-end test suite. Each is a full user journey with a clear pass/fail.

**W1 — New learner, first path, first lesson (the magic moment)**
Sign up → enter topic + level → outline generates → land on path → open lesson 1 → read → answer
Quick check → see feedback → mark complete → lesson 2 is available.
*Pass:* a real path with real lesson content renders and the learner completes lesson 1.

**W2 — Progress persists across sessions**
Complete ≥ 1 lesson → sign out / reload → sign back in → path and completed state are exactly as left,
and the learner resumes at the right lesson.

**W3 — Reach the north-star threshold**
From a fresh path, complete lessons 1→4 in sequence, including on-demand generation of later lessons.
*Pass:* four lessons complete, no dead-ends, prefetch keeps waits within budget.

**W4 — Multiple paths, independent progress**
Create path A, complete a lesson → create path B → switch back to A → A's progress intact and B's
untouched → switch via the "Your paths" list.

**W5 — Delete a path (reset)**
With paths A and B, delete A → confirm → A and its progress are gone, B is untouched and still
switchable → creating a fresh path still works. *Pass:* deletion is confirmed, removes only the
target path, and leaves the account in a clean state.

**W6 — Quick-check Outcome, both branches**
Make a correct Attempt on one Quick check (positive Outcome) and an incorrect Attempt on another
(correct answer + explanation shown) → in both cases the learner can still proceed and mark complete
(non-gating).

**W7 — Unsafe topic is refused gracefully**
Enter a topic over the safety boundary (§10) → generation refuses with a clear, non-error message →
the app stays usable (no crash, no partial harmful content).

**W8 — Generation failure is recoverable**
Force an outline (and, separately, a lesson) generation to fail → learner sees a clear error with a
retry, not a dead spinner → retry succeeds and the journey continues → prior progress and other paths
are untouched (§5.6).

> **Open (owned by the TDD):** how these run in CI against nondeterministic, paid, latency-heavy
> generation. Options include recorded fixtures / a stub generator for deterministic assertions
> versus periodic live-generation smoke runs. This choice also defines what "real lesson content
> renders" actually asserts (structure and invariants, not exact text).

## 9. Evals (AI components)

**What we eval.** Two generated artifacts per lesson: the **Read passage** and the **Quick-check MCQ**
(plus the outline at path level).

**Method — binary LLM-as-judge.** A judge model scores each generation **pass/fail** against a simple
rubric. Deliberately binary (not a 1–5 scale) to keep the signal unambiguous and the gate easy to
reason about. For MVP the judge is a **prompted frontier model** with a rubric and few-shot examples
("trained" in the calibrated-by-examples sense) — **fine-tuning is out of scope** unless calibration
(below) shows prompting can't reach acceptable agreement with human labels.

**Rubric (all must pass → PASS):**
1. **Accurate** — factually correct, no hallucinated specifics.
2. **Level-appropriate** — matches the learner's stated level.
3. **In scope** — on-topic for the path/unit/lesson, right size for one sitting.
4. **Continuous** — builds on lessons 1…N: doesn't re-teach or contradict earlier lessons, and only
   assumes concepts already introduced. Evaluated with prior-lesson content in the judge's context.
5. **Check validity** — the MCQ is answerable from its own Read passage, has exactly one correct
   option, and the keyed answer is actually correct. *(This item can also be checked
   deterministically/self-consistency as a cheap pre-filter.)*
6. **Safe** — within the §10 boundary.

**Harness.** Run the judge over a **fixed seed set of representative topics × levels** (e.g.
TypeScript, SQL performance, Rust ownership, plus a non-technical topic and a sensitive-but-legitimate
topic). Regenerate + re-judge on every change to prompts or generation logic; treat it as a
regression suite.

**Calibration.** The judge is only as good as its agreement with a human. Maintain a small
**human-labeled set** (≈ 30–50 generations the builder has marked pass/fail) and measure judge↔human
agreement; the judge is trusted as a gate only while agreement stays high (target ≥ 90%). Re-check
after any prompt change to the judge.

**Gates.** Shipping / merging a generation change requires the judge to pass **≥ 90% of a seed set of
~20 representative topic × level pairs** (provisional numbers — recalibrate once we have real
distributions and the human-labeled agreement figure above). Any safety-rubric failure is a hard
block regardless of the aggregate rate.

## 10. Guardrails & safety

- **Topic policy: open with a refusal boundary.** Any genuine learning topic is allowed — including
  sensitive ones (healthcare, law, security *concepts*). The system **refuses** to generate content
  that materially aids serious harm (weapons, illicit synthesis, targeted wrongdoing).
- Refusals are graceful (W6): a clear message, app stays usable, no partial harmful output.
- Safety is a rubric item in the eval (§9), so it's measured, not assumed.
- Content-quality guardrails (accuracy, scope) are enforced through the same eval loop, matching the
  roadmap's "AI content quality is a first-class concern from Phase 1."

## 11. Risks & open questions

- **No regenerate + no adaptivity → a bad generation is a dead end.** Mitigation: the eval gate, plus
  **delete-the-path-and-start-fresh** as the reset escape hatch (§5.5); revisit per-lesson regenerate
  if the path-start / continuation metrics disappoint. *(Open: do we need a lightweight "this lesson
  looks wrong" signal even in MVP?)*
- **On-demand generation latency is in the learner's critical path.** Mitigation: prefetch +N ahead;
  needs a real latency budget (owned by the TDD).
- **Judge quality bounds eval quality.** A weak binary judge passes bad content. Mitigation: the
  human-labeled calibration set and judge↔human agreement check (§9); the judge is itself iterated.
- **Level self-assessment is coarse** (3 buckets). Acceptable for MVP; adaptivity (Phase 4) is the
  real fix.
- **Open:** path-size bounds (units/lessons) — target ≈ the mock; confirm exact caps in the TDD.
- **Open:** value of `N` for prefetch — tune against the latency budget.
- **Prior-lesson context grows with path length** (continuity requirement, §5.2/§6): later lessons
  carry more preceding content, pressuring the token budget and cost. Mitigation (running summary vs.
  full text, truncation policy) is owned by the TDD.

## 12. Release criteria

Phase 1 is shipped (solo-builder, internal-first) when:
- [ ] W1–W8 pass end-to-end on real topics, on a phone-sized viewport.
- [ ] A learner (you + a few invited users) can complete **> 3 lessons** on a single real path — the
      north-star journey works end to end.
- [ ] Accounts + server-side persistence work: sign in on a fresh load restores paths and progress (W2).
- [ ] Multiple paths with independent progress work (W4); delete resets cleanly (W5).
- [ ] Generation failures are recoverable, not dead-ends (W8), and unsafe topics are refused (W7).
- [ ] The eval seed set passes at **≥ 90%** with zero safety failures, and judge↔human agreement is
      measured (§9).
- [ ] Instrumentation emits the §5.7 events, so the §7 activation rate is actually computable.
- [ ] Generation latency stays within the TDD's budget under normal use (prefetch working).
- [ ] Surfaces extend **Nocturne** (dark, teal, mobile-first), matching the mocks.

---

### Appendix — traceability to the roadmap

| Roadmap Phase-1 element | Where in this PRD |
| --- | --- |
| Topic + self-assessment onboarding | §5.1 |
| AI drafts a path of units & lessons | §5.2, §6 |
| Real lesson content: Read passage + Quick check | §5.3 |
| Linear progression, mark complete, persist | §5.4 |
| Multiple paths + switcher | §5.5 |
| Data model (account → paths → units → lessons → quick checks) | §6 |
| AI content quality as first-class concern | §9, §10 |
| Nocturne, mobile-first | §4, §12 release |
