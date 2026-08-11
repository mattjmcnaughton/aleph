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

## Status at a glance

Where each phase stands, and where its specification lives. **Shipped** means the
code is merged and deployed; **launched** means it is on for every learner rather
than admins alone (the flag playbook is in
[`deploy.md`](deploy.md#launching-a-flagged-phase-al-270--al-370)). A phase with no
PRD has not been specified yet — that document is its first deliverable, not an
afterthought.

| Phase | Status | Specs |
| ----- | ------ | ----- |
| **1 — The generated path (MVP)** | ✅ Shipped & launched | [PRD](prds/phase-1-path-generation.md) · [TDD](tdds/phase-1-path-generation.md) |
| **2 — The tutor** (2A, in-lesson) | ✅ Shipped & launched (`tutor` flag on, AL-270) | [PRD](prds/phase-2-tutor.md) · [TDD](tdds/phase-2-tutor.md) |
| **2B — Shape your path** (learner-initiated) | ✅ Shipped & launched (`shaping` flag on, AL-370) | [PRD](prds/phase-2b-shape-your-path.md) · [TDD](tdds/phase-2b-shape-your-path.md) |
| **3 — Flashcards and spaced repetition** | ✅ Shipped & launched (`flashcards` flag on) — all ten TDD tickets plus AL-410's card-management surface | [PRD](prds/phase-3-flashcards.md) · [TDD](tdds/phase-3-flashcards.md) · [mock](mocks/aleph-phase-3-flashcards.html) |
| **4 — Adaptive paths** | ⬜ Not started — **no PRD yet** (2B pre-built the proposal/apply machinery) | — |
| **5 — Momentum** | 🟡 In progress — streaks shipped **and launched**; goal ring and daily minutes unbuilt | streaks: [PRD](prds/phase-5-streaks.md) · [TDD](tdds/phase-5-streaks.md) |
| **6 — The analyst** | ✅ Shipped & launched (`analyst` flag on) — a second pillar beside paths, not a deepening of one | [PRD](prds/phase-6-analyst.md) · [TDD](tdds/phase-6-analyst.md) |
| **Beyond** | ⬜ Unscoped, sequenced against real usage | — |

Three tails hang off the shipped phases and are tracked as open issues rather than
here: the **eval artifact kinds** and their labelled calibration sets (AL-083/AL-091
for Phase 1, AL-250/AL-251 for Phase 2, AL-350/AL-351 for Phase 2B) and the
**ship-verification sweeps** (AL-270, AL-370) whose flag flips have already landed.
The Phase 2 and 2B epics (#82, #114) stay open until those close.

So, concretely, what is left to build: **the rest of Phase 5** — the goal ring and
the daily-minutes target, now that streaks are launched — then **all of Phase 4**,
plus the Phase 2 slices listed as deferred below. Phase 3 has shipped and launched
too, so within that numbered sequence no phase is waiting on a flag flip anymore —
only on code not yet built. **Phase 6 sits outside that sequence**: it is a second
pillar rather than the next rung of the first one, so it was sequenced by appetite
rather than by dependency — nothing above it blocked it, and it blocked nothing
above it. It has since shipped in full (epic #163) and launched — the `analyst`
flag flip landed ahead of the rest of AL-570's ship-verification, which stays
open on [#180](https://github.com/mattjmcnaughton/aleph/issues/180): the
guardrail reads, the retrieval-quality ritual, production smoke, and the
Logfire import. No phase in either sequence is waiting on a flag flip
anymore, though AL-570 itself is not done.

## Phase 1 — The generated path (MVP)

> ✅ **Status:** shipped and launched — accounts, generation, paths, lessons and
> Quick checks are live for every learner.
> 📄 **Full spec:** [Phase 1 PRD — Path generation](prds/phase-1-path-generation.md) ·
> [Phase 1 TDD](tdds/phase-1-path-generation.md)

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

> ✅ **Status:** both shipped slices are launched — **2A** (in-lesson tutor) and
> **2B** (Shape your path). Deferred to a later slice: the whole-path **Q&A** tutor,
> **selection-to-quote**, and summarized carried context.
> 📄 **Full spec:** [Phase 2 PRD — The tutor (in-lesson)](prds/phase-2-tutor.md) ·
> [Phase 2 TDD](tdds/phase-2-tutor.md) ·
> [Phase 2B PRD — Shape your path](prds/phase-2b-shape-your-path.md) ·
> [Phase 2B TDD](tdds/phase-2b-shape-your-path.md) ·
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

> ✅ **Status:** shipped and launched — the `flashcards` flag defaults on. Both specs
> are accepted — the product boundary ([PRD](prds/phase-3-flashcards.md), drawn in the
> [mock](mocks/aleph-phase-3-flashcards.html)) and the technical design
> ([TDD](tdds/phase-3-flashcards.md)) — and all ten tickets of the TDD's delivery plan (§16)
> have shipped: the cards and reviews tables, the pure ladder and daily selection, the
> drafting agent, every route, the streak union, the `/review` surface, the eval kind, the
> events, and the W24–W27 journeys. A post-phase ticket, **AL-410**, then added the one
> surface the daily queue never offered — browse every kept card, edit its text, delete it
> (`/cards`) — reversing two of PRD §7's original exclusions for reasons the TDD records (D16
> soft delete, D17 edit provenance); it shipped behind the same `flashcards` flag and launched
> with it. **Every route is live for every learner**: `FeatureFlag.FLASHCARDS` now defaults
> on, the fourth flag to run the `tutor`/`shaping`/`streaks` dark-then-flip playbook, per the
> flagged-phase runbook in [`deploy.md`](deploy.md); the flag stays registered as a kill
> switch. The retention loop Phase 5 was waiting on is now earning its keep — the streak
> slice's second signal is exactly the return metric this phase exists to feed
> ([streaks PRD §2](prds/phase-5-streaks.md)).
> 📄 **Full spec:** [Phase 3 PRD — Flashcards and spaced repetition](prds/phase-3-flashcards.md) ·
> [Phase 3 TDD](tdds/phase-3-flashcards.md) ·
> mock: [phase-3 flashcards](mocks/aleph-phase-3-flashcards.html)

Now we close the retention loop. When you finish a lesson, the AI drafts a handful
of candidate flashcards from it — and, importantly, you stay in control: you
review the drafts and keep only the ones worth remembering, exactly as the mock
frames it ("Claude drafted 4 cards from Generic constraints"). Kept cards enter a
spaced-repetition schedule with the familiar four-way grading — Again, Hard, Good,
Easy — so due cards resurface at widening intervals. This gives learners a reason
to return between lessons and turns passive reading into durable recall. It also
produces a second stream of signal about what a learner does and doesn't know,
which the next phase puts to work.

The [PRD](prds/phase-3-flashcards.md) departs from that paragraph in two places, both
deliberate. Cards are **learner-owned and reviewed in one queue spanning every path**,
not per path — the Daily streak is global, and mixing paths in a session is interleaving.
And grading ships as **two outcomes, not four**: a fixed interval ladder (Again / Got it)
rather than the Again/Hard/Good/Easy above, which needs ease factors this phase has no
data to tune. The four-way grading is deferred to a follow-on slice rather than dropped.
The PRD also adds a daily cap this paragraph never mentioned — ten cards a day, the seven
most overdue plus three at random — so a backlog after an absence is a bounded session
rather than a wall.

A later ticket, **AL-410**, rounds out the loop with the one thing the daily queue never
gave a learner: a way to see every kept card at once, fix one the agent got wrong, or drop
one that turned out not worth remembering — `/cards`, gated behind the same `flashcards`
flag and launched alongside everything else behind it.

## Phase 4 — Adaptive paths

> ⬜ **Status:** not started, and **no PRD or TDD yet** — but materially de-risked:
> Phase 2B already built and launched the proposal card, ghost-row preview,
> apply-with-undo and change history this phase would have had to invent
> ([Phase 2B TDD](tdds/phase-2b-shape-your-path.md)). What remains is genuinely
> Phase 4's own: the *system* proposing edits unprompted, which needs Phase 3's miss
> signal first, and the destructive edit shapes 2B declined.
> 📄 **Full spec:** none yet.

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

> 🟡 **Status:** partially built. The **streaks** slice is shipped and **launched**
> — the `streaks` flag defaults on, so every learner sees the streak line, the
> activity strip and the path chips, and the flag stays registered as a kill
> switch. The **weekly goal ring**, the **daily-minutes target** and the small
> **progress/stats view** are unbuilt and unspecified; "minutes this week" and
> "cards mastered" additionally wait on Phase 3.
> 📄 **Full spec (streaks only):** [Phase 5 streaks PRD](prds/phase-5-streaks.md) ·
> [Phase 5 streaks TDD](tdds/phase-5-streaks.md)

The final phase of the *learning-path* pillar turns the tool into a habit — it was
written as "the final core phase" when this roadmap had one pillar, and Phase 6
below is why that framing no longer holds. With learners already running
several paths, we add the light gamification the README is careful to bound: a
weekly goal ring, a day streak, a daily-minutes target, and a small progress view
with stats like cards mastered and minutes this week. The rule here is restraint —
streaks and progress tracking, and nothing more — because the point is to reinforce
the learning loop, not to bolt a game onto it.

The **streaks** slice was pulled forward and shipped early, the same move Phase
2B was for Phase 2 (📄 [Phase 5 streaks PRD](prds/phase-5-streaks.md)): a global
**Daily streak** and a per-path **Path streak**, both derived from
`lessons.completed_at` with no new table (a `GROUP BY` over rows that already
exist), plus the activity strip the mock draws as a heatmap — 49 days, seven
whole weeks, so the grid is exactly full. It ran the `streaks` flag through the
same playbook `tutor` and `shaping` used — dark through the build-out, admin
dogfood only, then one code-default flip — and is now **launched** for every
learner. The weekly goal ring and the daily-minutes target remain here, unbuilt,
for the rest of this phase.

## Phase 6 — The analyst

> ✅ **Status:** shipped and **launched** (epic #163's full first-slice build)
> — the `analyst` flag now defaults **on** in `FLAG_DEFAULTS`, the fifth flag
> to run the dark-then-flip playbook `tutor`/`shaping`/`streaks`/`flashcards`
> all ran
> ([deploy.md](deploy.md#launching-a-flagged-phase-al-270--al-370)), and
> `FEATURE_FLAG_DEFAULTS=analyst:off` stays the kill switch, reaching admins
> too with no deploy. **The flip landed ahead of**
> [AL-570](https://github.com/mattjmcnaughton/aleph/issues/180)'s
> ship-verification gates, which remain outstanding: both guardrail reads
> (`cost_per_read_brief.sql`, `brief_wait_tolerance.sql`, numbers recorded on
> the ticket), the retrieval-quality comparison written up for every
> dogfooded Beat, production smoke covering all four run outcomes, and the
> Logfire query import are all still open on #180. No dedicated Nocturne
> mock, on the Phase 5 precedent (specified against the existing tokens
> directly). The vocabulary is in [`CONTEXT.md`](CONTEXT.md) (the *The analyst*
> section), now marked shipped and launched rather than unbuilt.
> 📄 **Full spec:** [Phase 6 PRD — The analyst](prds/phase-6-analyst.md) ·
> [Phase 6 TDD](tdds/phase-6-analyst.md)

Every phase above deepens one pillar: a **path**, which teaches a body of knowledge
that was already settled when you asked for it. This phase adds a second pillar
beside it. A learner names a topic and **deploys an analyst** on it; the analyst
researches what has actually happened since it last reported and publishes a short,
cited **Brief** that builds on every Brief before it. Same reading surface, same
Markdown pipeline, deliberately the same shape of rail — and the opposite
relationship to time.

The design turns on three decisions the PRD argues at length, all shipped in the
first slice. **It is a sibling, not a "realtime path"**: linear unlock is the
wrong reading model for a feed, an infinite path has no denominator, and folding
Briefs into lesson-shaped counters would corrupt the **Activation rate** north
star irreversibly. **Nothing is scheduled**: cadence is a floor on frequency
rather than a calendar appointment, and work is driven entirely by learner
arrival — reaching the beats list or a Beat evaluates the cadence floor and
claims what is due, needing no cron, no always-on machine, and no deployment
change, and making an unread Beat cost exactly nothing. Time-axis **Brief
prefetch** (claimable a little before the Anchor day opens, so a warm moment
produces the next Brief early) is the named upgrade once the app has a warm
moment to exploit, but it is **deferred from this first slice** (PRD §7.1) — the
app sleeps between visits today, so arrival is the only trigger that actually
fires, and every first-slice Brief is researched while the learner waits. And
**nothing to report is a first-class outcome**: the failure mode that kills this
feature is not a broken trigger but Brief #7 confidently restating Brief #6, so a
period with no novel findings publishes a dated **Skipped** entry rather than
filler.

Two things the PRD deliberately leaves open. The **retrieval provider is
unnamed** — the document states three product constraints (URLs that resolve, a
usable publication date, enough text to ground a quote) and leaves the choice to
the TDD. And the phase adds a constraint the eval harness has never faced: live
retrieval makes an eval non-deterministic by construction, so the seed set has to
pin recorded retrieval fixtures or it measures the news rather than the agent.

The convergence with paths — flashcards drafted from a Brief, the tutor rail on a
Brief, "teach me the fundamentals of what I keep reading about", a Brief feeding a
Phase 4 proposal — is named in the PRD and built in none of it. That sequencing is
the point of building the sibling rather than forcing Briefs into `lessons` on day
one.

## Beyond

> ⬜ **Status:** unscoped by design — nothing here is committed to, and none of it
> has a PRD.
> 📄 **Full spec:** none, deliberately.

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
