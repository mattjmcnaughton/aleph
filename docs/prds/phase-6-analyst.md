# PRD — Phase 6: The analyst

**Status:** Accepted · **Owner:** solo builder · **Roadmap item:** [Phase 6 — The analyst](../roadmap.md#phase-6--the-analyst)
**Companion to:** [Phase 6 TDD](../tdds/phase-6-analyst.md) — this document owns the product boundary only; the TDD's §14 records where the shipped design corrected this one
**References:** [`README.md`](../../README.md) · [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) (ubiquitous language) · [Phase 1 PRD](phase-1-path-generation.md) · [Phase 2B PRD](phase-2b-shape-your-path.md) · [Phase 3 PRD](phase-3-flashcards.md) · [Phase 5 streaks PRD](phase-5-streaks.md) · [Phase 6 TDD](../tdds/phase-6-analyst.md) · [`architecture.md`](../architecture.md) · [`metrics.md`](../metrics.md) · [`evals.md`](../evals.md) · [`deploy.md`](../deploy.md)

> **This document owns the product boundary only.** Schema, the claim protocol, **which
> retrieval provider we use**, how findings are deduplicated against prior Briefs, the API and
> instrumentation belong to the TDD. Where this PRD names a mechanism it is because the product
> rule is unintelligible without it.
>
> **Provider choice is explicitly deferred.** This document says *retrieval* and never names a
> vendor. A neural search API, a general web search API, a search-augmented model through the
> OpenRouter client we already have — all are candidates, all are the TDD's call, and §4.4
> states the only product constraints the choice has to satisfy (the third rule there rules out
> the third candidate).

## 1. Summary

A learner names a topic and Aleph **deploys an analyst on it**. The analyst researches what has
actually happened since it last reported, and publishes a short, cited **Brief** that builds on
every Brief before it — reporting what changed rather than restarting the story.

Where a **path** teaches a body of knowledge that was already settled when you asked, a
**Beat** follows a subject that is still moving. Same learner, same app, same reading surface;
opposite relationship to time.

Three surfaces, all deliberately rhyming with the path surfaces a learner already knows:

- **Deploying a Beat** — topic, level, the day it reports, optional guidance. The path
  onboarding flow with one field added.
- **The Beat view** — the Brief list in a rail, newest first, each row dated. The path view
  with the ordering reversed and the lock semantics dropped.
- **A Brief** — a Markdown reading surface, exactly a lesson's, plus the one thing a lesson
  never has: **Sources**, attributed in the prose and listed at the foot. Appendix A is a
  worked example.

This is a **new pillar, not a deepening of the existing one.** Phases 1–5 make Aleph a tutor
for what is known. This makes it a tutor for what is happening, and it is the first feature
that gives a learner a reason to open Aleph on a day they have no lesson queued and no cards
due — because something new exists that did not exist yesterday.

## 2. Why this, why a sibling, and what it changes about the plan

**Why now.** Phases 1–3 built the reading surface, the tutor rail, the retention loop, and —
most importantly for this phase — the generation machinery that survives a crash with no
learner watching: claim, stale recovery, bounded concurrency
(`services/lifecycle.py`), and **Trigger + poll** as the delivery model. Every one of those
exists for reasons this phase inherits exactly, and §4.2 turns out to need almost nothing new.

**Why a sibling and not "a realtime path".** The tempting framing — and it is genuinely
tempting — is that this is just a path that keeps growing, since Phase 2B already built
**Addition** (insert lessons at or after the first non-engaged position). A scheduled analyst
is nearly describable as *a system-proposed Addition, auto-applied, generated from retrieved
sources*. Four things break that framing, and three of them are load-bearing:

1. **Progression is wrong for a feed.** A path unlocks linearly; the next lesson opens as the
   prior completes. If a learner skips three Briefs they want the newest one, and the old ones
   *decay* rather than block. Reverse-chronological-with-decay and linear-unlock-forward are
   not the same reading model, and **Unlock state** is stated in
   [`CONTEXT.md`](../CONTEXT.md) as *locked → available → complete*.
2. **There is no denominator.** A Beat never ends. `path_completed`, "12 of 40 lessons",
   percent-complete, and **Path streak** all assume a finite ordered set with a last element.
3. **It would corrupt the north star.** **Activated learner** is "> 3 lessons on a single path
   within 7 days". A feature that emits content on its own could raise lesson-shaped counters
   without the learning loop working at all. Polluting the one metric that tells us whether
   Aleph works is the expensive kind of mistake, and it is unrecoverable retroactively.
4. **Continuity means something else.** For lessons it is *don't re-teach 1…N*. For Briefs it
   is *report what changed since 1…N* — which needs prior Briefs' **cited Source URLs and
   claims**, not their prose. Same word, different context assembly (§4.5).

So: **a separate aggregate, a shared spine, and a surface that deliberately looks like a
path.** The model diverges where it must; the UI converges everywhere it can, because a
learner should not have to learn a second app.

**What is shared** (this is where the leverage is, and it is most of the feature):

| Reused verbatim | Why it fits unchanged |
| --- | --- |
| **Trigger + poll** and the generation lifecycle — claim, stale recovery, `TaskRegistry`, the concurrency semaphore, **except the reconciler** (§4.2) | Built for work that must survive a restart and be driven by whoever shows up. §4.2 is that model applied to a clock instead of a position. |
| **Prefetch (+N)**, on the time axis (§4.2) | The same idea — generate ahead of where the learner is, to hide latency — with "ahead" measured in hours rather than lesson positions. **Deferred from the first slice** (§7.1): at current scale the warm moment it exploits does not exist. |
| The Markdown pipeline (`markdown.tsx`) | It is the security boundary for model-written text. A Brief is model-written text, and it must not get its own renderer. |
| **Topic**, **Level**, **Guidance**, **Refused** | A Beat takes the same three generation inputs, frozen the same way, with the same safety branch. No new vocabulary where the old vocabulary is right. |
| The flag playbook (dark → dogfood → flip, kill switch retained) | Fifth flag, same runbook ([`deploy.md`](../deploy.md)). |
| The eval harness — seed set, binary judge, layer-1 predicates | New rubric and one genuinely new constraint (§6), same machinery. |
| The tutor rail's turn lifecycle and SSE framing | Not in this slice (§7), but the seam is unchanged when it lands. |

**What is deliberately not shared:** units, `position_in_path`, **Unlock state**,
**Progression**, path completion, **Path streak**, **Attempt**, **Quick check**.

**Accepting this proposal includes** (the vocabulary is authoritative, so the docs change on
the day the decision is made, not the day the code lands — the step 0 both the streaks and
flashcards slices took):

- **[`CONTEXT.md`](../CONTEXT.md)** — a new *The analyst* section defining **Beat**, **Brief**,
  **Source**, **Cadence**, **Anchor day**, **Brief continuity**, **Brief prefetch** and
  **Skipped**; a phase-boundary bullet marking every one of them unbuilt; and the §4.9 widening
  of **Active day** to count reading a Brief.
- **[`roadmap.md`](../roadmap.md)** — a new **Phase 6 — The analyst** section and status row.
  It is a *new pillar*, so the roadmap's "final core phase" framing for Phase 5 needs one
  sentence of correction rather than a silent contradiction.
- **[`docs/api.md`](../api.md)**, **[`docs/metrics.md`](../metrics.md)**,
  **[`docs/evals.md`](../evals.md)** — the new endpoints, saved queries, and artifact kinds.

**No deployment change is required** — see §4.2. That is a deliberate design outcome, not a
happy accident.

## 3. What a learner sees

**Deploying a Beat.** The path onboarding flow with one field added: a **Topic**
("EU AI regulation", "GLP-1 drugs", "the Rust release train"), a **Level** — the same
*new to it · some experience · I work in it* — an **Anchor day** (*Reports on ▾ Monday*), and
optional **Guidance** ("policy and enforcement, not stock moves"). The primary action says
what it does: `Deploy analyst`.

**Cadence is weekly and is not a choice** in this slice (§4.11); the day is. A learner picks
the day their week starts — Monday for a work subject, Sunday evening for a reading habit —
and that is the whole of the scheduling surface they ever see.

The first Brief is researched **immediately**, not at the first anchor day. A learner who
deploys an analyst and is told to come back Monday has been given a receipt, not a product.

**Home (`/`).** Beats live in a section beside "Your paths", not mixed into it — the same
card grammar (title, a line of state) with a different verb. A Beat card reads
`3 new briefs · weekly`. An unread count is the state that matters, where a path shows
progress.

**A Beat that is working.** When the analyst is researching, its card says so —
`Researching… · started 30s ago` — and the learner is free to go and do something else. This
is the same trigger-and-poll shape as path creation, and it is deliberately not a modal or a
blocking spinner: **the whole rest of the app is available while an analyst works**, which is
what makes §4.2's arrival-triggered model acceptable rather than merely cheap. Read a lesson,
clear your due cards, come back to a finished Brief.

**The Beat view.** This is the path view, and it should be recognisably so. The **Beat rail**
occupies the path rail's position and shape — an ordered list of Briefs, **newest first**, each
row carrying its own date (`Brief #7 · 3 Aug`). Read/unread replaces locked/available/complete;
nothing is ever locked.

**Flat, not grouped.** A path rail groups lessons under **units**, which are semantic groupings
the outline agent chose. A Beat has no units, and the nearest visual analog — month subheadings
— would group nothing (§7.1): at weekly cadence the first month's rail is a single *August*
header over the whole list. The resemblance to the path rail comes from the rail's shape and
position, not from subheadings, and a calendar month is a weaker analog to a unit than it first
looks — a unit means something, a month is where the weeks happened to fall. At the head of the rail, the analyst's
standing orders in one line: `Weekly · EU AI regulation · policy and enforcement`.

**A Brief.** The lesson reading surface, near-identically: a title, a date, a Markdown body
that renders through the same component and may draw a ```mermaid diagram where one earns its
place. Two things a lesson does not have:

- **A Sources list** at the foot: publisher, title, date, link. This is the part a learner
  should be able to check us on, so it is a first-class region of the page, not a footnote
  block in small grey type.
- **Attribution in the prose** — *"Northlake published"*, *"the agency's own July update
  reports"* — so a reader can tell which sentences are sourced and which are the analyst's
  read. This is what carries §4.4's provenance rule; **numbered inline markers are the
  upgrade, deferred to a later slice** (§7.1). Appendix A shows what the prose form has to
  achieve to stand in for them.

Above the body, one line of continuity: `Builds on Brief #4 (Mon 27 Jul)` — a link. This is the
product claim of the whole feature made visible.

**Appendix A is a worked example of all of this** — a Brief on *AI in healthcare*, annotated
with why it is good, and set beside what the same week looks like when it goes wrong.

**A quiet period.** Sometimes the honest answer is that nothing material happened. The Beat
rail shows that as a **Skipped** entry — dated, one line, *"Nothing material since Brief #4 —
the Commission's consultation is still open, closing 11 Sept"* — not a hole in the list, and
not a padded Brief. It is a first-class outcome (§4.6), the way **Refused** is a first-class
path status.

**Never:** a push notification, an email, a "you're falling behind" banner, a badge on the app
icon, an infinite scroll, engagement-optimised headlines, or a Brief whose confidence exceeds
its sources. This phase inherits Phase 5's restraint rule verbatim, and needs it more than any
phase so far — a feed is the easiest surface in the product to make compulsive, and Aleph is
not that.

## 4. Product decisions

**4.1 A Beat is a standing assignment; a Brief is a dated, immutable record of it.** The Beat
holds the orders (Topic, Level, Anchor day, Guidance) and the state (when it is next
claimable). Each Brief is a published artifact with a publication date and a Source list, and it is
**immutable, full stop** — not "immutable once engaged" as a lesson is. A lesson can be revised
because it teaches something timeless and the learner has not reached it yet; a Brief is a
claim about the world on a date, and rewriting it retroactively would make **Brief continuity**
a lie. If a Brief gets something wrong, the *next* Brief corrects it in the open. That is what
an analyst does.

**The publication date is part of that immutability, not a view onto it.** A Brief is stamped
with its date the moment it is written, from the local day the run that produced it was claimed
on (§4.2) — it is never recomputed later from whoever happens to be reading. `Brief #5 — Monday
3 August 2026` is content, the same way the body is; deriving it fresh per request the way a
streak derives "today" would let a learner's travel move a published document's date, and the
cadence floor (§4.2) with it. The accepted consequence: a learner who deploys in London and
reads in Tokyo sees each Brief dated where they were when it published, not where they are now.

**A Brief's period is "since the last Brief", never a calendar slot.** Brief #7 covers
everything since Brief #6, whenever that was. This falls out of §4.2 and is better than the
calendar alternative in three ways: a learner returning after a month triggers **one**
generation rather than four, a backlog of unwritten Briefs can never accumulate, and it is more
faithful to §4.5 — the delta is naturally measured from the last report, not from Monday.

**4.2 Whoever shows up drives the work; a late Brief is a late Brief, never a missing one.**
**Cadence is a floor on frequency, not a calendar appointment**: *weekly* means "at most one
Brief a week", and the promise to the learner is that a Beat keeps up, not that it fires at
07:00. A Beat becomes **claimable** on its **Anchor day** (§4.11), in the learner's local time,
and the claim is driven by two triggers:

1. **Arrival.** A request from a learner with a claimable Beat kicks the drain, exactly as
   reaching a lesson kicks its generation today. This is Phase 1's **Trigger + poll**, verbatim,
   and at current scale it is the sole trigger that fires — see below for why the reconciler
   does not join it.
2. **Brief prefetch** — **specified, and deferred from the first slice** (§7.1). A Beat becomes
   claimable a little *before* its Anchor day opens, so a warm moment on Sunday evening produces
   Monday's Brief and it is genuinely waiting. This is **Prefetch (+N)** on the time axis: the
   same trick, hiding the same latency, for the same reason. Early by hours is a feature; the
   Brief's period is *since the last Brief* (§4.1), so nothing about it depends on the exact
   moment it was written. It waits because the warm moment it exploits does not exist at current
   scale — which means **the first slice runs on arrival alone**.

**The learner's local time comes from the arrival, so nothing has to be stored.** "Is it Monday
for this learner" is only ever asked at the moment a request arrives, and a request already
carries `tz_offset_minutes` — the same value `services/progress_read.py` uses to decide what
"today" means for a streak. Aleph therefore needs no stored timezone and no stored delivery
time (§7) to honour an Anchor day, which is a second thing arrival-triggering buys and a
scheduler would have had to pay for.

**The reconciler plays no part in this.** It already ticks every `RECONCILER_INTERVAL` for
paths and lessons, whenever the process is alive for any reason, but it has no request and no
`tz_offset_minutes` to ask "is it Monday for this learner" with. Evaluating an Anchor day off
the reconciler would need either a stored timezone (which §7 excludes) or a conservative "wait
until the Anchor day has opened everywhere" lag, and at current scale it would be a trigger that
barely fires — so the reconciler does not scan Beats at all in this slice. **Arrival is
therefore the sole trigger that evaluates an Anchor day.** The learner-visible consequence is
worth stating plainly: **a Brief appears the first time you open the app on or after your
day**, and never before you show up. This becomes a real decision only on a move to always-on,
where the reconciler is worth teaching to scan Beats as the primary trigger and a stored
timezone is wanted anyway.

**Nothing is ever silently skipped for infrastructural reasons.** **Skipped** (§4.6) means *the
analyst found nothing*, and it must never become a laundry slot for *we failed to run*. Those
are different facts and the learner is owed the difference.

**Why this and not a scheduler.** The obvious design is a cron job, and it would require the
app to stop sleeping (`fly.toml` runs `auto_stop_machines = 'stop'` with
`min_machines_running = 0`, and the reconciler is an in-process loop, so on a stopped machine
no timer fires at all) — or an external trigger, which adds either a second deployment artifact
or a public endpoint plus a shared secret. Arrival-triggering needs none of that, and it
dissolves the cost problem in §4.8 rather than mitigating it.

**The honest cost, accepted knowingly.** At current scale the machine is asleep most of the
time, so the prefetch window will rarely catch a warm moment and most Briefs will be researched
while the learner waits. §3's rule — the rest of the app stays usable — is what makes that
acceptable. It also improves on its own: the more the product is used, the warmer the process,
the more often a Brief is finished before anyone asks for it.

**And it does not foreclose always-on.** Setting `min_machines_running = 1` is close to a
one-line config change — paired with teaching the reconciler the same claimable-Beats scan
arrival already runs, which the reconciler does not carry in this slice (§4.2) — and Briefs
start being ready on schedule instead of on arrival. The decision is reversible in both
directions for close to the price of a deploy, which is the main reason to start here.

**4.3 A Beat has a Level and Guidance, for the same reason a path does.** "What has happened in
EU AI regulation" is a different document for a policy lawyer and for someone who just heard
the phrase. Reusing **Level** verbatim — rather than inventing a "depth" — is the cheapest
correct answer and keeps one word for one idea.

**4.4 Every claim about the world is traceable to a Source.** This is the phase's central
quality rule and the one that constrains the TDD's provider choice. The product requirements on
retrieval are: **real URLs that resolve**, a **publication date** we can show and reason about,
and enough of the retrieved text to ground a quote. Which vendor satisfies that is a TDD
question with real cost and quality tradeoffs, and this document takes no position beyond those
three requirements.

Three things it *does* take a position on, because they are product rules and not implementation:

- **The analyst never cites what it did not read.** A URL that was retrieved but whose content
  never entered the model's context is not a Source. A citation is a claim of provenance.
- **A Brief with no Sources is not publishable.** If retrieval failed, that is a failure state
  (retryable, visible), never an uncited essay from model priors. The whole difference between
  this phase and asking a chatbot what's new is that difference.
- **Retrieval itself is deterministic — a plan, not a tool a model reaches for.** The queries are
  derived from the Beat's frozen standing orders (Topic, Guidance) and the period since the last
  entry; no model in the pipeline calls a search tool of its own to produce them, whichever
  vendor ends up executing the plan. This is what keeps the two rules above enforceable rather
  than aspirational — the researcher's inputs are exactly what retrieval returned, with no tool
  call in between whose arguments could drift from what was actually read — and the
  **research/write split** holds independently, for the same reason: read, then write, never
  one pass doing both.

**4.5 Brief continuity: report the delta, not the topic.** Brief *N* is generated with awareness
of Briefs *1…N-1* — their claims and, critically, **their cited Source URLs** — and its job is
what has changed. Where lesson continuity prevents *re-teaching*, Brief continuity prevents
*re-reporting*, and it needs different material to do it: prior Source URLs are what make "we
already covered this" a mechanical check rather than a stylistic hope.

The learner-visible form of this is the `Builds on Brief #4` line (§3). The product claim is
that Brief #7 is worth reading *because* you read #1–#6, and if that is not true this feature
is an RSS reader with extra steps.

**4.6 Nothing to report is a first-class outcome.** The failure mode that kills this feature is
not a broken trigger; it is **Brief #7 confidently restating Brief #6 in new words**. A weekly
analyst on almost any subject will hit stretches where nothing material happened, and a model
asked to produce a brief will always produce one. So: if no finding survives the novelty check
against prior Briefs, the analyst publishes **Skipped** — dated, one honest line, no filler —
and the cadence floor resets so the next arrival does not immediately re-research the same
empty period.

This is the rule most likely to be argued away later under pressure to look busy, so it is
stated as a decision and not a heuristic: **a Skipped period is the feature working
correctly.** A learner who reads three padded Briefs stops opening the fourth, and we will
never see that in a metric until the Beat is already dead.

**A Skipped entry carries no number of its own.** §3's own example already assumes this — "Nothing
material since Brief #4" names the last *Brief*, not a Skipped period before or after it. Only
published Briefs are numbered, and a Skipped period sits in the rail dated but unnumbered between
them. Numbering it would imply a Skipped period is a kind of Brief; it is the opposite, an honest
record that no Brief exists for that stretch.

**4.7 Multiple Beats, with a cap.** A learner may deploy several — this mirrors paths, where
multiple-from-day-one was a deliberate Phase 1 call, and it is what makes the Beat section on
home worth building. The cap exists because §4.4's research step is the most expensive
generation in the product per unit of output, not because Beats accumulate cost while idle
(§4.8). It is configuration, not a constant.

**4.8 A Beat's cost is bounded by attention, and that is structural.** An earlier draft of this
document proposed auto-hibernating an unread Beat, because a scheduled analyst spends money
every period *whether or not anyone ever reads it* — an economic shape this codebase has never
had, where every other generation is paid for by a learner action immediately preceding it.

§4.2 removes the problem instead of managing it. Under arrival-triggering **a Beat nobody opens
costs nothing**, because nobody triggered it; a learner who deploys three analysts and churns
leaves three rows in a table, not three recurring bills. Cost tracks attention automatically,
with no hibernation rule to tune, no "are you still there?" prompt, and no unread threshold to
argue about.

This is the strongest argument for §4.2 beyond the hosting bill, and it is worth stating as a
constraint on any future move to always-on: **flipping `min_machines_running = 1` re-introduces
this problem**, and hibernation is the thing that would have to come back with it. That is a
cost of the flip, and it belongs in the decision when it is made.

**4.9 Reading a Brief is an Active day; a Brief arriving is not.** A Brief that gets generated
is not learner effort — a streak that advanced because a background task ran would make the
streak a measure of our uptime. A Brief the learner *reads* is effort, of the same kind as
reviewing a card, so it keeps a streak alive.

This widens **Active day** a second time (Phase 3 widened it once, for reviews), and
[`CONTEXT.md`](../CONTEXT.md) is amended to say so as part of accepting this document (§2). The
timing argument is Phase 3's, verbatim and still valid: **no Brief exists yet**, so adding the
third signal adds no past Active day and moves no live streak. Deciding it now is the only
moment it is free — after Briefs exist, the same change would silently lengthen streaks that
had already broken.

**Reads count toward the Daily streak only, never the Path streak** — a Beat is not a path, and
there is no path to credit.

**Activation stays lesson-based regardless.** Whatever the streak counts, **Activated learner**
keeps its current definition and this phase adds nothing to it. That is §2's point 3 as a
standing rule.

**4.10 A Beat is not a path, and Aleph never blurs them for the learner.** No Brief appears in a
path's rail; no lesson appears in a Beat's. The convergence this feature is *for* (§5) is built
later and built explicitly — as offers a learner accepts, never as a merged list. The moment
"your stuff" becomes one undifferentiated feed, the learner loses the ability to tell what they
are choosing to do.

**4.11 Weekly only, but the learner picks the day.** The first slice ships **one cadence**:
weekly. Daily is deferred, not dropped.

Weekly is the cadence on which §4.6's novelty gate is under the least strain — more happens
between Briefs, so "is there anything new?" has an easy answer more often — and it is the one
that lets the gate be calibrated before it is trusted on the hard case. It is also 7× cheaper
per learner, which matters for the most expensive generation in the product per unit of output
(§4.7). Shipping both would mean tuning the gate against daily's much thinner deltas at the
same time as proving the whole pillar.

**The Anchor day is the learner's, and it is the only scheduling control in the product.** A
weekly report is a habit, and a habit has a day: Monday morning for a work subject, Sunday
evening for something read over coffee. Picking it costs one dropdown at deployment and makes
the Beat fit a week the learner already has, rather than one Aleph imposes.

**A day, never a time.** The Anchor day is a *date* in the learner's local timezone, and
§4.2's trigger model is why a time would be a lie: without a scheduler there is nothing that
could honour 07:00, and offering the control would promise precision the design deliberately
does not buy. A day is a promise arrival-triggering can actually keep — the Brief is there the
first time you look on Monday — and it is the honest granularity to expose.

Changing the day means deleting and redeploying the Beat, as §7 has it for every other standing
order. That is a real rough edge and §8 Q5 records it as the first thing to revisit.

## 5. Success metrics & workflows

**The one question worth asking:**

> **Does a Brief bring a learner back on a day nothing else would have?** Among learners with
> at least one Beat, the share of **Active days** whose first action is opening a Brief — and
> whether those learners' overall Return exceeds their own pre-Beat baseline.

This phase's entire premise is that a moving subject creates a return reason that a static path
cannot. If Briefs are read only on days the learner was coming anyway, the premise is wrong —
the feature is a nice reading surface with a research bill attached, and it should be cut rather
than deepened.

**Supporting metrics:**

| Metric | Question it answers |
| --- | --- |
| **Brief read rate** — Briefs opened ÷ Briefs published | The blunt one. Below some floor, nothing else matters. |
| **Depth of read** — share of opened Briefs where the learner reaches the Sources | Is it being read, or glanced at? A feature about provenance should be able to show that provenance gets used, which needs a second signal distinct from opening: whether the Sources block was seen at all. |
| **Skip rate** — Skipped ÷ research runs, per Beat | Calibrates §4.6 in both directions. Near zero means the novelty gate is not gating and we are shipping filler; consistently high on one Beat means weekly is faster than that subject moves (§8 Q7), and high across all of them means the gate is too strict. It is also the number that has to look healthy before a daily cadence is trusted (§4.11). |
| **Wait tolerance** (guardrail, §4.2) — share of researching Beats the learner is still present for when the Brief lands, and what they did in between | The direct measurement of the tradeoff §4.2 accepts, and the **first** metric to read: with Brief prefetch deferred (§7.1) the first slice waits every time, so this is measured at its worst case rather than an average. If learners consistently leave and never come back to the finished Brief, the fix is prefetch first and always-on second. |
| **Beat survival** — Beats with a read Brief in the last 30 days | The honest verdict on whether an analyst is a thing people keep. |
| **Cost per read Brief** (guardrail) | The number that decides whether this is viable at all. §4.8 makes it structurally bounded; this confirms it. |

**The convergence seam** (named, not built — this is what §2's "sibling" buys, in the order I
would build it):

1. **Flashcards from a Brief.** Nearly free: the drafting agent takes a passage, and a card
   already keeps its source only as a citation (Phase 3 §4.1, §4.11), so it survives its Brief.
   The cheapest possible proof that the two pillars belong in one app.
2. **The tutor on a Brief.** Brief scope; the entire streaming lifecycle is unchanged.
3. **Brief → path.** *"I keep reading about this — teach me the fundamentals."* Spawn a path
   with the Beat's Topic as **Topic** and the Brief as **Guidance**. One-way, cheap, and the
   most natural sentence a learner will say to this product.
4. **Path → Beat.** Finish a path, offer an analyst on the same subject to stay current. The
   retention story for a completed path, which today has none.
5. **A Brief informs shaping.** Something a Brief surfaced is missing from your path → a
   system-proposed **Addition**. This is genuine fusion and it lands *after* Phase 4, on Phase
   4's machinery.

**New workflows** (W29 is the next free number; W1–W28 are taken — AL-410 took W28 while this
document was in review):

- **W29 — Deploy an analyst, get a cited Brief.** Create a Beat → the first Brief is researched
  immediately → it renders with resolving Sources.
- **W30 — The second Brief builds on the first.** A Beat with a prior Brief produces one that
  cites the earlier Brief and does not re-report its claims.
- **W31 — A quiet period is Skipped, not padded.** With no novel findings, the run publishes a
  Skipped entry and no Brief body, and does not immediately re-research.
- **W32 — A long absence produces one Brief, not a backlog.** A Beat left claimable for several
  Anchor days generates a single Brief covering everything since the last one (§4.1).
- **W33 — Retrieval failure is recoverable and never uncited.** With retrieval unavailable, the
  run fails visibly and retries; no Brief is ever published without Sources.

## 6. Evals (AI components)

Two new artifact kinds, following the `flashcard_draft` (Phase 3) precedent — and **one new
constraint the harness has never faced.**

**`brief_findings`** (the research step) is judged on:

- **Provenance** — every finding carries a Source that was actually retrieved and read.
- **Recency** — findings fall inside the period since the prior Brief, and are dated correctly.
- **Novelty** — nothing restates a claim already made in a prior Brief of the same Beat.

**`brief`** (the written artifact) is judged on — with **Appendix A as the concrete rubric**,
the artifact to argue with when these dimensions are turned into judge prompts:

- **Grounded** — every claim about the world traces to a cited Source, and none exceeds what
  that Source supports. This is [`CONTEXT.md`](../CONTEXT.md)'s existing **Grounded**, pointed
  at a Source instead of a Read passage.
- **Delta** — it reports change against prior Briefs rather than re-establishing the subject.
- **Level-appropriate** and **Safe** — inherited from the existing rubric unchanged.

**The new constraint: live retrieval makes an eval non-deterministic by construction.** Every
prior eval in this repo regenerates from a frozen prompt; this one would regenerate from *the
internet on the day it ran*, which measures the news rather than the agent. The seed set
therefore has to pin **recorded retrieval fixtures** — topic × level × a frozen set of returned
Sources — and the TDD owns how. This is a real cost and it is worth naming now: it is the single
biggest thing this phase adds to the eval harness, and the phase should not ship without it,
because §4.6's novelty gate is precisely the kind of rule that stops working silently.

The same fixtures are what let `scripts/e2e_backend.py` boot this phase at all — a deterministic
stub retrieval adapter beside `services/stub_model.py` is a hard requirement, not a convenience.

## 7. Explicitly out of scope

Email, push, or any delivery channel that is not opening the app · a Quick check on a Brief ·
flashcards drafted from a Brief (§5 — the first convergence, and deliberately not in the first
slice) · the tutor rail on a Brief · Brief → path and path → Beat offers · shaping or editing a
Beat's standing orders after deployment, the **Anchor day** included (delete and redeploy, as
Phase 1 does for paths — §4.11, §8 Q5) · learner-supplied sources, RSS feeds, or private or
paywalled documents · **a daily cadence, or any cadence choice at all** (§4.11 — weekly is the
only one this slice ships) · a delivery *time* of day, and any stored per-learner timezone
(§4.2 — the claiming arrival carries it; §4.1 for why the date is then frozen) · a public or
shareable Brief · exporting a Beat · comments,
highlights or annotations on a Brief · any aggregation of Brief content across learners ·
counting a read Brief toward **Activated learner** (§4.9, a standing rule rather than a slice
boundary) · always-on hosting and the hibernation rule it would require (§4.2, §4.8).

