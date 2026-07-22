# Aleph — CONTEXT (ubiquitous language)

The shared vocabulary for Aleph. These are the exact terms we use in the product, the docs, and the
code — same word, same meaning, everywhere. When a term here has a precise name, prefer it over a
synonym (say **path**, not "course"; **Quick check**, not "quiz question").

> Status: **living document, started at the Phase 1 PRD.** It will be refined and extended during the
> TDD (data types, IDs, states, storage). References: [`README.md`](../README.md) ·
> [`roadmap.md`](roadmap.md) · [Phase 1 PRD](prds/phase-1-generated-path.md).

## Core domain

| Term | Meaning |
| --- | --- |
| **Learner** | A self-directed adult user studying a topic of their choosing. The human. |
| **Account** | The authenticated identity a learner signs in as; owns their paths and progress. Present from day one. |
| **Topic** | The free-text subject a learner wants to learn (e.g. "Rust ownership", "how US healthcare is paid for"). |
| **Level** | The learner's self-assessed starting point for a path, chosen at onboarding — one of *new to it · some experience · I work in it*. Scopes generation. |
| **Path** | A structured learning journey for one topic at one level: an ordered set of units. The top-level thing a learner works through. A learner can have several. (Not "course".) |
| **Unit** | An ordered grouping of lessons within a path (e.g. "Foundations & types"). |
| **Lesson** | The atomic unit of learning: one **Read passage** followed by one **Quick check**. Taken linearly; can be marked complete. |
| **Read passage** | The short teaching passage at the start of a lesson — the content the learner reads. ("Read" is the UI label; **Read passage** is the term we use in prose and schema.) |
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
| **On-demand generation** | Generating a lesson's content when the learner reaches it, rather than all up front. |
| **Prefetch (+N)** | Generating the next *N* lessons ahead of where the learner is, to hide generation latency. |
| **Continuity** | The rule that lesson *N+1* is generated with awareness of the content of lessons *1…N*, so the path builds on itself and never re-teaches or contradicts earlier lessons. |
| **Multi-model architecture** | The assumption that more than one model is used across generation (e.g. structure vs. per-lesson content). Specifics live in the TDD. |

## Progress & structure

| Term | Meaning |
| --- | --- |
| **Progression** | Moving through a path's lessons linearly; the next lesson unlocks as the prior completes. |
| **Mark complete** | The learner action that records a lesson as done. Completion, not correctness, is what counts (the Quick check is non-gating). |
| **Unlock state** | Where a lesson sits on the learner's path: *locked* → *available* → *complete*. The learner-facing axis. (The mock's rail labels this state "current" for the available lesson; *available* is the term, "current" is only a UI label.) |
| **Generation state** | Whether a lesson's content exists yet: *ungenerated* → *generated*. The system/AI axis, driven by on-demand generation. Orthogonal to Unlock state — a lesson can be *available but ungenerated* (generated the moment the learner reaches it). |
| **Progress** | The persisted record of which lessons/units are complete, per path, per account. |
| **Switcher** | The "Your paths" UI for moving between a learner's multiple paths, each keeping its own progress. |
| **Delete path** | Removing a path and its progress (confirmed, not undoable in MVP). Doubles as **reset**: with no regenerate, deleting and creating anew is how a learner discards an unsatisfying path. |

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

---

### Phase boundaries (so the vocabulary stays honest)

Some terms name things that exist in the mocks but are **not** Phase 1. Use them, but know their phase:

- **Tutor** — the context-aware chat rail (**Phase 2**).
- **Flashcard** / **spaced repetition** / grading (**Again/Hard/Good/Easy**) — retention loop (**Phase 3**).
- **Shape your path** — adaptive, learner-approved path edits (**Phase 4**).
- **Streak / goal ring / daily minutes** — light gamification (**Phase 5**).
