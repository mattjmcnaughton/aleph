# PRD — Phase 5, slice 1: Streaks

**Status:** Accepted — **shipped and launched** (the `streaks` flag defaults on) · **Owner:** solo builder · **Roadmap item:** [Phase 5 — Momentum](../roadmap.md#phase-5--momentum)
**Companion to:** [Phase 5 streaks TDD](../tdds/phase-5-streaks.md)
**References:** [`README.md`](../../README.md) · [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) (ubiquitous language) · [Phase 1 PRD](phase-1-path-generation.md) · [`metrics.md`](../metrics.md) · prior art: habagou `domains/streaks.py`, `services/progress.py`

> **This document owns the product boundary only.** It shipped as one combined doc on the
> argument that the slice was too small to split — "the product surface is a number and a chip,
> and the technical design is one query, one pure module, and one endpoint". That held until the
> technical surface turned out to carry four decisions the product boundary should not
> ([TDD](../tdds/phase-5-streaks.md) D3, D9, D10, D11), so it was split as the note itself
> allowed. Everything technical — decisions, schema, queries, API, frontend, instrumentation,
> testing, delivery — lives in the TDD, along with a record of the six places the split corrected
> what the combined text said (TDD §14, two of which changed §3 below).

## 1. Summary

Two streaks, both counted in **days**, both derived from work the learner already does:

- **Daily streak** (global) — consecutive days on which the learner completed **at least one
  lesson**, on any path. This is *the* streak: the one that gets the flame, the celebration, and the
  count on the home screen.
- **Path streak** (per path) — consecutive days on which the learner completed at least one lesson
  **on that path**. A quieter stat shown on the home list, deliberately not celebrated (§4.3).

Nothing else from Phase 5 ships here: no weekly goal ring, no daily-minutes target, no stats page,
no notifications. The README bounds this feature deliberately ("streaks and progress tracking, and
nothing more"), and this slice takes the smallest honest bite of it.

**The core design claim: this needs no new table.** A lesson already records `completed_at`, and a
lesson already belongs to exactly one path owned by exactly one learner. "Days I completed a lesson"
is a `GROUP BY` over rows we have. The whole feature is one migration (an index), one pure domain
module, one repository method, one service, one endpoint, and two small UI surfaces. The TDD's D1
carries the argument and the escape hatch.

## 2. Why now, and what it changes about the plan

Streaks are scheduled for **Phase 5**, and [`CONTEXT.md`](../CONTEXT.md)'s phase-boundary note lists
"Streak / goal ring / daily minutes" as deferred there. Building this now pulls a slice forward, the
same way Phase 2B was pulled forward from Phase 4 by owner decision. That is a legitimate move, but
it is not free: the vocabulary is authoritative, so shipping the word **streak** means the docs say
what the code does on the same day, not later.

Accepting this proposal therefore includes (TDD §16, step 0):

- **[`CONTEXT.md`](../CONTEXT.md)** — add **Daily streak**, **Path streak**, **Active day**, **Best
  streak** to *Progress & structure*; amend the phase-boundary bullet so it reads "goal ring / daily
  minutes — Phase 5; **streaks shipped early, see the streaks PRD**".
- **[`roadmap.md`](../roadmap.md)** — a sentence in Phase 5 recording that the streak slice landed
  ahead of the rest, exactly as Phase 2's paragraph records 2B's pull-forward.
- **[`docs/api.md`](../api.md)** — the new endpoint.
- **[`docs/metrics.md`](../metrics.md)** — the new saved query (§5).

The honest counter-argument: Phase 3 (flashcards) and Phase 4 (adaptive paths) both produce
*reasons to return*, and a streak counts returns that the product does not yet earn. A streak over a
loop with nothing due tomorrow measures willpower, not the product. This slice is cheap enough that
it is worth shipping anyway — but it should be read as instrumentation for the return metric, not as
a retention feature in its own right, and §5 states the metric that would prove it either way.

## 3. What a learner sees

**Home (`/`, "Your paths").** A single line above the list: `🔥 5-day streak · 1 lesson today`. Once
a longer streak has been broken, the line also carries the best — `🔥 5-day streak · best 12 · 1
lesson today` — quietly, as an aim rather than a scoreboard, and never when best and current are the
same number. On a day with nothing completed yet, the trailing clause simply disappears —
`🔥 5-day streak · best 12` — rather than reading `0 lessons today`, which would be the one place
this feature could scold. At zero it reads `Complete a lesson to start a streak` — an invitation,
never a scold. Below it, a 45-day activity grid (the habagou heatmap, one cell per day, weeks as
columns, Nocturne teal at three intensities).

**Each row of the home list.** A small neutral chip when the path streak is ≥ 2 days: `3-day`. No
flame, no colour escalation.

**On completing a lesson.** If the completion is the first of the day, the streak line updates and
briefly says `Day 6 🔥`. It does not block, animate at length, or interrupt navigation to the next
lesson. If the completion is the second of the day, nothing happens — the streak is a day counter,
not a lesson counter.

**Never:** a push, an email, a "you're about to lose your streak" warning, a freeze/repair purchase,
or a leaderboard. Restraint is the feature.

**Not in v1:** a chip on the path view or in the desktop sidebar's switcher. The streak surfaces on
the phone-first home screen and nowhere else (TDD §14, R6).

## 4. Product decisions

**4.1 A day is a calendar day in the learner's local timezone.** [`CONTEXT.md`](../CONTEXT.md)
already defines **Day** this way for metrics; the streak inherits it rather than inventing a second
answer. Mechanics in TDD D3.

**4.2 Completion is the signal — not viewing, not attempting.** A lesson read but not marked
complete does not count, and neither does an Attempt on its own. This matches the existing rule that
"completion, not correctness, is what counts" and keeps the streak to **one** input. Note this
diverges from **Engaged** (attempt *or* complete), which is the immutability boundary and answers a
different question; the two must not be conflated in code or prose.

**4.3 The path streak is a stat, not a game.** With multiple paths a learner naturally alternates —
which is exactly the behavior the **Breadth** metric wants — and a per-path streak breaks every time
they do. Celebrating it would punish the product's own goal. So the path streak is displayed
neutrally, is never the subject of a nudge, and is hidden below 2 days.

**4.4 The current streak does not break at midnight.** A learner who studied yesterday and has not
yet studied today still sees "5-day streak", not "0". The streak breaks when a day passes with no
completion — i.e. it is computed from the run ending **today, or yesterday if today is empty**.
(Ported from habagou's `compute_streaks`; it is the difference between a streak that motivates and
one that shouts at you before breakfast.)

**4.5 The daily target is one lesson.** habagou's streak requires 3 completions a day because its
activities are ~1 minute each; an Aleph lesson is a Read passage plus a Quick check. One is the
right bar, and it means the daily goal *is* the streak — no separate goal concept, no ring.

**4.6 Deleting a path erases its days from the global streak.** This is the real cost of the
no-new-table design (TDD D1), and it is a genuine product wart: delete a path you soured on, lose the
streak you built on it. Accepted for v1 because deletion is rare and the escape hatch is designed
(TDD §15, "if it bites"). **The delete confirm says nothing about it** — the owner's call on open
question 2: advertising a wart nobody may hit costs more attention than it saves, and the behaviour
is pinned by a test so it is a decision rather than a bug. If someone hits it, the ledger upgrade is
the fix, not a warning.

## 5. Success metric & workflows

**The one question worth asking:**

> **Does the streak move return?** Compare the existing **Return** metric (activated learners back on
> a 2nd distinct day) for accounts before and after the flag flip.

No new event is needed to answer it: `lesson_completed` already carries `account_id` and its
timestamp, so streak length is computable in Logfire from data emitted since Phase 1 — **including
for the period before this ships**, which is the useful part, because it is the only way to have a
before-cohort at all. TDD D9 and §9 carry the reasoning and the saved query.

If the streak does not move Return, this slice is decoration and Phase 5's remaining scope should be
re-argued rather than built. `streak_return.sql` alongside `return_rate.sql` makes that answerable
instead of arguable.

**New workflows** (W22 is the next free number; W1–W21 are taken):

- **W22 — Completing a lesson visibly advances the streak.** Complete the day's first lesson → the
  count increments in the same interaction, on a phone viewport.
- **W23 — A streak survives a missed day boundary but breaks on a missed day.** Studied yesterday,
  nothing today → the streak still reads yesterday's length (§4.4). Two days idle → zero.

## 6. Explicitly out of scope

Weekly goal ring · daily-minutes target · a dedicated stats/progress page · streak freezes, repairs
or purchases · notifications or email of any kind · leaderboards or any social surface · milestone
badges (habagou has `next_milestone`; it is a nudge mechanic and this slice does not need one) ·
per-path heatmaps · a path-view or sidebar streak chip (§3) · timezone as an account setting
(TDD D3) · counting Attempts or lesson views as activity (§4.2).

## 7. Open questions

1. ~~**Is the activity strip in v1, or does the streak line ship alone?**~~ **Resolved: in.** It is
   the largest chunk of frontend in the slice, and the owner took it in v1 — the habit is what the
   strip shows and the number only summarises. Its geometry became a design question instead
   (TDD §15: 45 days in a 7×7 grid leaves four pad cells; 49 would fill it).
2. ~~**Should the delete confirm carry the streak warning (§4.6)?**~~ **Resolved: no.** §4.6 records
   the reasoning and the test that keeps the behaviour honest.
3. **Does the path streak earn its place at all?** It was asked for explicitly, and it is nearly free
   given the grouped query — but §4.3 argues it is a stat nobody acts on, and it now has exactly one
   surface. Worth a look after a few weeks of dogfooding; removing it later is a one-component
   deletion.
