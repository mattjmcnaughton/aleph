# Aleph roadmap

This is a functional roadmap: what Aleph does, and the order we build it in. It
is intentionally high-level — each phase is a paragraph, not a spec — and it is
sequenced so that every phase ships a product someone could actually use, then
the next phase deepens it. The reference points are [`README.md`](../README.md)
and the MVP mocks in [`docs/mocks/`](./mocks/).

## North star

Aleph is a mobile-friendly AI tutor for self-directed adult learners studying
anything they choose. The irreducible magic is simple: you name a topic, and you
get a real, personalized lesson you can learn from today. Everything on this
roadmap either delivers that moment or compounds it — sharper recall, a tutor
that knows where you are, a path that bends around your weak spots. We build the
whole loop one vertical slice at a time rather than spreading thin across
half-finished features.

## Phase 1 — The generated path (MVP)

> 📄 **Full spec:** [Phase 1 PRD — Path generation](prds/phase-1-path-generation.md)

The MVP is the first complete vertical slice: name a topic and skill level, and
Aleph generates a structured path you can immediately start learning. Onboarding
takes a topic (or a goal) plus a rough self-assessment — "new to it," "some
experience," "I work in it" — and the AI drafts a path of units and lessons, the
way the mock lays out TypeScript across five units from Foundations to Modules.
Crucially, the AI generates real lesson content, not just an outline: each lesson
is a short "Read" passage followed by an inline "Quick check" question that gives
immediate feedback. You move through lessons linearly, marking them complete, and
your progress persists. Multiple paths are supported from the start: you can spin
up more than one — Learn TypeScript, SQL performance, Rust ownership — and switch
between them from the "Your paths" list or the sidebar switcher, each remembering
its own progress. This is deliberately the smallest thing that already feels like a
tutor — one AI feature, but a whole one — and it establishes the data model
(account → paths → units → lessons → quick checks) that every later phase builds on.
Because that durable, per-learner data underpins the whole loop, this phase also
introduces **accounts** — authentication and server-side persistence — pulled
forward from "Beyond" rather than retrofitted later. The full specification lives in
the [Phase 1 PRD](prds/phase-1-path-generation.md), which also defines the phase's
success metrics, end-to-end workflows, and AI evals.

## Phase 2 — The tutor

> 📄 **Full spec:** [Phase 2 PRD — The tutor (in-lesson)](prds/phase-2-tutor.md) ·
> mock: [phase-2 tutor](mocks/aleph-phase-2-tutor.html)

With a real path to talk about, we add the feature that makes Aleph a tutor and
not just a generated course: a context-aware chat that always knows where you
are. Docked as a right rail on the web and a sheet over the lesson on mobile, the
tutor carries the current lesson's context — its Read passage, its Quick check,
and your answer to it — so "explain this simpler," "go deeper," and "quiz me on
this" all resolve against exactly what you're looking at, streamed back as they
are written. It can ask a question back, too — a non-scoring **Tutor check** that
sits outside your lesson progress entirely — and it can reference what you've
already covered ("you're on Generics, with Utility Types still ahead") from a thin
digest of lesson names and progress, without reading another lesson's content. This
phase is chat and comprehension only; it reads your path and speaks about it, but it
does not change your path or your flashcards.

The phase ships the **in-lesson** tutor first, on its own (2A, shipped). The
follow-on slice — **2B** — is not the whole-path Q&A tutor originally sketched
here; it is **Shape your path, learner-initiated**: on the path view, the tutor
can *change* the path on your instruction — add lessons where something is
missing, revise an upcoming lesson to your pitch — always as a proposal you
preview, apply, and can undo, never a silent rewrite. That slice was pulled
forward from Phase 4 by owner decision, superseding the original plan to gate 2B
on in-lesson adoption data (📄 [Phase 2B PRD — Shape your path](prds/phase-2b-shape-your-path.md)).
It resolves the collision with Phase 1's continuity and immutability rules by
shrinking the edit vocabulary: additions and revisions only, and content becomes
**immutable once engaged** rather than once generated. It is built, deployed and
now **launched**: AL-370 flipped the `shaping` flag's global default on, so every
learner sees it rather than admins alone (AL-270 did the same for the in-lesson
tutor). Both flags stay registered as kill switches.
The whole-path *Q&A* slice
(path scope, scope switching, lesson citations as links) and **selection-to-quote**
are re-deferred to a later slice, sequenced against real usage.