### 7.1 The MVP boundary — in the phase, but not the first slice

Everything above is out of the *phase*. These are in it, and deliberately not in the **first
slice**. Recorded here with the reasoning so the TDD does not rediscover the argument, and so
adding one back later is a decision rather than a drift.

| Deferred | Why it waits |
| --- | --- |
| **Brief prefetch** — the second trigger (§4.2) | Speculative optimization for traffic that does not exist. With the machine asleep most of the time the warm-moment window essentially never fires, so cutting it leaves arrival as the only trigger there is today anyway. Costs nothing to cut, costs nothing to add back. |
| **Inline citations** (§3) | Numbered markers mean the agent emits them, the renderer resolves them, and something validates that every marker points at a real Source — and that means extending `markdown.tsx`, the security boundary for model-written text and the last place to churn early. **The Sources list is not deferred**: §4.4's provenance rule holds in full, carried by prose attribution in the body plus the Sources block at the foot (Appendix A shows exactly this). Inline markers are the upgrade once Briefs are worth checking line by line. |
| **The streak union** (§4.9) | The vocabulary amendment already landed and is the part that had to happen now; the wiring can wait. Read-tracking itself is **not** deferred — §5's north-star metric needs it, and it is two columns plus an event carrying a `marker` discriminator. This defers only feeding Brief reads into **Active day**, exactly the split [`CONTEXT.md`](../CONTEXT.md) already describes when it says nothing reads the third signal yet. |
| **The `brief_findings` eval kind** (§6) | Ship `brief` alone. Novelty against prior Briefs is mostly a *deterministic* check — Source-URL overlap plus claim dedup — which belongs as a layer-1 predicate rather than judge spend. **Recorded retrieval fixtures are not deferred**: without them the phase cannot be evaluated *or* booted by `scripts/e2e_backend.py`. |
| **Period grouping in the Beat rail** (§3) | Month subheadings would group nothing for the entire window in which the phase is being judged — at weekly cadence, month one is a single *August* header over one to four Briefs, and grouping starts earning its keep somewhere past ten. A flat dated list carries the same information, and there is an obvious trigger to revisit: when a Beat's rail no longer fits on one screen. |
| **W30, W32, W33** (§5) | W29 (a cited Brief) and W31 (Skipped, not padded) carry the phase's two load-bearing claims as browser journeys. W30 needs two Briefs to set up, and W32/W33 are edge cases that test better as integration cases than as Playwright runs. |

