# PRD — Phase 6: The analyst

**Status:** Proposal (not accepted) · **Owner:** solo builder · **Roadmap item:** [Phase 6 — The analyst](../roadmap.md#phase-6--the-analyst)
**Companion to:** a Phase 6 TDD that does not exist yet — this document owns the product boundary only
**References:** [`README.md`](../../README.md) · [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) (ubiquitous language) · [Phase 1 PRD](phase-1-path-generation.md) · [Phase 2B PRD](phase-2b-shape-your-path.md) · [Phase 3 PRD](phase-3-flashcards.md) · [Phase 5 streaks PRD](phase-5-streaks.md) · [`architecture.md`](../architecture.md) · [`metrics.md`](../metrics.md) · [`evals.md`](../evals.md) · [`deploy.md`](../deploy.md)

> **This document owns the product boundary only.** Schema, the claim protocol, **which
> retrieval provider we use**, how findings are deduplicated against prior Briefs, the API and
> instrumentation belong to the TDD. Where this PRD names a mechanism it is because the product
> rule is unintelligible without it.
>
> **Provider choice is explicitly deferred.** This document says *retrieval* and never names a
> vendor. A neural search API, a general web search API, a search-augmented model through the
> OpenRouter client we already have — all are candidates, all are the TDD's call, and §4.4
> states the only product constraints the choice has to satisfy.

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
- **The Beat view** — the Brief list in a rail, newest first, grouped by period. The path view
  with the ordering reversed and the lock semantics dropped.
- **A Brief** — a Markdown reading surface, exactly a lesson's, plus the one thing a lesson
  never has: **Sources**, cited inline and listed at the foot.

This is a **new pillar, not a deepening of the existing one.** Phases 1–5 make Aleph a tutor
for what is known. This makes it a tutor for what is happening, and it is the first feature
that gives a learner a reason to open Aleph on a day they have no lesson queued and no cards
due — because something new exists that did not exist yesterday.

## 2. Why this, why a sibling, and what it changes about the plan

**Why now.** Phases 1–3 built the reading surface, the tutor rail, the retention loop, and —
most importantly for this phase — the generation machinery that survives a crash with no
learner watching: claim, stale recovery, the reconciler, bounded concurrency
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
| **Trigger + poll** and the generation lifecycle — claim, stale recovery, reconciler, `TaskRegistry`, the concurrency semaphore | Built for work that must survive a restart and be driven by whoever shows up. §4.2 is that model applied to a clock instead of a position. |
| **Prefetch (+N)**, on the time axis (§4.2) | The same idea — generate ahead of where the learner is, to hide latency — with "ahead" measured in hours rather than lesson positions. |
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
occupies the path rail's position and shape — an ordered list of Briefs, **newest first**,
grouped by period (*August · July · June*) where a path groups by unit. Read/unread replaces
locked/available/complete; nothing is ever locked. At the head of the rail, the analyst's
standing orders in one line: `Weekly · EU AI regulation · policy and enforcement`.

**A Brief.** The lesson reading surface, near-identically: a title, a date, a Markdown body
that renders through the same component and may draw a ```mermaid diagram where one earns its
place. Two things a lesson does not have:

- **Inline citations.** Claims that came from a Source carry a numbered marker to it. A
  sentence about the world should be traceable to the thing that said so, in one tap.
- **A Sources list** at the foot: publisher, title, date, link. This is the part a learner
  should be able to check us on, so it is a first-class region of the page, not a footnote
  block in small grey type.

Above the body, one line of continuity: `Builds on Brief #4 (Jul 28)` — a link. This is the
product claim of the whole feature made visible.

**A quiet period.** Sometimes the honest answer is that nothing material happened. The Beat
rail shows that as a **Skipped** entry — dated, one line, *"Nothing material since Brief #4 —
the Commission's consultation is still open, closing Sept 12"* — not a hole in the list, and
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
claimable).
Each Brief is a published artifact with a publication date and a Source list, and it is
**immutable, full stop** — not "immutable once engaged" as a lesson is. A lesson can be revised
because it teaches something timeless and the learner has not reached it yet; a Brief is a
claim about the world on a date, and rewriting it retroactively would make **Brief continuity**
a lie. If a Brief gets something wrong, the *next* Brief corrects it in the open. That is what
an analyst does.

