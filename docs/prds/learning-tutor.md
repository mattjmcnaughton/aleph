# PRD — Account-wide Tutor and learning recommendations

**Status:** Accepted for a feature-branch experiment, 2026-10-23. Defaults below
govern the first implementation; consequential unresolved decisions still require
the owner. No production release or feature enablement is authorized.
**Companions:** [TDD](../tdds/learning-tutor.md) ·
[GitHub-ready tickets and execution guide](../plans/learning-tutor-tickets.md).
**Authority:** [Constitution](../../CONSTITUTION.md) ·
[Vocabulary](../CONTEXT.md). These documents describe intended behavior, not
shipped functionality. The [execution amendment](../plans/learning-tutor-tickets.md#execution-authorization-2026-10-23)
authorizes issue publication and run-waves delivery into `mattjmcnaughton/tutor`.
Production enablement, paid evaluation, main merge and deployment remain excluded.

## 1. Help the learner choose what deserves attention

The Tutor gains an account-wide view of learning across paths and Beats. It helps
the learner connect interests, decide what to learn or follow next, and continue
existing work when starting something new would not help.

Paths build understanding; Beats follow developments. The useful loop is learning
foundations, following developments, discovering questions, and deepening
understanding. This is the existing Tutor with broader, visible scope, not another
persona and not an autonomous manager of the learner's account.

The first experiment has two connected entry points:

- **Home:** existing work first, then up to three cards under “From your Tutor.”
- **Tutor:** a top-level conversation about overall learning, the learner's stated
  focus, and the same recommendation cards.

The [interactive prototype conversation](https://ampcode.com/threads/T-01a16468-96d4-76f9-9a98-65f5c269c599)
is a visual/interaction reference only. Its history and replies are fictional.
It is not a functional specification or evidence of agent quality. The real
feature reuses existing onboarding and reading surfaces rather than copying the
prototype's simplified library and placeholder Read passage/Brief.

## 2. First-slice boundaries

### Included

1. One persisted account-wide Tutor conversation, separate from lesson and
   Shaping conversations.
2. An optional, learner-edited **Learning focus** that persists across sessions.
3. Read-only context from owned paths, progress, Beats, and Brief read metadata.
4. Grounded recommendations for new paths and new Beats, with editable setup,
   discussion, persistent dismissal, and explicit refresh.
5. Advice to continue existing work, using owned links, without creating new
   recommendations merely to fill space.
6. Clear waiting, successful-empty, failure, and retry states, plus a default-off
   feature flag guarding server and client.

### Excluded

- Lesson/Shaping conversation mining, lesson bodies, Brief bodies, correctness
  profiles, sensitive-attribute inference, embeddings, or a general memory store.
- Web research by this Tutor, one-off Briefs, predictions of breaking news, or
  claims of having read sources it did not receive.
- Automatic enrollment, path edits, Beat edits, scheduling changes, notifications,
  reminder changes, a feed, popularity rankings, or another learner's data.
- Recommendations generated on every visit, scheduled refresh, inferred focus
  updates, a new goal/streak system, and productivity scoring.
- Replacing the lesson Tutor or Shape your path; modifying their flags or rules.
- Native iOS work, general-purpose agent tools, or permission to release.

## 3. Proposed language

These additions were adopted into `docs/CONTEXT.md` by LT-1 for the unshipped
experiment. That glossary remains authoritative; the proposal below records
the intended meanings without silently changing existing definitions.

| Term | Proposed meaning |
| --- | --- |
| **Account-wide Tutor** | The Tutor helping a learner reason across their paths and Beats. Same persona, broader scope. |
| **Learning conversation** | The account's persisted conversation with the account-wide Tutor, independent of lesson and Shaping conversations. |
| **Learning focus** | Optional text the learner explicitly saves about what they want to understand. Not an inferred profile, a path's Guidance, or its Teaching style. |
| **Learning recommendation** | A Tutor-proposed new path or Beat with a reason grounded in the learner's available context, awaiting the learner's decision. |

“Suggestion” retains its existing lesson-rail meaning. A Learning recommendation
is not a shaping **Proposal**, and accepting one is not **Apply**: it creates a
new path or Beat through the ordinary creation flow. UI copy may say “Suggested
path” and “Suggested Beat”; a Beat produces Briefs, so cards must not promise an
already researched Brief.

## 4. Journeys and acceptance

### P1 — Home offers perspective without displacing current work

When enabled, Home retains existing paths/Beats and their continue/read actions
above “From your Tutor.” It displays the same active recommendations as Tutor;
navigation must not make a dismissed or accepted card return.

Initially there is no model call. Show “Find directions to explore” and a brief
explanation of the context used. The learner initiates the first generation.
Thereafter “Refresh recommendations” explicitly requests a new set. Display when
the set was generated. Do not claim it reflects subsequent activity automatically.
Keep the last successful cards visible while refreshing or after a failed refresh.
A successful refresh may return zero recommendations: say there is nothing useful
to add and invite continuing or clarifying focus. Do not manufacture three cards.

**Verify:** enabled/disabled, initial, populated, refreshing, failed-with-old-cards,
successful-empty, and all-dismissed states in client tests; narrow and desktop
browser journeys; GET/navigation causes no model work.

### P2 — The learner can discuss the bigger picture

The top-level Tutor states what it can see. A learner can ask what to do next,
explain an interest, connect two topics, or ask whether to start a path or Beat.
Normal conversational chat is the default. Responses may offer owned existing-work
links and one new recommendation, or a connected path+Beat pair when explicitly
requested. Each card still requires its own explicit creation action.
A recommendation's “Discuss” action carries that card's identity into
the Learning conversation. A refinement supersedes that card only after a whole
successful turn; merely discussing it does not dismiss or accept it.

**Owner amendment, 2026-10-24:** “Help me discover something new” optionally starts
a guided conversation, not a mandatory wizard or persisted mode. Ask a few short
questions one at a time about distance from current interests, path/Beat/both,
and practical constraints; skip answers already supplied and let the learner
return to normal conversation or request recommendations anytime. The starter
uses the ordinary turn API with content: “Help me discover something new. Ask me
a few short questions one at a time to find a direction.” Novelty requests use
past activity as contrast, not a requirement of similarity. A cold-start request
for surprise can ground a recommendation without inventing history. Current
explicit requests take priority over saved focus; no inferred profile or automatic
focus update is introduced.

First-slice replies arrive whole after a visible waiting state, rather than
streaming provisional text. Navigation/reload can recover an in-flight operation.
Failure leaves no half-turn or half-card; the learner can retry explicitly.
Multiple tabs cannot silently overwrite each other's conversation or choices.

**Verify:** persisted two-message turns; contextual discussion/refinement; no
partial publication on invalid output, timeout, disconnect, or clear; reload
recovery and conflicting-tab behavior with stub agents and database tests.

### P3 — A recommendation earns its explanation

Each card contains a proposed Topic, kind (path/Beat), short description, and a
“Why this?” explanation tied to explicit focus or actual supplied activity. For
example: “You completed six electricity-market lessons,” not “You mastered
electricity markets.” A Brief read marker is evidence of opening/reading activity,
not attention duration or comprehension. Inactivity is not disinterest.

Actions: **Explore path / Explore Beat**, **Discuss**, **Not interested**.
Existing topics and dismissed recommendations are excluded from exact repeats;
the Tutor also receives bounded dismissal context to discourage paraphrases.
Semantic novelty is evaluated, not claimed to be mechanically guaranteed.

At most three active cards exist. Explicit refresh replaces the current set
atomically. A conversation can add a card if there is space, or a requested pair
only if two slots are free. With insufficient room, explain how to dismiss active
cards or ask for one kind instead; never silently publish half a requested pair.
If Beats are unavailable, explain that and ask before substituting a path alone.
With three cards a turn may refine the explicitly discussed card, but cannot
silently evict another. A pair cannot be combined with single-card refinement.
Dismissal persists across devices and refreshes. Immediately offer Undo. The
learner UI has no dismissed-recommendations list or later restore action. Undo
cannot exceed the three-card cap or restore an accepted/superseded card.

**Owner UI scope amendment (2026-10-24):** remove the expandable dismissed list,
retaining immediate Undo and persistent dismissal; backend history/restore APIs
remain available. Show animated thinking dots while an operation is pending,
with reduced-motion support and accessible status/recovery text. A failed refresh
must not describe recommendations published by a later turn as preserved old cards;
retain truthful failure feedback when no newer visible cards were published.

**Verify:** evidence references, exact repeat filtering, kind distinctions,
card cap, atomic refresh, dismissal/restore persistence; quality evals cover
unsupported reasons and paraphrased repeats.

### P4 — Exploration is not enrollment

Explore opens the existing creation form with an editable Topic, Level, and
Guidance. Suggested Level is a starting point, never an inferred account-wide
ability. A Beat's Anchor day remains a learner choice, using the current form's
default; a path's Teaching style remains the existing optional field. No model
output may set admin model overrides or trigger generation directly.

Final Create path / Deploy action is the sole acceptance point. Creation and
recording the recommendation as accepted succeed together. Double taps, retries,
or another tab cannot create two objects from one recommendation. An accepted
card disappears from both surfaces and can link to the created object in history.
Creation success is distinct from successful outline/Brief generation: subsequent
generation failures use the existing target's retry UI.

Cancelling leaves the recommendation active. Invalid input or a limit violation
preserves the form and creates nothing. A dismissed, superseded, stale, or deleted
recommendation cannot be accepted from an old tab. If the target is later deleted,
the recommendation stays accepted and does not quietly recreate it.

**Verify:** the ordinary creation validations plus concurrent acceptance, rollback,
lost-response replay, edited inputs, target-generation failure, target deletion,
and cross-account identifiers.

### P5 — The learner controls what is remembered

Learning focus is optional (up to 2,000 characters), editable, and removable. Only
the learner's explicit Save updates it; conversation text never does. Empty focus
is valid. Changing focus invalidates old active recommendations and any in-flight
work based on the old focus; explain why and offer explicit refresh. Already
accepted paths and Beats remain untouched.

“Clear Learning conversation” removes its messages and unaccepted recommendations,
including dismissal history. It preserves Learning focus and created paths/Beats;
the confirmation says so. Removing focus is a separate explicit action. Clearing
must also prevent a previously running response from reappearing later.

The Tutor builds context from currently owned live records. Deleting a path or
Beat removes it from future activity context and invalidates recommendations
grounded in it. Previous Learning conversation text may still mention it; explain
that clearing the Learning conversation removes those mentions. Do not claim
deleting a source rewrites conversational history. Do not store hidden summaries
that can resurrect deleted data. Technical usage receipts may remain without text
so clearing does not reset model quotas.

**Verify:** focus save/clear, conversation clear during a running request, source
deletion, account ownership, redaction of cleared content, and quota preservation.

### P6 — Flags and failures remain honest

`learning_tutor` is independent of `tutor`, `shaping`, and `analyst`, and defaults
off for ordinary learners and admins. Dogfooding uses explicit user overrides.
With the flag off, hide all new entry points and reject its API operations; do not
silently continue paid work through an unguarded creation or refresh endpoint.

If `analyst` is off, omit Beat context and Beat recommendations and reject Beat
acceptance, while allowing path-related use. Existing unrelated learning works
exactly as before. If the flag changes during a run, do not publish the result.
Already-created targets retain their normal lifecycle and visibility rules.

Infrastructure failure is not “no recommendations.” Lack of history is not failure:
invite the learner to name an interest, save a focus, or use ordinary creation.
With neither history nor focus, refresh should explain what input is missing
without spending a model call; free-form conversation remains available.

**Verify:** flag matrix, API bypass attempts, mid-flight flag changes, provider
failure vs empty output, and cold start with and without an expressed interest.

## 5. Quality and experiment evaluation

Optimize for useful next choices and learning follow-through, not cards generated,
time in chat, or number of paths/Beats created. The Tutor should be willing to say
“continue what you have.” Unfinished work is not debt; an explicit goal is optional.

Before wider enablement, evaluate synthetic cases for: accurate reasons, useful
path/Beat distinction, respecting focus and dismissals, avoiding redundant topics,
no unsupported mastery/identity claims, safety, and resistance to prompt injection
in stored topic/Guidance/message text. Deterministic provenance checks and agent
quality evaluation are separate gates. No live eval is run without permission.

Dogfood for two weeks after separately authorized enablement. Review examples and
counts, not statistical uplift claims from a tiny cohort:

- Recommendations displayed, explored, discussed, dismissed, and accepted.
- Accepted paths with at least one completed lesson within seven days.
- Accepted Beats with at least one published Brief read within fourteen days.
- Time to complete a Tutor operation, failure rate, and model usage per successful
  operation and accepted recommendation.

Only report mature follow-through windows; pending windows are not failures.
Aggregate events contain IDs, kinds, timings, and counts, never focus/chat/topic
text. Acceptance alone is not success. At review choose continue, revise, or stop;
there is no automatic global flag flip or invented conversion target.

## 6. Decisions proposed for review

| Decision | Proposed first-slice default | Consequence |
| --- | --- | --- |
| Context boundary | Metadata + explicit focus; no lesson/Shaping chats or bodies | Less personalization, clearer expectations and smaller exposure |
| Refresh | Explicit first generation and refresh only | Stable cards and bounded spend; not automatically current |
| Memory | One Learning conversation and explicit focus | No inferred-profile editor or summarization pipeline |
| Delivery | Whole validated reply through trigger + poll | Slower perceived first text, simpler atomic reply/card publication |
| Rollout | Default off including admins; explicit opt-in | No surprise dogfood exposure or costs |
| Scope | New paths/Beats plus advice about existing work | No autonomous changes or scheduler |

These remain revisable design choices; the owner's execution request accepts them
as the experiment's implementation baseline, not as demonstrated product value. The TDD
maps constitution obligations to enforcement and evidence; the ticket guide owns
dependencies and execution permission boundaries.

## 7. Owner-approved chat-first amendment (2026-10-24)

This amendment supersedes the separate refresh-first UI above. Tutor is one
conversation, not a recommendation dashboard. Home opens that conversation;
neither surface offers a recommendation-refresh button or a “nothing useful”
empty-card message. A reply without a proposal remains a normal conversation.

The Tutor may ask one optional inline question with 2–5 choices. Selecting a choice
does not submit; Continue sends the question and answer as an ordinary turn.
Learners can write their own answer or change the subject in the composer at any
time. There is no mandatory discovery interview. A later successful turn collapses
the old question into conversation history. Pending/uncertain submissions prevent
duplicate choice submission; an existing draft is never overwritten by a choice.

Proposals appear with their originating replies and retain explicit creation and
dismissal/Undo. Existing proposals from older refreshes or unloaded history remain
available in the conversation. No automatic creation or eviction is introduced.
Learning focus is an optional secondary disclosure, not a prerequisite for chat.
Metadata-only context, default-off flags, whole validated replies, transactional
publication, quotas and recovery remain unchanged. Legacy refresh API requests
remain compatible but are no longer the active client workflow.
