# PRD — Phase 3: Flashcards and spaced repetition

**Status:** Proposal (not accepted) · **Owner:** solo builder · **Roadmap item:** [Phase 3 — Flashcards and spaced repetition](../roadmap.md#phase-3--flashcards-and-spaced-repetition)
**Companion to:** [Phase 3 TDD](../tdds/phase-3-flashcards.md) — this document owns the product boundary only
**References:** [`README.md`](../../README.md) · [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) (ubiquitous language) · [Phase 1 PRD](phase-1-path-generation.md) · [Phase 2B PRD](phase-2b-shape-your-path.md) · [Phase 5 streaks PRD](phase-5-streaks.md) · [`metrics.md`](../metrics.md) · mock: [phase-3 flashcards](../mocks/aleph-phase-3-flashcards.html) · prior art: habagou `domains/scheduling.py`, [ADR 0008](https://github.com/mattjmcnaughton/habagou/blob/main/docs/adrs/0008-review-state-as-rebuildable-projection.md)

> **This document owns the product boundary only.** Schema, the scheduler's
> implementation, the API, how the day's queue is pinned, instrumentation and delivery belong
> to the [TDD](../tdds/phase-3-flashcards.md), which now exists. Where this PRD names a
> mechanism it is because the product rule is unintelligible without it; the TDD owns how —
> and it settles §8's open questions 3, 4 and 5 in its own §14.

## 1. Summary

When a learner finishes a lesson, Aleph drafts a handful of flashcards from it. The learner
keeps the ones worth remembering and discards the rest. Kept cards enter a spaced-repetition
schedule and come back on widening intervals, capped at **10 cards a day**, in one queue that
spans every path.

Three surfaces, drawn in the [mock](../mocks/aleph-phase-3-flashcards.html):

- **Drafting** — at the end of a lesson, four proposed cards with keep/discard per card.
- **The daily queue** — a *Due today* card on home, and a due count in the app bar.
- **The review session** — front, reveal, grade, next.

This is the phase that closes the retention loop. Phases 1 and 2 make a learner read and
understand; nothing yet makes them *remember*, and nothing yet gives them a reason to open
Aleph on a day they do not want to start a new lesson.

## 2. Why now, and what it changes about the plan

Phase 5's streak slice shipped ahead of this one and its own PRD says why that is awkward:
"a streak counts returns the product does not yet earn… it should be read as instrumentation
for the return metric, not as a retention feature in its own right"
([streaks PRD §2](phase-5-streaks.md)). **This phase is what earns them.** The streak is
already live and measuring; this gives it something to measure.

It also feeds Phase 4. Adaptive paths need a second signal about what a learner does and
does not know, beyond Quick-check misses. §5 names the read seam and builds nothing for it.

**Two departures from the roadmap, both deliberate:**

1. **Grading is two outcomes, not four.** The roadmap promises "the familiar four-way grading
   — Again, Hard, Good, Easy". This PRD ships **Again / Got it** (§4.6). Four labels over a
   two-outcome ladder is a lie the UI tells, and the owner's call was "whatever is easiest
   from a spaced repetition perspective — we can refine later". Ease factors and the
   four-way grading are named as the phase's own follow-on slice, not dropped.
2. **A daily cap the roadmap does not mention.** Ten cards a day, 7 most overdue + 3 random
   (§4.4). Standard SRS shows everything due; this deliberately does not.

**Accepting this proposal includes** (the vocabulary is authoritative, so the docs say what
the code does on the same day — the same step 0 the streaks slice took):

- **[`CONTEXT.md`](../CONTEXT.md)** — add **Flashcard**, **Draft**, **Kept card**, **Due**,
  **Daily queue**, **Review**, **Lapse** to a new *Retention* section; amend the
  phase-boundary bullet that currently defers Flashcard / spaced repetition / grading to
  Phase 3, and correct its "Again/Hard/Good/Easy" to the two grades this ships. **Already
  done for the streak-bearing terms**: **Active day** now counts a review, **Daily streak**
  names its second source, and **Path streak** records that reviews are excluded from it
  (§4.9). Those three landed ahead of the rest because the definition had to change while
  changing it was still free.
- **[`roadmap.md`](../roadmap.md)** — Phase 3's status blockquote moves off "no PRD yet",
  and the two departures above are recorded in its paragraph.
- **[`docs/api.md`](../api.md)** — the new endpoints.
- **[`docs/metrics.md`](../metrics.md)** — the new saved queries (§5).
- **[`docs/evals.md`](../evals.md)** — the `flashcard_draft` artifact kind (§6).

## 3. What a learner sees

**At the end of a lesson.** The lesson is already complete — the completion is recorded and
the streak has already advanced — and *below* that, a proposal: `Aleph drafted 4 cards`, each
a front and a back, each with a keep/discard toggle, all keeping by default. The primary
action names what it will do: `Keep 3 cards`. `Skip — keep none` is beside it, equally
reachable. Discarded drafts are not saved anywhere.

**Home (`/`, "Your paths").** Above the path list, below the streak line: a *Due today* card
reading `10 cards · ~4 min`, with a `Review` action and a one-line breakdown of where they
came from. Each path row carries a `Review 7` chip when that path has cards in today's queue.

**Everywhere.** A `10 due` pill in the app bar — the one piece of persistent navigation this
phase adds, and the only way to reach review from inside a lesson without going home first.

**The review session.** One card at a time: the front, a tap to reveal the back, then two
grades. A `Card 4 of 10` counter and an `All paths` chip that says which scope you are in.
Under the card, its source: `From Generic constraints · Learn TypeScript`, a link.

**On the day's first review.** The streak line advances and briefly says `Day 7 🔥` — the
same line, in the same place, with the same restraint as the day's first lesson completion
(§4.9). A review-only day keeps the streak alive. There is no second celebration for the
second card.

**When the day's queue is finished.** The session ends. There is no "study more" button —
the cap is the point (§4.4).

**Never:** a push, an email, a "your cards are piling up" warning, a leech shaming screen, a
card count that reads as debt, or a leaderboard. This inherits Phase 5's restraint rule
verbatim.

## 4. Product decisions

**4.1 A flashcard belongs to the learner, not to the lesson.** A card keeps a reference to
the lesson that produced it — for the citation, and for Phase 4's signal — but its life is
not that lesson's life. This is forced by §4.3: one queue across every path cannot be
assembled from rows owned by paths that come and go. It is also what makes 2B safe: shaping
can revise a lesson under a card without the card evaporating.

**4.2 Aleph proposes, the learner disposes.** Nothing enters the schedule without an explicit
keep. The roadmap is emphatic about this ("you stay in control") and it is the same
proposal/approve shape 2B already established for path edits — the learner previews, then
applies. Cards default to *kept* so the common case is one tap, not four.

**4.3 One schedule, global; path is a filter, not a second queue.** A card has one due date
and lives in one queue spanning every path. A path entry (`/review?path=…`) filters that
queue; it does not open another. Three reasons: the Daily streak the learner is already
keeping is global, so a per-path queue would give them three habits against one streak;
mixing paths in a session is **interleaving**, which retains better than drilling one path at
a time; and two schedules mean two answers to "what is due".

**4.4 Ten cards a day — the 7 most overdue, plus 3 at random.** Let *D* be the cards due at
or before the end of the learner's local day, most overdue first.

- If *|D| ≤ 10*, the queue is all of *D*. The split below never applies — which is the
  ordinary case for a learner who reviews most days.
- If *|D| > 10*, the queue is the **7 most overdue**, plus **3 drawn uniformly at random**
  from the rest of *D*.
- Cards not selected stay due and are candidates again tomorrow, when the random draw is
  made fresh.
- **No top-up.** A learner with 3 cards due reviews 3 — not-yet-due cards are never pulled
  forward to fill the day.

The 3 random slots are **anti-starvation**, and that is their whole job: without them a large
backlog means the oldest cards monopolize every session forever and a mid-aged card is never
seen again. They bound the expected wait; they do not eliminate it, and the TDD should say
what the wait actually is at realistic backlog sizes.

**4.5 The day's queue is decided once and does not change.** The set is chosen on the first
request of the learner's local day and persists: leaving and returning resumes the same
cards, and a reload does not re-roll the random three. Without this, "3 at random" is a
reroll button.

**4.6 Two grades: Again and Got it.** The schedule is a fixed interval ladder — *Again*
demotes a card one rung, *Got it* promotes it one, and the rung determines the next interval.
No ease factor, no per-card divergence. This is habagou's shipped scheduler
(`domains/scheduling.py`), a pure function with nothing to tune, and it is the right first
answer for a scheduler with no real review data to tune *against*.

The daily cap is a second argument for it: with a cap, intervals get violated whenever there
is a backlog, so "due" is advisory. A ladder shrugs at a late review; SM-2's ease factors are
supposed to *react* to lateness, which would import a correction problem before there is
anything to correct.

**4.7 A lapse does not cost another card its slot.** Grading *Again* returns the card later
in the same session. The cap counts **distinct cards**, so a lapse never pushes a different
card out of the day.

**4.8 Home shows today's ten, not the backlog.** The count on home and in the app bar is the
size of today's queue — the number the learner can act on. The true outstanding total is not
displayed anywhere. A learner returning from a week away sees `10 cards`, not `73`, because
the second number's only effect is to make them close the app. The cost is that the debt is
invisible: §7 asks whether that survives contact.

**4.9 A review keeps the streak alive.** An **Active day** is a day on which the learner
completed a lesson **or reviewed a flashcard** — [`CONTEXT.md`](../CONTEXT.md) is amended to
say so, and that amendment is part of accepting this document (§2). One reviewed card is
enough, the same lowest-honest-bar the streak already takes for lessons (streaks PRD §4.5):
membership in the set *is* the target, so there is no second threshold to justify or drift.

The alternative was to leave **Active day** as lesson completion alone, which would mean a
learner who clears their whole daily queue and starts no lesson **breaks a streak they were
actively maintaining** — a retention feature punishing retention. The objection to widening
was that it rewrites history; it does not, and the timing is why. **No review exists yet**, so
adding the second signal adds no past Active day and moves no live streak. Deciding it now,
before Phase 3 ships, is the only moment it is free — after review data exists, the same
change would silently lengthen streaks that had already broken.

**Reviews count toward the Daily streak only, never the Path streak.** A card belongs to the
learner (§4.1), and §4.11 lets it outlive its source lesson entirely, so there is not always a
path to credit. The path streak stays what it has always been: work on that path.

**4.10 Scope is chosen at the door, not inside the session.** The review screen shows which
scope it is in and offers no switcher: `Card 4 of 10` is a contract, and changing the
denominator underneath a learner mid-session breaks it. Phase 2 made the same call — scope
switching was deferred with the whole-path Q&A slice — and the two features should not
disagree about what *scope* means. The one exception is the **end of a filtered session**,
where widening is what the learner actually wants, so it is offered there and nowhere else.

**4.11 A card outlives its source; the citation degrades honestly.** When the source lesson
has been revised past recognition, removed by a future Phase 4 edit, or its path deleted, the
card still reviews — its front and back are self-contained — and the source line stops being
a link, reading *"from a lesson you've since changed"*. Nothing is silently deleted and
nothing dangles. (Note this diverges from the streak's treatment of a deleted path, which
*does* erase its days — that is a consequence of the no-new-table design there, not a
principle to copy.)

## 5. Success metrics & workflows

**The one question worth asking:**

> **Does the retention loop move Return?** Compare the existing **Return** metric for
> accounts before and after this ships — the same before/after cohort split
> `streak_return.sql` already established.

This phase exists because Phase 5's streak measures returns the product had not yet earned.
If Return does not move once there is something due tomorrow, the premise is wrong and Phase
4 should be re-argued before it is built.

**Supporting metrics:**

| Metric | Question it answers |
| --- | --- |
| **Keep rate** — kept ÷ drafted | Are the drafts worth keeping? A low rate is an AI-quality problem, not a UI one, and it is the signal §6's evals exist to catch early. |
| **Queue completion** — sessions finished ÷ started | Is 10 the right cap? Consistently abandoned queues say it is too many; consistently exhausted ones with a growing backlog say too few. |
| **Recall rate over rung** — *Got it* ÷ reviews, by ladder rung | Is the ladder's spacing right? Recall collapsing at a rung is the ladder telling you that interval is too long. |

**The Phase 4 seam** (named, not built): Phase 4 needs "what this learner keeps getting
wrong". A lapse is that signal, per card, with its source lesson attached. This PRD commits
only to *lapses being queryable per learner and per source lesson* — no aggregation, no
surface, no API.

**New workflows** (W24 is the next free number; W1–W23 are taken):

- **W24 — Finishing a lesson produces a due card.** Complete a lesson → drafts appear →
  keep two → they are due for review.
- **W25 — The daily queue caps and holds.** With more than ten cards due, the queue is ten;
  reloading returns the same ten in the same order.
- **W26 — A lapse resurfaces without costing a slot.** Grade *Again* → the card returns later
  in the session → the session is still ten distinct cards.
- **W27 — A card survives its source lesson.** Delete the path a kept card came from → the
  card still reviews, with a degraded citation.

## 6. Evals (AI components)

One new artifact kind, `flashcard_draft`, following the `tutor_reply` (AL-250) and
`path_proposal` (AL-350) precedent. A drafted card is judged on:

- **Grounding** — is it answerable from the lesson's Read passage, without inventing facts?
- **Scope** — one fact per card. A card with three clauses is a card nobody grades honestly.
- **Non-triviality** — it must not restate the Quick check's stem, and must not be a
  definitional card so obvious that keeping it is a waste of the learner's tap.
- **Independence** — the back must make sense without the lesson in front of you, since §4.11
  guarantees the card outlives it.

Keep rate (§5) is the production proxy for all four, which is what makes this eval
calibratable against real behavior later rather than judge-only.

## 7. Explicitly out of scope

Manual card authoring · editing a kept card · un-discarding a draft · cards drafted by the
tutor or from a Quick check · ease factors and four-way grading (§4.6 — the phase's own
follow-on) · cloze deletion, images, or any card type beyond front/back · a card-management
or browse-all-cards page · leech detection and handling · "cards mastered" and the rest of
Phase 5's stats view · notifications, email or reminders of any kind · sharing, exporting or
importing decks · burying, suspending or rescheduling a card by hand · counting review toward
the streak (§4.9) · any Phase 4 aggregation over lapse data (§5).

## 8. Open questions

1. ~~**Should a review-only day keep a streak alive (§4.9)?**~~ **Resolved: yes.** §4.9
   carries the reasoning and the amendment to [`CONTEXT.md`](../CONTEXT.md). The "it rewrites
   history" objection turned out to be an argument about *timing*, not about the rule: with no
   review data in existence, widening **Active day** now moves nothing. A separate *review*
   streak was considered and rejected as worse than either — two streaks is two habits.
   What remains open is only the **bar**: one card is enough today (§4.9), where "finished
   the day's queue" would be a more meaningful unit of work and a harder one to game. The
   queue-completion metric in §5 is what would justify raising it.
2. **Is the debt ever visible (§4.8)?** Hiding it is right for the returning learner and
   wrong for the one who wants to know where they stand. A single line in a stats view that
   does not exist yet is the obvious home, which is an argument for deciding it with the rest
   of Phase 5 rather than here.
3. **Is 10 the right number, and is 7/3 the right split?** Both are owner-chosen and neither
   is derived from anything. §5's queue-completion metric is what makes them revisable, and
   the TDD should make them configuration rather than constants so the answer costs a deploy
   and not a migration.
4. **Does drafting need its own model slot?** Phase 1 established *outline* / *lesson* /
   *judge* slots and Phase 2/2B added *tutor* and *shaper*. Drafting cards from a passage is
   a small, structured extraction task — plausibly the cheapest model in the fleet, which
   argues for its own slot rather than borrowing the lesson slot's.
5. **How many cards should Aleph draft per lesson?** The mock draws four and the roadmap says
   "a handful". Too many and the keep step becomes work; too few and the deck never grows.
   Likely a function of the passage's length, which makes it an agent-design question the TDD
   should own.
