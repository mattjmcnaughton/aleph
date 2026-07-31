# PRD — Phase 2B: Shape your path (learner-initiated)

**Status:** Draft · **Owner:** solo builder · **Roadmap item:** [Phase 2](../roadmap.md#phase-2--the-tutor)
**References:** [`README.md`](../../README.md) · [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) (ubiquitous language) · [Phase 1 PRD](phase-1-path-generation.md) · [Phase 2 PRD](phase-2-tutor.md) · mock: [phase-2 tutor, Turn 3](../mocks/aleph-phase-2-tutor.html)

> Companion doc: the **[Phase 2B TDD](../tdds/phase-2b-shape-your-path.md)** owns the technical
> design — the proposal payload and its transport, apply/undo transaction mechanics, revision
> regeneration, storage schema, and model routing. This PRD stops at the product boundary.
>
> Implementation is tracked in GitHub issues labeled
> [`tdd-shape-your-path`](https://github.com/mattjmcnaughton/aleph/issues?q=is%3Aissue+label%3Atdd-shape-your-path)
> — the issues (parent epic [#114](https://github.com/mattjmcnaughton/aleph/issues/114) +
> children) are the source of truth for ticket content and status (TDD §15); no ticket list
> is duplicated here.

## 1. Summary

Phase 2A shipped a tutor that reads your path and speaks about it. Phase 2B ships the first tutor
that can **change** it — on your instruction, and never any other way.

On the **path view**, a shaping rail carries a conversation about the path as a whole: "add a couple
of lessons on error handling after this unit," "my next lesson looks too basic — make it assume I
know closures." The tutor answers with a **Proposal**: a small, legible set of edits — add these two
lessons here, revise that one like so — previewed as **ghost rows** in the path itself. Nothing
happens until the learner taps **Apply**; an applied change can be **undone** until the learner has
engaged with what it created, and every change lands in a visible **change history**.

Two edit shapes exist, and only two: **add** lessons (or a unit of them) at or after where the
learner is, and **revise** a lesson the learner has not yet engaged with. Nothing is ever removed,
reordered, or rewritten out from under recorded work — Phase 1's immutability rule softens exactly
one notch, from *immutable once generated* to **immutable once engaged**.

The in-lesson tutor is untouched: it keeps its own thread, its lesson scope, and its
reads-and-speaks boundary exactly as Phase 2A shipped them.

## 2. Context & goals

**This is a re-scope, recorded as one.** The Phase 2 PRD (§4) deferred two different things under
the name "2B": a whole-path *Q&A* slice (path scope, citations as links, the Shaky badge, scope
switching, selection-to-quote) and, separately to Phase 4, path *editing*. The owner's call is that
the editing is the valuable half: a tutor that can only *talk about* a path is decoration next to
one that can *bend* it. So this PRD pulls the **learner-initiated** half of path editing forward
from Phase 4, and the Q&A slice moves behind it (§4 records where everything went). Phase 4 keeps
what was always its real contribution — the **system proposing** edits unprompted from miss data —
and inherits this phase's machinery to do it with.

**The gate this phase does not wait for.** The original 2B was gated on Phase 2A adoption data.
That data does not exist yet (the `tutor` flag has not shipped to non-admin learners), and this
phase proceeds anyway — **an explicit owner decision to build on product conviction rather than
evidence**, recorded here so the roadmap's gate discipline stays honest. The §7 metrics are written
so the bet can visibly fail.

**The collision, resolved.** The Phase 2 PRD §4 recorded why editing was parked: inserting or
removing content mid-path breaks Phase 1's **continuity** (lesson *N+1* is generated conditioned on
lessons *1…N*) and **immutability** (content is fixed once generated). This PRD resolves the
collision by shrinking the edit vocabulary until both invariants survive recognizably:

- **Additive by default, and in this phase additive only.** Adding a lesson at position *k*
  conditions its generation on lessons *1…k−1*, exactly as Phase 1 generates everything — continuity
  is preserved by construction, not by repair. Removal and reordering, which genuinely break it,
  stay out of scope.
- **Immutable once engaged.** A lesson with a recorded Attempt or a completion is never touched.
  Revising a lesson the learner has not engaged with rewrites nothing the learner has done —
  the record of their work stays exact.

**Goals**
- A learner who can see what their path is missing — or what it gets wrong for *them* — can fix it
  in one conversation, without deleting the path and starting over (today's only recourse).
- Every change is legible, previewed, consented to, and reversible until it is real.
- The machinery (proposals, apply/undo, change history) is shaped so Phase 4's system-initiated
  proposals reuse it rather than rework it.

**Definition of "shipped":** see [§12 Release criteria](#12-release-criteria).

## 3. Target user & user stories

**User:** unchanged — a self-directed adult learner on a phone, mid-path. What is new is that they
have opinions about their path: something is missing, something is mis-pitched, and until now the
only fix was delete-and-regenerate, losing all progress.

**Core stories**
1. *As a learner,* I tell the tutor my path is missing something ("nothing here covers error
   handling") and get a concrete proposal — two named lessons, in a sensible spot — that I can
   preview in my path before agreeing to it.
2. *As a learner,* I ask for an upcoming lesson to be pitched differently ("assume I already know
   closures") and, once I apply it, the lesson is regenerated my way — while every lesson I've
   completed stays exactly as I left it.
3. *As a learner,* I apply a change, think better of it, and undo it — my path is exactly as it was.
4. *As a learner,* I can see what I've changed: a history of every applied edit, in plain language.
5. *As a learner,* when I ask for something the tutor can't do to my path ("delete the boring
   unit"), it tells me plainly what it can and can't do — it doesn't pretend, and it doesn't do
   something else instead.

## 4. Scope

**In scope (Phase 2B)**
- The **shaping rail** on the path view: the same rail component, docked right on desktop, a sheet
  on a phone — with its **own conversation per path**, separate from the in-lesson thread (§5.8).
- **Shaping scope** context: the path digest (names + unlock state), Quick-check **Outcomes** per
  attempted lesson, the change history, and the path's topic and level — never a lesson's body (§5.2).
- **Proposals** with exactly two edit shapes: **Addition** (lessons, optionally grouped as a new
  unit, at or after the learner's position) and **Revision** (regenerate a not-yet-engaged lesson's
  content per instruction) (§5.3).
- **Ghost preview** of a proposal in the path rail; **Apply**; **Undo until engaged**; a read-only
  **change history** (§5.4, §5.5).
- Streaming replies, failure states, and refusal behavior — inherited from Phase 2A's transport and
  vocabulary (§5.6, §5.7).
- Instrumentation for the §7 metrics; **evals** for proposals (§9).

**Non-goals — deferred, and where they go**

| Deferred | Was | Goes to |
| --- | --- | --- |
| **Removing, reordering, or merging** lessons/units; revising **engaged** content | Turn 3's "cut the decorators stuff" | **Phase 4 at the earliest** — each breaks continuity or rewrites recorded work; needs its own design |
| **System-proposed edits** from Quick-check miss data and flashcard signal — Aleph suggesting unprompted | Phase 4's defining feature | **Phase 4**, unchanged — it builds on this phase's proposal/apply machinery |
| **Whole-path Q&A slice**: path scope in the in-lesson rail, scope switching, lesson citations as links, the "Shaky" badge, mixed-scope thread dividers | The original 2B (Phase 2 PRD §4) | **A later slice, sequenced against usage** — no longer "2B". The Phase 2 PRD's §6 seams still hold for it |
| **Selection-to-quote** (W10) | Phase 2B per the Phase 2 PRD | Deferred with the Q&A slice; W10 stays reserved |
| **Tutor writes to progress** — marking complete, skipping | — | Nowhere. Completing a lesson is a learner action, full stop (§5.5 of the Phase 2 PRD stands) |
| **Editing from inside a lesson** ("make the next one deeper", said mid-lesson) | — | Out; the in-lesson tutor keeps its 2A boundary. If real conversations show learners asking for edits there, that is signal for a later slice, not a reason to grow this one |

Also out: editing a path's topic or level, cross-path moves, and any change that is not the direct
result of a learner instruction in the shaping conversation.

## 5. Functional requirements

**5.1 The shaping rail**
- On the **path view**, the learner can open a shaping surface: the same rail presentation grammar
  as Phase 2A — docked right column on desktop, bottom sheet on a phone, opened by a floating mark.
- The in-lesson rail is unchanged. Two surfaces, two jobs: in a lesson you ask about *this lesson*;
  on the path view you shape *the path*. There is no scope switching on either surface.
- The rail header offers **new conversation** and **collapse**, as in 2A.
- The **context chip** reads *Shaping · {topic}* — the learner-facing statement that this
  conversation sees, and can change, the path's structure.
- An **empty state** names what the shaping tutor can do (add lessons, revise upcoming ones — not
  remove, not reorder, not touch finished work) and offers the §5.3 suggestions.
- The shaping rail appears only when the path is `ready` (there is a structure to shape). No entry
  point on `pending`/`generating`/`failed`/`refused` paths.

**5.2 What the shaping tutor can see (shaping scope)**
- **Always:** the path's topic and level; the **path digest** (ordered unit/lesson names with
  unlock state); each attempted lesson's Quick-check **Outcome** (correct/incorrect — the datum
  that grounds "you struggled with Narrowing"); and the **change history**.
- **Never:** any lesson's Read passage or Quick check body. The Phase 2 PRD's context bound holds:
  shaping is a structural conversation, and titles + states + outcomes are enough to have it. (This
  also keeps the context flat-sized on long paths.)
- Outcomes are new to the tutor's view of the world (2A's digest was names and unlock state only).
  They appear **only in shaping scope**, and only as outcomes — never the learner's selected
  option, never the question content.

**5.3 Suggestions & the conversation**
- One-tap asks seed the vocabulary: **Add practice on…** *(opens composer prefilled)*, **What's
  missing?**, **Make my next lesson simpler**, **Make my next lesson deeper**.
- Free text is always accepted. The conversation is a real conversation — the tutor can ask a
  clarifying question back ("go deeper on generics the *type-theory* way, or the *day-to-day API*
  way?") before proposing.
- Replies stream, exactly as 2A replies do.

**5.4 Proposals, preview, apply**
- When the conversation arrives at a concrete edit, the tutor produces a **Proposal**: a card in
  the thread carrying one or more edit operations of the two allowed shapes, each with a
  plain-language rationale ("one short lesson on `unknown` vs `any`, then straight into Utility
  Types") and a clear statement of scale ("adds 2 lessons ≈ 10 min").
- **Additions** name the new lessons (and unit, if one is created) and their insertion point, which
  must be **at or after the learner's first non-engaged position**. Additions may not push the path
  past Phase 1's lesson cap; a proposal that would is not offered (the tutor says why and offers a
  smaller one).
- **Revisions** name the target lesson — which must be **not yet engaged** (no Attempt, not
  complete) — and the instruction ("re-teach assuming closures are known"). A revision changes how
  the lesson teaches; it keeps the lesson's slot in the path. Its title may be adjusted to match
  the revised content.
- While a proposal is pending, the path rail shows its effect as **ghost rows** — the mock's
  drawing: insertions rendered in place, revisions marked on the target row — so the learner
  previews the shape of their path before consenting.
- **Apply** is an explicit learner tap on the card. Nothing is ever applied by conversational
  inference — "yes do that" in the composer produces a fresh confirmation on the card, not a silent
  apply. **Never a silent rewrite** is the roadmap's rule and this phase's contract.
- Applying creates a **Change** (the unit of history and undo), inserts added lessons as
  `ungenerated` rows that ride Phase 1's generation machinery untouched (trigger + poll, prefetch,
  the same caps), and queues revised lessons for regeneration. Content the learner has engaged
  with is untouchable at apply time regardless of what the proposal said when drafted — staleness
  is re-checked at apply (the TDD owns the mechanics).
- A proposal the learner ignores or declines simply expires with the conversation's flow; declining
  is never destructive and needs no confirmation.

**5.5 Undo & change history**
- Every applied Change is **undoable until the learner engages** with anything it created or
  revised — records an Attempt on it, or marks it complete. Undo restores the path exactly:
  added rows removed, revised content restored verbatim.
- Once engaged, the Change becomes permanent history; the UI says so plainly rather than hiding
  the button.
- The **change history** is visible from the shaping rail: every applied Change in plain language,
  its date, and its status (applied / undone). Read-only; it is a record, not a second edit
  surface.
- Undo never touches progress: it removes only what the Change created. (By the engagement rule,
  anything the learner has worked on is un-undoable, so undo can never delete an Attempt or a
  completion.)

**5.6 Reply delivery**
- Streaming, stop, and the disabled-composer-while-in-flight behavior are Phase 2A's §5.6,
  unchanged and served by the same transport.

**5.7 Failure, limits & error states**
- **Failed reply:** 2A's contract verbatim — clear message, retry, question preserved, the rest of
  the app usable.
- **Failed apply:** the path is never left half-changed — an apply either lands whole or not at
  all, with a clear error and the proposal still on the card to retry.
- **Failed generation of added/revised lessons:** Phase 1's failure states own this already
  (`failed` + learner-facing retry); a shaping Change is *applied* when the structure lands, not
  when generation finishes — exactly like path creation.
- **Declined edit** (out-of-vocabulary ask — remove, reorder, edit engaged work, skip ahead): a
  graceful, non-error reply that names what shaping can do, distinct in wording from both failure
  and the §10 safety refusal.
- **Daily cap:** shaping messages get the same disabled-by-default knob posture as 2A
  (`RATE_LIMIT_SHAPING_MESSAGES_PER_DAY = 0`); applied additions are already bounded by Phase 1's
  lesson-generation daily cap and the per-path lesson cap. No cap UI.
- No shaping state — pending proposal, failed apply, mid-regeneration — may ever block reading a
  lesson, attempting a Quick check, or marking complete.

**5.8 Conversation & persistence**
- The shaping conversation is **its own thread, one per path**, separate from the in-lesson
  conversation. The in-lesson rail never shows shaping turns or proposal cards; the shaping rail
  never shows lesson-scope turns. (This is why 2A's surface can stay bit-identical.)
- The thread persists across sessions and survives lesson navigation; **new conversation** clears
  it. Deleting a path deletes both of its conversations.
- Proposal cards persist in the thread with their resolution state (pending / applied / undone /
  superseded), so returning to the conversation reads as history, not amnesia. The **change
  history** (§5.5) survives even a cleared thread — history belongs to the path, not the
  conversation.

**5.9 Instrumentation**
- Events sufficient to compute every §7 metric: shaping conversation started, shaping message sent,
  shaping reply completed (with the 2A latency/outcome fields), proposal shown (edit shapes and
  counts), change applied, change undone — each stamped with account, path, and timestamp in the
  established shape. Added/revised lesson generation is already covered by Phase 1's
  `lesson_generated`.
- **Session** and **Day** keep their definitions.

**5.10 Nocturne**
- The shaping rail extends Nocturne with the established grammar: **iris** is the tutor's accent on
  this surface too; ghost rows and the proposal card follow the mock's Turn 3 drawing. Teal remains
  the path's color — applied (real) rows are teal, proposed (ghost) rows are iris until applied.

## 6. AI system design (product view)

**Data model.** One branch grows and one small table arrives:

```
account → path → conversation (kind: lesson | shaping) → messages
                 path → changes (the change history)
```

- **conversation** gains a **kind**; a path has at most one of each. The Phase 2A thread is the
  `lesson` kind; nothing about it changes.
- **message** in a shaping thread may carry a **Proposal** payload (as 2A messages may carry a
  Tutor check).
- **change** — an applied edit: what it did, what it replaced (for undo), when, and its status.
  Owned by the path; survives thread clearing.

**The invariant amendment — stated once, precisely.** Phase 1's rule was *content is immutable once
generated*. From this phase, the rule is **content is immutable once engaged** (an Attempt exists
or the lesson is complete). Between *generated* and *engaged* there is now exactly one mutation
path: a learner-applied Revision, which snapshots what it replaces so undo is exact. Continuity's
statement is unchanged — lesson *N+1* is generated conditioned on lessons *1…N* — and additions
satisfy it by construction, because an added lesson is generated the same way any lesson is: from
everything before it.

**One consequence is accepted and named:** content generated *before* a change (via prefetch)
does not know about lessons inserted or revised *after* it was written. An added lesson can build
on everything before it, but the pre-existing lesson after it will not reference it. This is the
cost of additive editing without cascading regeneration, and it is deliberately accepted — the
alternative (regenerating downstream on every change) burns money and violates
immutable-once-engaged the moment the learner is past the insertion point. The TDD owns keeping
revised lessons from *contradicting* their already-generated neighbors.

**Context assembly** extends the Phase 2 PRD's named seam with a shaping-scope variant — same
bounded-window discipline for carried turns, same "the structural context must dominate" rationale.
The shaping context is names, states, outcomes, and history — no lesson bodies — so it stays small
and flat for the life of the path.

**Model architecture.** Shaping is a different job from tutoring — it produces *structure* under
constraints, closer to the outline agent than to chat — and gets its **own model slot** alongside
*outline / lesson / judge / tutor*. Routing is the TDD's.

**The proposal is a contract, not prose.** The edit operations in a Proposal are structured data
validated against the rules in §5.3–§5.4 (shapes, positions, engagement, caps) before the card ever
renders — an invalid proposal is the tutor's failure to fix invisibly, never the learner's to
discover at apply time. The reply text explains; the payload is what applies.

## 7. Success metrics

Phase 1's **Activation rate** stays the north star. This phase makes a different compounding claim
than 2A did: that learners who can bend their path stick with it instead of abandoning it. Stated
so it can fail:

**Primary — Shaping yield.**
> Of applied Changes, the share whose created or revised lessons the learner subsequently
> **engages with** (records an Attempt or completes) within 7 days of applying.

If learners apply changes and then never touch what they asked for, shaping is theater — adding
lessons *feels* like progress and substitutes for doing them. That is the failure mode this metric
is aimed at, and a low yield argues against Phase 4, which would generate *more* proposals.

**Supporting metrics**
- **Shaping adoption:** % of learners with a ready path who apply ≥ 1 Change.
- **Proposal acceptance:** applied / proposed. Low acceptance with high conversation depth means
  the tutor proposes badly; near-100% acceptance suggests the consent step is a rubber stamp.
- **Edit-shape mix:** additions vs revisions — tells us which lever learners actually want.
- **Undo rate:** undone / applied, and time-to-undo. A guardrail on proposal quality — regret is
  the signal consent didn't work.
- **Depth to proposal:** median messages before the first proposal in a conversation.

**Guardrail / counter-metrics**
- **Path completion must not fall on shaped paths.** Paths growing while completion stalls is
  the hoarding failure ("I'll add it" replacing "I'll do it").
- **Quick-check correctness** stays in its Phase 1 band on revised lessons — a revision that makes
  checks trivially easy inflates a Phase 1 guardrail metric.
- **Generation spend per path** — additions and revisions buy real generations; watched via the
  existing per-call token data, bounded by the existing caps.
- **Reply failure rate and latency** — 2A's budgets apply to this surface unchanged.

## 8. Core workflows (E2E)

Continuing the W-numbering (W10 stays reserved for selection-to-quote). Phone-sized viewport,
doubling as the e2e suite.

**W17 — Shape by adding (the magic moment)**
On a ready path with progress → open the shaping rail → "add practice on X after this unit" → a
proposal streams in naming new lessons → ghost rows preview in the path → **Apply** → the lessons
are real, in order, `ungenerated`, and generate on demand exactly like Phase 1 lessons → the
learner completes one.
*Pass:* the path contains the added lessons at the agreed position; every pre-existing lesson and
all progress are bit-identical; the added lesson is completable end-to-end.

**W18 — Shape by revising**
"Make my next lesson assume I know closures" → proposal targets the not-yet-engaged lesson →
Apply → the lesson regenerates per the instruction → every engaged lesson's content, Attempts, and
completion state are untouched.
*Pass:* revised content differs and reflects the instruction (stub-structural assertion); engaged
rows bit-identical.

**W19 — Undo restores exactly**
Apply an addition and a revision → undo each before engaging → the path (structure, content,
progress) is exactly its pre-apply state → engage with a fresh Change's lesson → its undo is now
unavailable and the history says why.
*Pass:* bit-identical restoration; engagement flips undoability.

**W20 — Out-of-vocabulary edits are declined, not improvised**
Ask to remove a unit, reorder lessons, revise a completed lesson, and mark a lesson complete → each
gets a graceful declined-edit reply naming what shaping can do → no proposal card, no change, path
untouched.
*Pass:* zero mutations; wording distinct from error and from safety refusal.

**W21 — Shaping is never on the critical path**
With a proposal pending and a revision mid-regeneration → the learner can still read lessons,
attempt Quick checks, and mark complete; the in-lesson rail behaves exactly as 2A shipped it, and
the shaping thread and lesson thread never bleed into each other.
*Pass:* Phase 1 and 2A behavior unchanged under active shaping.

## 9. Evals (AI components)

**What we eval:** a **Proposal**, given a (path digest + outcomes + change history, conversation,
instruction) input. The conversational reply text around it is already covered by the 2A tutor-reply
rubric posture; the new artifact is the structured edit plan.

**Rubric (all must pass → PASS):**
1. **Well-formed** — every operation is one of the two shapes, at a legal position, targeting
   revisable content, within caps. (Deterministic — this item never needs the judge.)
2. **Responsive** — the proposal does what was asked, at the scale asked; "a couple of lessons"
   does not become a unit of six.
3. **Coherent** — added/revised titles fit the path's topic, level, and sequence; a revision's
   instruction is faithfully reflected in its stated intent.
4. **Honest** — the rationale and cost statement match the payload (says "2 lessons" iff it adds 2).
5. **Bounded** — proposes only when asked for an edit; a question gets an answer, not an
   unsolicited proposal.
6. **Safe** — added-lesson intents respect the Phase 1 refusal boundary (a shaped path cannot
   smuggle in what onboarding would refuse).

**Harness:** the same one, extended — deterministic validation of item 1 gates judge spend;
the judge owns 2–6. Revised/added lesson *content* is then judged by the existing Phase 1 lesson
rubric — shaping must not become a side door to lower content quality.

**Gates:** the Phase 1/2A discipline — ≥ 90% seed-set pass to merge shaper-prompt or
context-assembly changes; any safety failure is a hard block; judge↔human agreement re-checked.

## 10. Guardrails & safety

- **Consent is structural.** The only write path into a path's structure is Apply on a validated
  Proposal. There is no code path from conversation text to a mutation.
- **The engagement boundary is server-enforced** at propose, apply, *and* undo — not a UI
  convention. Stale proposals re-validate at apply.
- **Progress is never written.** Not by proposals, not by applies, not by undo. Completing a lesson
  remains the learner's tap; Tutor checks remain non-scoring (all of the Phase 2 PRD §10 stands on
  the in-lesson surface).
- **The refusal boundary extends to structure.** Adding a lesson is generating content; a shaped
  addition passes the same boundary as an onboarding topic, at proposal time and again at
  generation time (the Phase 1 pipeline's own gates are unchanged).
- **Generated content is data, not instructions** — the shaping context includes generated titles
  and history; imperative text inside them must not redirect the shaper (2A's rule, same wording).
- **Spend is bounded by existing rails:** the per-path lesson cap and the daily lesson-generation
  cap apply to shaped additions with no new machinery.

## 11. Risks & open questions

- **This phase is a conviction bet, on the record.** The 2A adoption gate was consciously not
  waited for (§2). If shaping adoption and yield land low, the loss is bounded to this slice — and
  it is real information against Phase 4's system-proposed edits.
- **Shaping may substitute for learning.** The hoarding failure mode (§7): applying changes feels
  like progress. The yield metric is designed to catch it; if it fires, the answer is probably
  restraint in Phase 4, not more proposal surface.
- **Pre-existing downstream content doesn't know about insertions** (§6, accepted). If real paths
  read as disjointed around insertion points, the escape hatches are (in escalating cost): prompt
  the added lesson to bridge explicitly; offer regeneration of *not-yet-engaged* downstream lessons
  as part of large additions. Neither is in scope now.
- **Revision consistency:** a revised lesson must not contradict already-generated unengaged
  neighbors that were written against its old text. TDD-owned mechanics; rubric item 3 and the
  Phase 1 continuity rubric are the checks.
- **Proposal quality is unproven.** A shaper that proposes six lessons for every ask, or misplaces
  insertions, dies by rubric items 2/5 and the acceptance/undo metrics. The consent step bounds the
  blast radius while we learn.
- **Two threads per path may read as two tutors.** Accepted for the boundary it buys (2A untouched,
  no scope switching). The context chips name each surface's job; if learners are confused, that is
  UX iteration, not architecture.
- **Open:** are two edit shapes enough? "Cut the boring stuff" is the most-drawn ask in the mock
  and it is deliberately declined this phase. The declined-edit rate for removal asks (visible in
  conversations on Logfire) is the datum for whether removal design gets prioritized in Phase 4.

## 12. Release criteria

Phase 2B is shipped when:
- [ ] W17–W21 pass end-to-end on real topics, on a phone-sized viewport.
- [ ] A learner can, on a phone: ask for lessons on a missing subtopic, preview them as ghost rows,
      apply, and complete one — the phase's magic moment — with all prior work bit-identical.
- [ ] A learner can revise a not-yet-engaged lesson and see the instruction reflected; no engaged
      content is mutable by any request the surface can express.
- [ ] Undo restores the exact pre-apply state and is correctly disabled after engagement (W19).
- [ ] Out-of-vocabulary edits are declined gracefully with zero mutation (W20).
- [ ] The in-lesson tutor's behavior, thread, and surface are unchanged from 2A (W21).
- [ ] The proposal seed set passes at ≥ 90% with zero safety failures; added/revised content passes
      the Phase 1 lesson rubric.
- [ ] Instrumentation emits the §5.9 events; §7's primary metric (shaping yield) is computable from
      saved queries.
- [ ] Reply latency stays within 2A's budgets on the shaping surface.
- [ ] The shaping rail, proposal card, and ghost rows extend Nocturne per the Turn 3 mock on both
      viewports.

---

### Appendix — traceability

**To the roadmap & the Phase 2 PRD**

| Element | Disposition here |
| --- | --- |
| Roadmap "Shape your path" (drawn in Turn 3, parked in Phase 4) | Learner-initiated half: **this phase**. System-proposed half: Phase 4, on this machinery |
| Phase 2 PRD §4 "path editing → Phase 4" | Superseded by this PRD for the learner-initiated slice; the §4 collision note is resolved by §2/§6 here |
| Phase 2 PRD's original 2B (whole-path Q&A, citations, Shaky, scope switching, quote) | Deferred to a later slice, sequenced against usage (§4) |
| Roadmap rule "every change is a suggestion you accept or decline, never a silent rewrite" | §5.4 verbatim; structural in §10 |
| Phase 1 continuity & immutability | Amended to **immutable once engaged**; continuity preserved by additive-only construction (§6) |

**To the mock (Turn 3)**

| Mock element | In this phase? |
| --- | --- |
| Conversational asks ("add those two") → proposal card with rationale + cost | Yes — §5.3, §5.4 |
| Ghost-row preview in the path | Yes — §5.4 |
| Apply with undo; change history | Yes — §5.5 |
| "A change that throws away finished work reads differently" | Moot this phase — such changes cannot be expressed (§4); returns with Phase 4 removal design |
| "Cut the decorators stuff" (removal) | No — declined-edit flow (§5.7); Phase 4 at the earliest |
| System-initiated proposals from miss data | No — Phase 4 |