**A Brief's period is "since the last Brief", never a calendar slot.** Brief #7 covers
everything since Brief #6, whenever that was. This falls out of §4.2 and is better than the
calendar alternative in three ways: a learner returning after a month triggers **one**
generation rather than four, a backlog of unwritten Briefs can never accumulate, and it is more
faithful to §4.5 — the delta is naturally measured from the last report, not from Monday.

**4.2 Whoever shows up drives the work; a late Brief is a late Brief, never a missing one.**
**Cadence is a floor on frequency, not a calendar appointment**: *weekly* means "at most one
Brief a week", and the promise to the learner is that a Beat keeps up, not that it fires at
07:00. A Beat becomes **claimable** on its **Anchor day** (§4.11), in the learner's local time,
and the claim is driven by three triggers, in order — all of which already exist:

1. **Arrival.** A request from a learner with a claimable Beat kicks the drain, exactly as
   reaching a lesson kicks its generation today. This is Phase 1's **Trigger + poll**, verbatim,
   and at current scale it is the trigger that will almost always fire.
2. **The reconciler.** It already ticks every `RECONCILER_INTERVAL` whenever the process is
   alive for any reason. One more scan drains claimable Beats for free during any warm moment.
3. **Brief prefetch.** A Beat becomes claimable a little *before* its Anchor day opens, so a
   warm moment on Sunday evening produces Monday's Brief and it is genuinely waiting. This is
   **Prefetch (+N)** on the time axis: the same trick, hiding the same latency, for the same
   reason. Early by hours is a feature; the Brief's period is *since the last Brief* (§4.1), so
   nothing about it depends on the exact moment it was written.

**The learner's local time comes from the arrival, so nothing has to be stored.** "Is it Monday
for this learner" is only ever asked at the moment a request arrives, and a request already
carries `tz_offset_minutes` — the same value `services/progress_read.py` uses to decide what
"today" means for a streak. Aleph therefore needs no stored timezone and no stored delivery
time (§7) to honour an Anchor day, which is a second thing arrival-triggering buys and a
scheduler would have had to pay for. The reconciler (trigger 2) has no request and no offset,
so it is a **backstop that may run a Beat late and never early** — which is exactly §4.2's
promise, not an exception to it. The TDD owns how it stays on the safe side of that.

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

**And it does not foreclose always-on.** Setting `min_machines_running = 1` is a one-line
config change that requires **no code change at all** — it simply promotes trigger 2 from
backup to primary, and Briefs start being ready on schedule. The decision is reversible in both
directions for the price of a deploy, which is the main reason to start here.

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

Two things it *does* take a position on, because they are product rules and not implementation:

- **The analyst never cites what it did not read.** A URL that was retrieved but whose content
  never entered the model's context is not a Source. A citation is a claim of provenance.
- **A Brief with no Sources is not publishable.** If retrieval failed, that is a failure state
  (retryable, visible), never an uncited essay from model priors. The whole difference between
  this phase and asking a chatbot what's new is that difference.

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
| **Depth of read** — share of opened Briefs where the learner reaches the Sources | Is it being read, or glanced at? A feature about provenance should be able to show that provenance gets used. |
| **Skip rate** — Skipped ÷ research runs, per Beat | Calibrates §4.6 in both directions. Near zero means the novelty gate is not gating and we are shipping filler; consistently high on one Beat means weekly is faster than that subject moves (§8 Q7), and high across all of them means the gate is too strict. It is also the number that has to look healthy before a daily cadence is trusted (§4.11). |
| **Wait tolerance** (guardrail, §4.2) — share of researching Beats the learner is still present for when the Brief lands, and what they did in between | The direct measurement of the tradeoff §4.2 accepts. If learners consistently leave and never come back to the finished Brief, arrival-triggering is not working and always-on is the answer. |
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

