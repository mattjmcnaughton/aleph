# Aleph — CONTEXT (ubiquitous language)

The shared vocabulary for Aleph. These are the exact terms we use in the product, the docs, and the
code — same word, same meaning, everywhere. When a term here has a precise name, prefer it over a
synonym (say **path**, not "course"; **Quick check**, not "quiz question").

> Status: **living document, started at the Phase 1 PRD, extended by the Phase 1 TDD** (states,
> generation mechanics, model slots), **the Phase 2 PRD** (the tutor), **the Phase 2 TDD**
> (the tutor model slot, reply transport), **the Phase 2B PRD** (shaping), **the Phase 5
> streaks PRD** (Daily streak, Path streak, Active day, Best streak — pulled forward, see the
> phase-boundary note below), **the Phase 3 PRD** (which widened **Active day** to count a
> review, and owns the Retention section below), **and the Phase 6 PRD** (which owns The
> analyst section, and widens **Active day** a second time — to count reading a Brief —
> against a phase that is **not yet built**). References:
> [`README.md`](../README.md) · [`roadmap.md`](roadmap.md) ·
> [Phase 1 PRD](prds/phase-1-path-generation.md) · [Phase 1 TDD](tdds/phase-1-path-generation.md) ·
> [Phase 2 PRD](prds/phase-2-tutor.md) · [Phase 2 TDD](tdds/phase-2-tutor.md) ·
> [Phase 2B PRD](prds/phase-2b-shape-your-path.md) · [Phase 2B TDD](tdds/phase-2b-shape-your-path.md) ·
> [Phase 5 streaks PRD](prds/phase-5-streaks.md) · [Phase 5 streaks TDD](tdds/phase-5-streaks.md) ·
> [Phase 3 PRD](prds/phase-3-flashcards.md) · [Phase 6 PRD](prds/phase-6-analyst.md).

## Core domain