**Considered and kept.** *Multiple Beats* (§4.7) stays in the first slice by owner decision —
the cap is what bounds it. The *research/write split* (§4.4) stays: one agent that reads
retrieved documents and writes the Brief in a single pass is simpler, but it makes "never cite
what you did not read" very hard to enforce, and that rule is the whole difference between this
phase and asking a chatbot what is new.

**Considered and rejected as a smaller MVP.** Dropping cadence entirely — one Beat, a manual
`Research now` button, no triggers — tests whether a cited, continuous Brief is worth reading
without any scheduling machinery. It saves very little, because arrival-triggering is a derived
due date plus one claim in a protocol that already exists, and it costs the whole of
§5's north-star question: whether a Brief brings a learner back on a day nothing else would
have. That question is the reason to build a pillar rather than a report generator.

**Not cuttable at any size.** **Brief continuity** (§4.5) and **Skipped** (§4.6). Making each
Brief independent would remove more work than everything in the table combined and would leave
an RSS reader with extra steps — §4.5's own words. Skipped will read as scope ("just always
publish something"); it is the rule that keeps Brief #7 from quietly killing the Beat.

## 8. Open questions

Questions 1–4 are **resolved by owner decision**; the reasoning is kept because the decisions
are load-bearing and the alternatives will be re-proposed by someone eventually — possibly by me.