## Phase 3 — Flashcards and spaced repetition

Now we close the retention loop. When you finish a lesson, the AI drafts a handful
of candidate flashcards from it — and, importantly, you stay in control: you
review the drafts and keep only the ones worth remembering, exactly as the mock
frames it ("Claude drafted 4 cards from Generic constraints"). Kept cards enter a
spaced-repetition schedule with the familiar four-way grading — Again, Hard, Good,
Easy — so due cards resurface at widening intervals. This gives learners a reason
to return between lessons and turns passive reading into durable recall. It also
produces a second stream of signal about what a learner does and doesn't know,
which the next phase puts to work.

## Phase 4 — Adaptive paths

This is where "dynamically generated" earns its name. Until now the path is
generated once and then fixed; here it starts bending around the individual. Using
the miss data from Quick-checks and the flashcard schedule — plus what surfaces in
tutor conversation — Aleph proposes concrete, targeted edits to your path through
the "Shape your path" flow: slot a five-minute Narrowing refresher before Unit 4
because you missed it twice and never reviewed, or add a short detour on a concept
you keep confusing. Every change is a suggestion you accept or decline, never a
silent rewrite, and each is small and legible ("one short lesson, then straight
into Utility Types"). This phase depends on both the tutor and the quiz/flashcard
signal already existing, which is why it comes fourth rather than first.

Much of this machinery now ships earlier: **Phase 2B builds the learner-initiated
flow** — the proposal card, ghost-row preview, apply-with-undo, and change history
that Turn 3 of the [Phase 2 mock](mocks/aleph-phase-2-tutor.html) draws — for
additive edits and revisions of not-yet-engaged lessons
([Phase 2B PRD](prds/phase-2b-shape-your-path.md)). Phase 4's own contribution is
what was always its center: the **system** proposing edits unprompted from miss
data, reusing 2B's proposal/apply machinery. Phase 4 also owns the edit shapes 2B
deliberately declined — removing and reordering content ("cut the decorators
stuff") — which genuinely break continuity and need their own design, along with
any change that touches finished work.

## Phase 5 — Momentum

The final core phase turns the tool into a habit. With learners already running
several paths, we add the light gamification the README is careful to bound: a
weekly goal ring, a day streak, a daily-minutes target, and a small progress view
with stats like cards mastered and minutes this week. The rule here is restraint —
streaks and progress tracking, and nothing more — because the point is to reinforce
the learning loop, not to bolt a game onto it.

## Beyond

Once the core loop is proven end to end, natural extensions open up: richer lesson
formats (diagrams, worked examples, code you can run), cross-device sync, sharing or
importing paths, and deeper analytics on how a learner is progressing. (Accounts
themselves are no longer here — Phase 1 pulls them forward, since the learning loop
needs durable per-learner data from the start.) These are deliberately out of scope
for the phases above and will be sequenced later against real usage rather than
guessed at now.

## Cross-cutting concerns

A few things span every phase rather than living in one. The visual system is
**Nocturne** — the dark, teal, mobile-first design language already established in
the mocks — and new surfaces should extend it rather than reinvent it. **AI content
quality** is a first-class concern from Phase 1: generated lessons and cards need to
be accurate, appropriately scoped, and safe, so evaluation and guardrails grow
alongside the features that generate content. And because the audience is
**mobile-first self-directed learners**, every phase is designed for a phone first
and a desktop second, matching the two mocks that already exist side by side.