| Term | Meaning |
| --- | --- |
| **Learner** | A self-directed adult user studying a topic of their choosing. The human. |
| **Account** | The authenticated identity a learner signs in as; owns their paths and progress. Present from day one. |
| **Topic** | The free-text subject a learner wants to learn (e.g. "Rust ownership", "how US healthcare is paid for"). A generation input: every agent prompt reads it, and it is frozen once the path exists (the outline and lessons since were generated from that exact string). Distinct from the **Path title**, the display label a learner may change afterwards. |
| **Level** | The learner's self-assessed starting point for a path, chosen at onboarding — one of *new to it · some experience · I work in it*. Scopes generation. |
| **Path** | A structured learning journey for one topic at one level: an ordered set of units. The top-level thing a learner works through. A learner can have several. (Not "course".) |
| **Path title** | The learner-editable display label for a path, shown in the switcher and the path view. Defaults to (falls back to) the Topic until renamed. Display only — **never** a generation input; no agent prompt reads it (enforced by construction: it is absent from every `*Deps` dataclass). Renaming touches nothing else — not the Topic, and no regeneration. |
| **Unit** | An ordered grouping of lessons within a path (e.g. "Foundations & types"). |
| **Lesson** | The atomic unit of learning: one **Read passage** followed by one **Quick check**. Taken linearly; can be marked complete. |
| **Read passage** | The short teaching passage at the start of a lesson — the content the learner reads. Authored and stored as **Markdown** (GitHub-Flavored, a bounded subset — see `docs/api.md` — including a ` ```mermaid ` diagram where one earns its place), rendered for the learner; the source, not the rendering, is what generation produces and the API serves. ("Read" is the UI label; **Read passage** is the term we use in prose and schema.) |
| **Quick check** | The single question artifact ending a lesson — in Phase 1 a **single-select MCQ**. It is *the question*, not the answering of it. Composed of a **stem**, 3–4 **options**, one **correct option**, and an **explanation**. (Not "quiz", not "test". There is no separate "Check" — Quick check is the one name for this entity.) |
| **Stem** | The question text of a Quick check. |
| **Option** | One selectable answer choice of a Quick check; exactly one is the **correct option**. |
| **Attempt** | A learner answering a Quick check: the option they selected (and when). The interaction/record, distinct from the question itself. |
| **Outcome** | The result of an Attempt: **correct** or **incorrect**. Formative and non-gating — it reveals the explanation and lets the learner proceed either way. |

## Generation

| Term | Meaning |
| --- | --- |
| **Generation** | The AI producing content. Two kinds: **outline generation** (path structure) and **lesson generation** (a lesson's Read + Quick check). |
| **Outline** | The units-and-lessons skeleton of a path, generated once at path creation, before lesson content exists. |
| **Guidance** | The learner's optional free text, captured once at path creation, steering the Outline's shape — which stages, what order, what to emphasise or skip, how big. A generation input alongside Topic and Level: read into the outline prompt, and fixed once the path exists (no route changes it after creation). |
| **On-demand generation** | Generating a lesson's content when the learner reaches it, rather than all up front. |
| **Prefetch (+N)** | Generating the next *N* lessons ahead of where the learner is, to hide generation latency. |
| **Continuity** | The rule that lesson *N+1* is generated with awareness of the content of lessons *1…N*, so the path builds on itself and never re-teaches or contradicts earlier lessons. |
| **Multi-model architecture** | More than one model is used across generation. Realized as configurable **model slots** — *outline*, *lesson*, *judge* (Phase 1 TDD §5.3), *tutor* (Phase 2 TDD §5.3), plus *shaper* (Phase 2B TDD §5.3) — each an OpenRouter model id. |
| **Path status** | The lifecycle of a path's outline generation: *pending* → *generating* → *ready*, with *failed* (retryable) and *refused* (terminal, safety) branches (TDD §4). |
| **Refused** | The path status when the outline agent declines an over-the-boundary topic via its structured refusal output. A first-class result with a graceful message — never conflated with *failed* (TDD D12). |
| **Trigger + poll** | The delivery model for generated content: a POST triggers generation and returns immediately; the client polls a GET until the state resolves (TDD D5, §5.4). |
| **Stale recovery** | A row stuck in *generating* longer than the stale timeout is treated as failed and re-claimable, so a crashed or restarted process self-heals (TDD §4). |
| **Position in path** | A lesson's index in the path's single total order — the order continuity, prefetch, and progression all operate on (TDD §4). |

## Progress & structure

| Term | Meaning |
| --- | --- |
| **Progression** | Moving through a path's lessons linearly; the next lesson unlocks as the prior completes. |
| **Mark complete** | The learner action that records a lesson as done. Completion, not correctness, is what counts (the Quick check is non-gating). |
| **Unlock state** | Where a lesson sits on the learner's path: *locked* → *available* → *complete*. The learner-facing axis. (The mock's path rail labels this state "current" for the available lesson; *available* is the term, "current" is only a UI label.) |
| **Generation state** | Whether a lesson's content exists yet: *ungenerated* → *generating* → *generated*, with *failed* as the retryable error branch (TDD §4). The system/AI axis, driven by on-demand generation. Orthogonal to Unlock state — a lesson can be *available but ungenerated* (generated the moment the learner reaches it). Content is immutable once **engaged** (Phase 2B amendment — was "once generated"): between *generated* and *engaged* the one mutation path is a learner-applied **Revision**. |
| **Progress** | The persisted record of which lessons/units are complete, per path, per account. |
| **Switcher** | The "Your paths" UI for moving between a learner's multiple paths, each keeping its own progress. |
| **Delete path** | Removing a path and its progress (confirmed, not undoable in MVP). Doubles as **reset**: with no regenerate, deleting and creating anew is how a learner discards an unsatisfying path. |
| **Active day** | A calendar day, in the learner's local timezone, on which the learner did **at least one** of: completed a lesson, reviewed a flashcard, or **read a Brief**. Those are the three signals a streak counts — not a view, not an Attempt on its own, not drafting or keeping a card, and never a Brief merely *arriving* (Phase 6 PRD §4.9: a streak that advanced because a background task ran would measure our uptime, not the learner). Membership in the set of Active days *is* the daily target, so there is no separate goal concept. **Widened twice**: by the Phase 3 PRD (§4.9) to count a review — lesson completion alone through Phase 5, live with Phase 3's launch — and by the Phase 6 PRD (§4.9) to count a Brief read. **The third signal is decided but unbuilt**, on the same reasoning the second was decided early: no Brief exists yet, so adding it moves no live streak, and this is the only moment the amendment is free. |
| **Daily streak** | The learner's **global** streak: the count of consecutive Active days, across every path, ending today — or ending yesterday if today is still empty, so it does not break at midnight (Phase 5 PRD §4.4). *The* streak: the one with the flame and the celebration. Derived, never stored (Phase 5 TDD D1) — from `lessons.completed_at`, **union**ed with reviews now that Phase 3 has shipped, and to be unioned with Brief reads when Phase 6 is built. |
| **Path streak** | The same run-of-consecutive-Active-days count, scoped to one path's **lesson completions** instead of every path's. Deliberately narrower than the Daily streak: neither reviews nor Brief reads count toward it — a flashcard belongs to the learner rather than to a path (Phase 3 PRD §4.1) and an orphaned card has no path to credit, and a **Beat** is not a path at all (Phase 6 PRD §4.9), so neither has a path to credit. A quieter stat, shown on the home list and deliberately not celebrated — with multiple paths a learner naturally alternates, which is the **Breadth** metric working, and a per-path streak breaks every time they do (Phase 5 PRD §4.3). |
| **Best streak** | The longest run of consecutive Active days ever recorded — global or per path, matching whichever streak it sits beside — including a run that is not the current one. Renders only when it exceeds the current streak (Phase 5 TDD §14 R5). |

## The tutor

Phase 2 vocabulary. Phase 2 ships **lesson scope only** — see the phase-boundary note at the foot of
this document for what is deferred.

| Term | Meaning |
| --- | --- |
| **Tutor** | The context-aware chat that knows where the learner is. In lesson scope it reads the path and speaks about it, changing nothing; in the **Shaping conversation** (Phase 2B) it may change path *structure*, and only through an applied **Proposal** — never progress, never silently. ("Tutor" names the feature and the assistant's turn in a conversation — the product has no separate assistant persona name.) |
| **Rail** | The tutor's surface: a docked right column on desktop, a sheet over the lesson on a phone. One surface, two presentations — not two features. Unqualified, "the rail" means this. The path view's units-and-lessons list is the **path rail** and the desktop left column is the **Sidebar** — three surfaces, three names (the code keeps them apart as `tutor-rail`, `path-rail`, `Sidebar`). |
| **Conversation** | The persisted thread of messages, **one per path** (not per lesson). Survives moving between lessons and between sessions; deleted with its path. |
| **Message** | A single utterance in a conversation — learner or tutor — recording the **lesson it was asked in**, and optionally a **Tutor check**. |
| **Turn** | One learner Message and the tutor Message it produced, as a unit. Turns persist atomically — a turn exists whole or not at all — and are what the tutor's carried-context window counts (Phase 2 TDD §5.2). Two Messages make one Turn; avoid "turn" for a single message. |
| **Scope** | What the tutor can see for a turn. **Lesson scope** (Phase 2): the current lesson's Read passage, Quick check, the learner's Attempt, plus the Path digest. **Path scope** (deferred with the Q&A slice): every unit and lesson with progress, but never a lesson's body. **Shaping scope** (Phase 2B) is defined in the Shaping table below. |
| **Path digest** | The thin whole-path context available in lesson scope: topic, level, and the ordered unit/lesson **names with unlock state**. Names and state only — never another lesson's Read passage. It is how the tutor answers "have I covered this already?". |
| **Context chip** | The line above the composer naming the current Scope (*Reading · Generic constraints*). The learner-facing statement of what the tutor can see. |
| **Quote** | A span of the current Read passage the learner selected and sent with their question. Visible in the sent message and part of that turn's context. (Deferred — cut from Phase 2, and no longer part of 2B; see the phase boundaries below.) |
| **Suggestion** | A one-tap ask offered by the rail — *Explain this simpler · Go deeper · Quiz me on this · Show me a real example*. Sent as if typed; never a constraint on free text. |
| **Tutor check** | A question the **tutor** asks back inside a conversation, with options and immediate feedback. **Non-scoring and outside progress**: it is not a Quick check, creates no **Attempt**, and touches no progress or metric. It persists only as an artifact of its conversation, deleted with it. (Distinct entity, distinct name — do not call it a Quick check, and avoid "ephemeral": it *is* stored, with the learner's answer, for the life of the thread.) |
| **Grounded** | The property that a tutor reply is anchored in the current lesson's Read passage and does not contradict it. The behavior ships now; as an eval rubric item it lands with the post-launch tutor evals. |
| **Contradiction handling** | The tutor's behavior on a **checkable factual error** in a lesson: correct it, attribute the difference plainly, and say what the Quick check expects (Phase 2 PRD §5.7b). Nothing is regenerated or mutated. Incomplete is not wrong — a level-scoped simplification is never flagged. (A machine-readable *flag event* was cut from Phase 2; the behavior lives in reply text only.) |

## Shaping (Phase 2B)

Phase 2B vocabulary — the tutor that changes the path, on instruction only. Spec:
[Phase 2B PRD](prds/phase-2b-shape-your-path.md).

| Term | Meaning |
| --- | --- |
| **Shape your path** | The flow: a conversation on the path view that ends in learner-approved edits to the path's structure. (The roadmap's name for the Turn 3 mock; the learner-initiated half is Phase 2B, the system-proposed half stays Phase 4.) |
| **Shaping rail** | The tutor's surface on the **path view** — same rail grammar (docked column / sheet), its own thread (the code keeps it apart as `shaping-rail`, beside `tutor-rail` and `path-rail`). Unqualified, "the rail" still means the in-lesson tutor surface. |
| **Shaping conversation** | The second persisted thread per path (conversation kind `shaping`), separate from the in-lesson thread. The in-lesson rail never shows it, and vice versa. |
| **Shaping scope** | What the tutor sees in a shaping conversation: topic, level, the Path digest, each attempted lesson's **Outcome**, and the Change history. Never a lesson's body. |
| **Proposal** | The tutor's structured, validated edit plan, rendered as a card in the thread: one or more **Additions**/**Revisions**, each with rationale and cost ("adds 2 lessons ≈ 10 min"). Data, not prose — the payload is what applies. Where it stands (*pending* / *applied* / *undone* / *superseded*) is derived from the Changes, never stored; "Not now" only dismisses the card in the client, and a reload brings a pending Proposal back. |
| **Addition** | An edit that inserts new lessons (optionally as a new unit) **at or after the learner's first non-engaged position**. The only way a path grows. Added lessons are ordinary `ungenerated` lessons — Phase 1 machinery generates them. |
| **Revision** | An edit that regenerates a **not-yet-engaged** lesson's content per the learner's instruction. Keeps the lesson's slot; the title may adjust. The one mutation between *generated* and *engaged*. |
| **Engaged** | The immutability boundary: a lesson with a recorded **Attempt** or marked **complete**. Engaged content is never added before, revised, or removed by any shaping operation, and engaging with a Change's content ends its undo window. |
| **Ghost row** | A proposed lesson/unit previewed in place in the path rail (iris, not teal) before the learner applies. The mock's drawing of "see it before you say yes". (Rendered client-side from the pending payload — `path-rail-ghost` — so ghosts exist only while that Proposal is pending in the open thread.) |
| **Apply** | The explicit learner tap that turns a Proposal into a **Change**. The only write path into path structure — never inferred from conversation text. **One Apply is one Change**, even when the Proposal mixes Additions and Revisions. |
| **Change** | An applied edit, the unit of history and undo: what it did, what it replaced, when, and its status (*applied* / *undone*). Owned by the path; survives a cleared thread. Its shape(s) are derived from the payload — a mixed edit reports both. |
| **Undo** | Reverting a Change exactly — until the learner engages with anything it created or revised. After that the Change is permanent history. Undo never touches progress, and it is **last-in-first-out**: the newest live Change on a path is the one that can be undone (an older one waits for the ones above it — `409 not_latest`), because a Change's recorded inverse is only true against the path it was applied to. |
| **Change history** | The read-only, plain-language record of every Change on a path, visible from the shaping rail. |
| **Declined edit** | The tutor's graceful reply to an out-of-vocabulary ask (remove, reorder, revise engaged work, touch progress): names what shaping can do. Distinct wording from both failure and safety refusal. |

## Retention (Phase 3)

Phase 3 vocabulary — the loop that turns a read lesson into something remembered: draft, keep,
schedule, review. Built and launched, behind the `flashcards` flag; see the phase-boundary note at
the foot of this document. Spec: [Phase 3 PRD](prds/phase-3-flashcards.md) ·
[Phase 3 TDD](tdds/phase-3-flashcards.md).

| Term | Meaning |
| --- | --- |
| **Flashcard** | A front/back pair generated from a lesson's Read passage. Owned by the learner, not the lesson that produced it (Phase 3 PRD §4.1) — the source lesson is kept only as a citation, so shaping, a Revision, or deletion never takes the card down with it. Not a Quick check: no options, no explanation, and it enters a review schedule instead of ending a lesson. |
| **Draft** | One of 3–5 AI-proposed cards **drafted when a lesson opens, offered when it completes** — front and back, kept by default with a per-card discard toggle (`Aleph drafted 4 cards`). The split is deliberate: drafting takes seconds and reading takes minutes, so starting at open means the cards are already waiting at the completion rather than behind a spinner (Phase 3 TDD D5). A row in `flashcards` with `kept_at IS NULL` (Phase 3 TDD D6); unsaved until kept — a discarded Draft is deleted outright, never archived (Phase 3 PRD §3, §4.2). |
| **Kept card** | A Draft the learner explicitly kept, via `Keep N cards`. Enters the spaced-repetition ladder at **rung 0**, due **tomorrow** — never today, which falls out of entering at rung 0 with no case coded to say so (Phase 3 TDD §5.1). `Got it` promotes it a rung; `Again` demotes it (see **Lapse**); the top rung is a fixed point, so a mature card settles at a wide interval rather than growing without bound. |
| **Due** | A Kept card whose `due_on` has arrived: on or before the end of the learner's local day. The candidate pool the Daily queue draws from — being Due is necessary to be reviewed today, not sufficient, since the cap can leave it waiting. |
| **Daily queue** | The learner's capped set of cards to review today, spanning every path — a path is a filter on it, never a second queue (Phase 3 PRD §4.3). All Due cards when there are 10 or fewer; otherwise the **7 most overdue plus 3 drawn at random** from the rest — anti-starvation, not top-up, since a not-yet-due card is never pulled forward (Phase 3 PRD §4.4). Derived, never stored: decided once, on the first request of the learner's local day, and stable for the rest of it — grading and reloading never re-roll it (Phase 3 PRD §4.5, TDD D3). Home and the app bar show only its size; the true backlog is never displayed (Phase 3 PRD §4.8). |
| **Review** | The learner grading a Kept card in the review session: front, reveal, then one of two grades — **Again** or **Got it** — the fixed ladder this phase ships instead of the four-way Again/Hard/Good/Easy grading the roadmap once promised (Phase 3 PRD §4.6). Not an **Attempt** — that term stays a Quick check's. **Streak union:** a Review counts toward the **Daily streak**, the global one, and never toward the **Path streak** (Phase 3 PRD §4.9 — see both above). |
| **Lapse** | An **Again** grade: demotes the card one rung (floor 0) and sets `due_on` to **today**, so the card re-shows later the **same day** rather than tomorrow. Never costs the Daily queue a slot — the cap counts distinct cards, and a re-shown Lapse is not a new one (Phase 3 PRD §4.7, TDD D8). |

## The analyst (Phase 6)

Phase 6 vocabulary — the second pillar, beside paths: a subject that is still moving, reported
on as it moves. **Specified but entirely unbuilt**; see the phase-boundary note at the foot of
this document. Spec: [Phase 6 PRD](prds/phase-6-analyst.md).

| Term | Meaning |
| --- | --- |
| **Beat** | A standing research assignment on one **Topic** at one **Level**: the analyst a learner deploys, and the thing that produces **Briefs**. The top-level sibling of a **Path** — a learner can have several, capped (Phase 6 PRD §4.7). Where a path teaches what was already settled when you asked, a Beat follows what is still moving. Holds its orders (Topic, Level, Anchor day, Guidance) and its state (when it is next claimable); the orders are frozen at deployment exactly as a path's are, and changing your mind means deleting and redeploying. (Journalism's word for a standing assignment — not "subscription", not "feed", and never "newsletter", which names a genre rather than a thing in this product.) |
| **Brief** | One dated, cited report published by a Beat: a **Markdown** body on the lesson reading surface, plus its **Sources**. Numbered per Beat (*Brief #7*) and **immutable, full stop** — not "immutable once engaged" as a lesson is, because a Brief is a claim about the world on a date and rewriting it retroactively would make **Brief continuity** a lie. A correction goes in the *next* Brief, in the open. Its period is **"since the last Brief"**, never a calendar slot (PRD §4.1), so a long absence produces one Brief covering the gap rather than a backlog. Not a **Lesson**: no **Quick check**, no **Unlock state**, no position in an ordered curriculum. |
| **Source** | A retrieved document a Brief cites: publisher, title, publication date, URL, and the span that grounds the claim. Cited inline and listed at the foot of the Brief — a first-class region of the page, because it is the part a learner checks us on. **The analyst never cites what it did not read** (PRD §4.4): a URL that was retrieved but never entered the model's context is not a Source. **A Brief with no Sources is not publishable** — retrieval failure is a visible, retryable failure state, never an uncited essay from model priors. |
| **Cadence** | How often a Beat may report. A **floor on frequency, not a calendar appointment** (PRD §4.2): *weekly* means "at most one Brief a week", and the promise is that the Beat keeps up, not that it fires at 07:00. **The first slice ships weekly and nothing else** (PRD §4.11) — it is the cadence that strains the novelty gate least and costs 7× less per learner, so the gate is calibrated there before daily is trusted to it. Daily is deferred, not dropped, which is why this stays a named axis rather than collapsing into **Anchor day**. |
| **Anchor day** | The weekday a learner picks at deployment for a Beat to report on — *Reports on ▾ Monday* — evaluated in the learner's **local** time, and the **only scheduling control the product exposes**. A weekly report is a habit and a habit has a day; **a day, never a time**, because §4.2's trigger model has nothing that could honour 07:00 and offering the control would promise precision the design does not buy (PRD §4.11). The Beat becomes **claimable** as its Anchor day opens (a little before, once **Brief prefetch** exists). Local time is read from the arriving request's `tz_offset_minutes`, the same value the streak uses, so honouring an Anchor day needs **no stored timezone and no stored delivery time**. Part of the standing orders, so changing it means deleting and redeploying the Beat (PRD §8 Q5 records that as the first rough edge to revisit). |
| **Brief continuity** | The rule that Brief *N* is generated aware of Briefs *1…N-1* — their claims and, critically, **their cited Source URLs** — so it reports what *changed* rather than re-establishing the subject. The **Continuity** of the Generation section, pointed at a different problem: lesson continuity prevents re-*teaching*, Brief continuity prevents re-*reporting*, and prior Source URLs are what make "we already covered this" a mechanical check rather than a stylistic hope. Surfaced to the learner as the `Builds on Brief #4` line. |
| **Brief prefetch** | Making a Beat claimable a little *before* its **Anchor day** opens, so a moment when the process is already warm — Sunday evening, for a Monday Beat — produces the next Brief early and it is genuinely waiting. **Prefetch (+N)** on the time axis: same trick, same latency hidden, "ahead" measured in hours instead of lesson positions. **Deferred from the phase's first slice** (PRD §7.1) — the warm moment it exploits does not exist while the app sleeps between visits, so arrival is the trigger that actually fires. |
| **Skipped** | The outcome when no finding survives the novelty check against prior Briefs: a dated, one-line rail entry saying nothing material happened, instead of a padded Brief. A first-class result the way **Refused** is for a path — and, like Refused, **never conflated with failure**: Skipped means *the analyst found nothing*, and must never become a laundry slot for *we failed to run* (PRD §4.2, §4.6). A Skipped period is the feature working correctly. |

## Quality, safety & measurement

| Term | Meaning |
| --- | --- |
| **Eval** | An automated quality check on generated content, run as a regression suite over a seed set. |
| **Judge** | The model that scores a generation **binary pass/fail** against the eval rubric. In MVP a prompted frontier model calibrated with few-shot examples (not fine-tuned). |
| **Seed set** | The fixed set of representative topics × levels the eval regenerates and judges on every change. |
| **Rubric** | The dimensions a generation must satisfy: accurate, level-appropriate, in scope, continuous, check-valid, safe. |
| **Refusal boundary** | The safety line: any genuine learning topic is allowed; content that materially aids serious harm is refused. |
| **Activated learner** | A learner who has completed **more than 3 lessons** (≥ 4) **on a single path**, each with a recorded **Attempt**, within **7 days** of signup. The unit behind the north-star **Activation rate** (% of new accounts that activate). Signals real value, not curiosity. |
| **Session** | A run of learner activity with no gap longer than 30 minutes. Used in metrics. |
| **Day** | A calendar day in the learner's local timezone. Used in metrics ("second distinct day"). |

## Design

| Term | Meaning |
| --- | --- |
| **Nocturne** | Aleph's visual system — dark, teal, mobile-first — established in the mocks. New surfaces extend it. |
| **Mobile-first** | Every surface is designed for a phone first, desktop second. |
| **Sidebar** | The desktop-only left column (≥1024px) holding the Switcher and, on a lesson, the current path's condensed lesson list (the **path rail**). Absent on a phone, and never called "the rail" — that word is the tutor's surface. |

---

### Phase boundaries (so the vocabulary stays honest)

Some terms name things drawn in the mocks that are **not all built**. Use them, but know their
phase:

- **Tutor** in **lesson scope** — the in-lesson rail, its **Suggestions**, its **Tutor check**, one
  **Conversation** per path, and streamed replies: **shipped and launched (Phase 2)** — AL-270
  flipped the `tutor` flag's global default on, so every learner sees it. Everything in "The
  tutor" above is built except where a row says otherwise.
- **Shape your path, learner-initiated** — the shaping rail, Proposals, Additions, Revisions,
  Apply/Undo, Change history: **shipped and launched** ([PRD](prds/phase-2b-shape-your-path.md) ·
  [TDD](tdds/phase-2b-shape-your-path.md)). Every term in the Shaping table above is
  implemented, and AL-370 flipped the `shaping` flag's global default on, so the surface is
  live for every learner rather than admins alone. Both flags remain registered as kill
  switches ([deploy.md](deploy.md#launching-a-flagged-phase-al-270--al-370)). "2B" names this
  slice, by owner re-scope — not the Q&A slice below.
- **Path scope** / scope switching / lesson citations as links / the **Shaky** badge on a lesson with
  missed Quick checks — the in-path *Q&A* tutor (**a later slice, sequenced against usage**;
  formerly called 2B, still specified in the Phase 2 PRD).
- **Quote** / selection-to-quote — cut from Phase 2 to keep the first slice simple; deferred with
  the Q&A slice above.
- **Summarized carried context** — Phase 2 carries a bounded window of the most recent turns and
  **drops** what falls out of it; summarizing older turns instead is a later upgrade behind the same
  context seam (Phase 2 TDD D6).
- **Flashcard** / **Draft** / **Kept card** / **Due** / **Daily queue** / **Review** / **Lapse** —
  the retention loop (**Phase 3**), defined in the Retention section above: **shipped and
  launched** ([PRD](prds/phase-3-flashcards.md) · [TDD](tdds/phase-3-flashcards.md) · mock:
  [phase-3 flashcards](mocks/aleph-phase-3-flashcards.html)). All ten tickets of the TDD's
  delivery plan (§16) have shipped, plus AL-410's card-management surface (`/cards`), gated by
  `FeatureFlag.FLASHCARDS`, which now defaults **on** — the fourth flag to run the
  `tutor`/`shaping`/`streaks` dark-then-flip playbook, and it stays registered as a kill switch.
  Grading ships as **two outcomes on a fixed ladder** — *Again* / *Got it* — not the
  Again/Hard/Good/Easy this list used to promise; that needs ease factors and is deferred to a
  follow-on slice (Phase 3 PRD §4.6). **Active day above is already widened to count a review**
  (§4.9): the definition changed the day it was decided rather than the day it ships, because the
  vocabulary is authoritative. The union is now built (TDD D11) **and live**: the review reader
  runs for every learner, so a review can carry the current day and the streak the same way a
  lesson completion always has.
- **System-proposed path edits** — Aleph proposing changes unprompted from miss data, plus the
  destructive edit shapes (remove, reorder, touching engaged work): **Phase 4**, building on 2B's
  Proposal/Apply machinery.
- **Beat** / **Brief** / **Source** / **Cadence** / **Anchor day** / **Brief continuity** /
  **Brief prefetch** / **Skipped** — the analyst (**Phase 6**), defined in The analyst section above: **specified,
  entirely unbuilt** ([PRD](prds/phase-6-analyst.md); no TDD, no code, no flag, no mock). The
  terms are here rather than waiting for the code because the vocabulary is authoritative and
  a name is cheapest to fix before prompts and schemas use it. Two things in that section are
  decisions the PRD makes rather than descriptions of anything running: a Brief's period is
  *since the last Brief* rather than a calendar slot, and **Skipped** is a first-class outcome.
  **Active day above is already widened to count reading a Brief** (PRD §4.9), on exactly the
  Phase 3 precedent — no Brief exists, so the third signal adds no past Active day and moves no
  live streak, and this is the only moment the amendment is free. Nothing reads that third
  signal yet.
- **Goal ring / daily minutes** — light gamification (**Phase 5**); **streaks
  shipped early, see the streaks PRD** ([PRD](prds/phase-5-streaks.md) ·
  [TDD](tdds/phase-5-streaks.md)), the same pull-forward move Phase 2B was.
  **Daily streak**, **Path streak**, **Active day** and **Best streak** above
  are built and **launched** — the `streaks` flag defaults on, having run the
  same dark-then-flip playbook as `tutor`/`shaping`, and stays registered as a
  kill switch.