1. ~~**Is "Beat / Brief" the vocabulary?**~~ **Resolved: yes.** *Beat* is journalism's word for
   a standing assignment and collides with nothing in [`CONTEXT.md`](../CONTEXT.md); *Brief* is
   the dated artifact. *Watch/Dispatch*, *Desk/Issue* and *Feed/Update* were the alternatives.
   "Newsletter" is the genre rather than the unit and is now absent from this document entirely
   — the product says **Beat** and **Brief**.
2. ~~**What replaces `min_machines_running = 0`?**~~ **Resolved: nothing.** §4.2 replaces the
   scheduler with arrival-triggering, plus a time-axis prefetch specified for a later slice,
   which needs no deployment change, no external cron, and no second deployment artifact. The
   accepted cost is that most Briefs are researched while the learner waits at current scale;
   §3's "the rest of the app stays usable" is what makes that acceptable, and the **Wait
   tolerance** guardrail in §5 is what would falsify it. The always-on option is deliberately
   left close to one config line away (§4.2 — plus the reconciler scan this slice does not carry)
   — with §4.8's hibernation as its price.
3. ~~**Does reading a Brief keep a streak alive?**~~ **Resolved: yes** (§4.9). Third signal into
   **Active day**, Daily streak only, decided now because no Brief exists yet and this is the
   only moment the amendment is free.