**New workflows** (W28 is the next free number; W1–W27 are taken):

- **W28 — Deploy an analyst, get a cited Brief.** Create a Beat → the first Brief is researched
  immediately → it renders with resolving Sources.
- **W29 — The second Brief builds on the first.** A Beat with a prior Brief produces one that
  cites the earlier Brief and does not re-report its claims.
- **W30 — A quiet period is Skipped, not padded.** With no novel findings, the run publishes a
  Skipped entry and no Brief body, and does not immediately re-research.
- **W31 — A long absence produces one Brief, not a backlog.** A Beat left claimable for several
  Anchor days generates a single Brief covering everything since the last one (§4.1).
- **W32 — Retrieval failure is recoverable and never uncited.** With retrieval unavailable, the
  run fails visibly and retries; no Brief is ever published without Sources.

## 6. Evals (AI components)

Two new artifact kinds, following the `flashcard_draft` (Phase 3) precedent — and **one new
constraint the harness has never faced.**

**`brief_findings`** (the research step) is judged on:

- **Provenance** — every finding carries a Source that was actually retrieved and read.
- **Recency** — findings fall inside the period since the prior Brief, and are dated correctly.
- **Novelty** — nothing restates a claim already made in a prior Brief of the same Beat.

**`brief`** (the written artifact) is judged on:

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
(§4.2 — the arrival carries it) · a public or shareable Brief · exporting a Beat · comments,
highlights or annotations on a Brief · any aggregation of Brief content across learners ·
counting a read Brief toward **Activated learner** (§4.9, a standing rule rather than a slice
boundary) · always-on hosting and the hibernation rule it would require (§4.2, §4.8).

## 8. Open questions

Questions 1–4 are **resolved by owner decision**; the reasoning is kept because the decisions
are load-bearing and the alternatives will be re-proposed by someone eventually — possibly by me.

1. ~~**Is "Beat / Brief" the vocabulary?**~~ **Resolved: yes.** *Beat* is journalism's word for
   a standing assignment and collides with nothing in [`CONTEXT.md`](../CONTEXT.md); *Brief* is
   the dated artifact. *Watch/Dispatch*, *Desk/Issue* and *Feed/Update* were the alternatives.
   "Newsletter" is the genre rather than the unit and is now absent from this document entirely
   — the product says **Beat** and **Brief**.
2. ~~**What replaces `min_machines_running = 0`?**~~ **Resolved: nothing.** §4.2 replaces the
   scheduler with arrival-triggering plus the existing reconciler and a time-axis prefetch,
   which needs no deployment change, no external cron, and no second deployment artifact. The
   accepted cost is that most Briefs are researched while the learner waits at current scale;
   §3's "the rest of the app stays usable" is what makes that acceptable, and the **Wait
   tolerance** guardrail in §5 is what would falsify it. The always-on option is deliberately
   left one config line away (§4.2) — with §4.8's hibernation as its price.
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
   (tool-using, expensive, mechanical) versus writing (no tools, quality-sensitive) — argues for
   **two** slots on the `outline`/`lesson` precedent rather than one. The TDD owns the split; the
   question here is only whether the per-request picker reaches them, which has a production-guard
   consequence (`MODEL_SLOTS` in `config.py`).
7. **How does a Beat handle a Topic that was a bad idea?** §4.8 means a dead Beat is no longer
   expensive, which removes the urgency but not the question: is there a case for the analyst
   itself reporting *"this subject does not move enough to be worth a weekly beat — try monthly,
   or fold it into a path"*? That is a genuinely new behaviour with no analogue in the product,
   and it may be the most honest thing an analyst can say. A high **Skip rate** (§5) on one Beat
   is exactly the signal that would trigger it.
