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
(learner → paths → units → lessons → checks) that every later phase builds on.

## Phase 2 — The tutor

With a real path to talk about, we add the feature that makes Aleph a tutor and
not just a generated course: a context-aware chat that always knows where you
are. Docked as a right rail on the web and a tab on mobile, the tutor carries the
current lesson's context inside a lesson and the whole-path context on the path
view — so "explain this simpler," "go deeper," and "quiz me on this" all resolve
against exactly what you're looking at. It can answer questions, reframe an
explanation, and reference what you've already covered ("you're on Generics, with
Utility Types still ahead"). This phase is chat and comprehension only; it reads
your path and speaks about it, but it does not yet change your path or your
flashcards — those hooks come next, once the conversation itself is solid.

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

## Phase 5 — Momentum

The final core phase turns the tool into a habit. With learners already running
several paths, we add the light gamification the README is careful to bound: a
weekly goal ring, a day streak, a daily-minutes target, and a small progress view
with stats like cards mastered and minutes this week. The rule here is restraint —
streaks and progress tracking, and nothing more — because the point is to reinforce
the learning loop, not to bolt a game onto it.

## Beyond

Once the core loop is proven end to end, natural extensions open up: richer lesson
formats (diagrams, worked examples, code you can run), accounts and cross-device
sync, sharing or importing paths, and deeper analytics on how a learner is
progressing. These are deliberately out of scope for the phases above and will be
sequenced later against real usage rather than guessed at now.

## Cross-cutting concerns

A few things span every phase rather than living in one. The visual system is
**Nocturne** — the dark, teal, mobile-first design language already established in
the mocks — and new surfaces should extend it rather than reinvent it. **AI content
quality** is a first-class concern from Phase 1: generated lessons and cards need to
be accurate, appropriately scoped, and safe, so evaluation and guardrails grow
alongside the features that generate content. And because the audience is
**mobile-first self-directed learners**, every phase is designed for a phone first
and a desktop second, matching the two mocks that already exist side by side.