4. ~~**Is in-app-only honest for a newsletter?**~~ **Resolved: the question dissolves.** The
   product is not a newsletter and no longer describes itself as one. A Beat is a standing
   assignment you check on; email, push and every other delivery channel stay out of scope (§7).
5. ~~**Daily, weekly, or both in the first slice?**~~ **Resolved: weekly only, with a
   learner-picked Anchor day** (§4.11). Weekly puts the least strain on §4.6's novelty gate and
   costs 7× less per learner, so the gate gets calibrated on the easy case before daily — where
   "an analyst working for you" genuinely feels more alive — is trusted to it. The day is
   exposed because a weekly report is a habit and a habit has a day; the *time* is not, because
   §4.2's trigger model could not honour one and offering it would promise precision the design
   does not buy.
   **What this leaves open:** changing the Anchor day currently means deleting and redeploying
   the Beat, which is the right Phase-1-consistent answer and a bad experience for the one
   setting a learner is most likely to want to change after living with it. It is the first
   candidate for a follow-on, ahead of daily cadence.
6. **Does a Beat inherit the admin model picker, and how many slots?** §4.4's split — research
   (a mechanical read of retrieved documents, huge-input, expensive) versus writing
   (quality-sensitive, short) — argues for **two** slots on the `outline`/`lesson` precedent
   rather than one. The TDD owns the split; the question here is only whether the per-request
   picker reaches them, which has a production-guard consequence (`MODEL_SLOTS` in `config.py`).
7. **How does a Beat handle a Topic that was a bad idea?** §4.8 means a dead Beat is no longer
   expensive, which removes the urgency but not the question: is there a case for the analyst
   itself reporting *"this subject does not move enough to be worth a weekly beat — try monthly,
   or fold it into a path"*? That is a genuinely new behaviour with no analogue in the product,
   and it may be the most honest thing an analyst can say. A high **Skip rate** (§5) on one Beat
   is exactly the signal that would trigger it.

---

## Appendix A — A worked example

The rubric in §6 says what a good Brief is; this is what one *looks like*. It is the concrete
form of §4.4 (every claim traces to a Source), §4.5 (report the delta), §4.3 (written at a
Level) and §4.11 (weekly, on the learner's day), and it is deliberately the artifact to argue
with when the TDD writes the analyst prompt.

> **Illustrative only. Every fact, publication and date below is invented, and the URLs are
> deliberately `example.com`, so nothing here can be mistaken for a real citation.** The point
> is the *shape* of a Brief, not its contents.

**Beat:** AI in healthcare · **Level:** some experience · **Anchor day:** Monday ·
**Guidance:** *"Clinical deployment and regulation. Not funding rounds."*

---

### Brief #5 — Monday 3 August 2026

*Builds on [Brief #4](#) (Mon 27 Jul)*

**The ambient-documentation backlash arrived, and it is about liability, not accuracy.**
Northlake Health published a post-deployment review of 14 months of AI scribe use across 900
clinicians. The accuracy findings were unremarkable — broadly in line with the vendor studies
covered in Brief #2. What is new is the governance finding: in 3% of encounters the note
contained a clinically material statement the clinician had not said and did not catch before
signing. Northlake's recommendation is not to withdraw the tool but to change who is
accountable for the signature. Expect that framing — *the error rate is fine, the sign-off
model is not* — to be the shape of the next year's argument.

**The FDA's PCCP pathway got its first real test, and it was slower than advertised.** The
agency's own Q2 update reports a median 71 days for predetermined change control plan
amendments, against the "weeks not months" the 2024 guidance implied. For anyone building
adaptive models this is the number that matters: it sets how often a deployed model can
actually be updated, and it is roughly a quarterly cadence rather than a monthly one.

**Still open from Brief #4:** the EU AI Act's high-risk classification consultation for
clinical decision support has not moved. It closes 11 September. Nothing has been published
since I flagged it, and there is no signal yet on which way the Commission is leaning.

**What I could not establish.** Two outlets reported that a large US payer is piloting
automated prior-authorisation review, but neither named the payer and there is no primary
source. I am not treating it as fact. If it is real, it is a bigger story than either item
above, and I will chase it for Brief #6.

**Sources**

- Northlake Health System — *Ambient Documentation: 14-Month Post-Deployment Review* — 30 Jul 2026 — `https://example.com/northlake-review`
- US Food and Drug Administration — *Digital Health PCCP Amendments: Q2 Processing Times* — 1 Aug 2026 — `https://example.com/fda-pccp-q2`
- European Commission — *Consultation: High-Risk Classification for Clinical Decision Support* — 14 Jul 2026 — `https://example.com/ec-consultation`

(Unnumbered on purpose: numbers would imply the inline markers §7.1 defers.)

---

### Why this is a good Brief

- **It opens on the delta, not the topic.** No paragraph explains what ambient documentation
  is. Brief #2 did that; re-establishing it would be re-reporting (§4.5).
- **Every claim about the world is attributed in the prose** and resolves in the Sources list.
  With inline markers deferred (§7.1), *this* is the provenance mechanism — "Northlake
  published", "the agency's own Q2 update reports" — and it has to be strong enough on its
  own that a reader can tell which sentences are sourced and which are the analyst's read.
- **It separates fact from interpretation.** "Expect that framing…" is visibly the analyst
  talking, not something a Source said. A Brief that blurs those two is ungrounded even when
  every fact in it is true.
- **It carries continuity forward explicitly.** The EU item is the *absence* of change, which
  is real information for someone tracking it and is only available to an analyst that read
  Brief #4.
- **It says what it does not know.** The prior-authorisation rumour is reported as a rumour
  with the reason it is not being treated as fact. §4.4's rule is that a Brief never exceeds
  what its Sources support; naming the gap is how that looks in practice.
- **It is level-appropriate.** "PCCP", "high-risk classification" and "prior authorisation" go
  unglossed at *some experience*. At *new to it* the same findings need a clause of context
  each; at *I work in it* the FDA item would lead with the number and drop the framing.
- **It is short.** Three developments and an honest gap. A Brief is read on a phone.

### What the same week looks like when it goes wrong

> *AI continues to transform healthcare at a rapid pace. Ambient documentation tools are seeing
> widespread adoption across health systems, with studies showing strong accuracy. Regulators
> including the FDA and the European Commission continue to develop frameworks for adaptive
> algorithms, and prior authorisation is emerging as a promising application area…*

Every sentence is defensible and the whole thing is worthless. It re-establishes the subject
instead of reporting change, it has no Sources because nothing in it came from one, it launders
an unverified rumour into "an emerging application area", and it would read identically if
written the week before or the week after. **This is what §4.6 exists to prevent**, and the
correct output for a week with nothing in it is a one-line **Skipped** entry, not this.
