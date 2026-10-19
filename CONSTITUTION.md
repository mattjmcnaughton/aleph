# Aleph Constitution

These are durable constraints on Aleph's design and implementation, agreed on
2026-10-19. They are obligations, not a claim that existing code has been audited
for compliance. They do not authorize deferred features or speculative infrastructure.

## 1. The server owns authorization and durable invariants

Every operation on learner-owned data verifies the authenticated account's access.
Knowing an identifier is not authorization. Rules protecting progress, content,
and path structure hold regardless of the initiating client. Client validation and
disabled controls improve usability; they do not enforce security or integrity.

Model output is untrusted input. It cannot grant access, bypass validation, or
authorize a mutation.

## 2. Learners authorize changes; models propose them

A model may produce a Proposal, but conversation text and model output never
constitute permission to apply it. Apply requires an explicit learner action and
validation against current state; a previously valid Proposal may have become stale.

Shaping never silently changes progress or engaged content. Changes preserve the
history and undo boundaries defined by the domain.

## 3. Persisted history remains truthful

Engaged lessons and published Briefs are not silently rewritten. Corrections
respect the artifact's domain rules and remain distinguishable from the original
record. Explicit learner-authorized deletion remains governed by the domain.

Related writes constituting one operation succeed together or fail together.
Retries and concurrent requests must not duplicate a logical operation or violate
its invariants. Use database constraints, transactions, and concurrency controls
where guarantees cross process boundaries; an in-process lock alone is insufficient.

## 4. Failure, absence, and success are different states

Infrastructure failure must not masquerade as empty data, zero progress, a safety
refusal, or a Skipped period. A successful response means the promised operation
has succeeded: accepting background work is not completing generation, and
streaming provisional output does not imply durable storage.

Long-running work has explicit outcomes and a recovery path for interruption.
Clients can distinguish waiting, success, and actionable failure.

## 5. Model the domain explicitly and use its ubiquitous language

The domain model describes learner activities, rules, and constraints, not merely
the database schema. Use the same domain terms in discussion, documentation, code,
API contracts, and model prompts. [CONTEXT.md](docs/CONTEXT.md) owns the vocabulary;
resolve ambiguous meanings rather than introducing incidental synonyms.

Name important states, transitions, invariants, and rejection reasons explicitly.
Keep authoritative domain rules out of UI components, HTTP handlers, and model
prompts. Pure rules belong in domain code; services and persistence enforce them
against authorized, current state within the necessary transaction boundary.

Test the model with concrete examples and counterexamples: when does a lesson
become Engaged, when may a Change be undone, and how does Skipped differ from failed?
When a concept changes meaning, reconcile documentation, implementation, contracts,
and tests. Surface disagreements rather than automatically privileging code or prose.

Introduce bounded contexts when meanings genuinely differ, with explicit boundaries
and translations. DDD does not mandate aggregates, value objects, domain events,
CQRS, or event sourcing; justify each by the problem it solves.

## 6. Organize code around responsibilities and dependency boundaries

- Routers own HTTP parsing and response translation, call services, and contain
  neither domain rules nor direct persistence access.
- Services orchestrate use cases, authorization, domain rules, agents, repositories,
  and transaction boundaries. Business behavior does not depend on HTTP objects.
- Domain code takes explicit inputs and returns results without database, network,
  model-provider, or application-configuration dependencies.
- Agent definitions own instructions and structured outputs. They bind no concrete
  model and import no application orchestration or persistence layers.
- Repositories own queries and storage operations. Services decide which operations
  must succeed together; database constraints protect structural and concurrency
  invariants.
- API contracts are separate from persistence models. Database representation must
  not accidentally become the public interface.

Dependencies flow from orchestration toward these capabilities, never back from
domain code or agent definitions into orchestration. Enforce meaningful import
boundaries with automated checks. Testing a pure rule does not prove its
transactional enforcement.

## 7. Keep responsibilities cohesive and complexity earned

Each module has a coherent responsibility. Split responsibilities that change
independently, not files that merely exceed an arbitrary line count. Keep a rule's
authoritative implementation in one place wherever practical.

Use the existing stack and boundaries unless concrete requirements justify change.
Add abstractions, dependencies, or services for demonstrated problems, not hypothetical
flexibility. New operational burdens need an explicit rationale. A layer owns
meaningful behavior or a real boundary, rather than merely forwarding calls.

## 8. One API serves equivalent learner experiences

Every learner client uses the same API contract and domain meanings, and a learner
can do the same things in each. When more than one learner client exists,
learner-facing changes land in all of them together, or the missing implementation
has a tracked issue linked from the PR. Admin tools remain web-only. Notifications
reach a learner on every client able to receive them. Authentication mechanisms may
differ without changing ownership or authorization rules.

Shared rules belong on the server where practical. Rules needed independently in
more than one client are implemented and tested in each. API changes keep the
committed schema, any generated clients, and documentation consistent.

## 9. Generated content is untrusted; quality needs separate evidence

Validate generated content before accepting it as application state. Render it
without executable HTML or dangerous URL schemes. A valid schema does not establish
accuracy, teaching quality, or grounding. Deterministic tests verify application
behavior; evaluations assess generated-content quality. Neither replaces the other.

The analyst cites only sources it actually received as evidence. Missing evidence
is not permission to publish unsupported claims as researched findings.

## 10. Verification targets the guarantee being claimed

Tests exercise observable behavior with independently derived expectations. Prefer
fakes over mocks of implementation details. Use the cheapest test level that can
establish the claim: database guarantees need database-backed verification, client
behavior needs client-level verification, and model quality needs evaluations.

Behavior changes follow red-green TDD. Required CI checks are the merge gate.
Live-provider tests and paid evaluations remain explicit, opt-in operations.

## Governance and use

Read this document before planning, implementation, or review. For a design or
behavior change, identify applicable principles, their enforcement, and the evidence
that will verify them. Report gaps and unresolved conflicts; do not claim compliance
from prose alone or expand a task into an unsolicited whole-repository audit.

Changes to a principle's meaning require explicit agreement and a brief dated
rationale here. Meaning-preserving clarifications need no separate amendment
discussion. Reconcile affected guidance when amending a principle. A violation in
existing code is a discrepancy to resolve, not permission to weaken the principle.

This constitution owns enduring commitments. [CONTEXT.md](docs/CONTEXT.md) owns
vocabulary and detailed domain invariants; PRDs own product scope; TDDs own design
decisions; [architecture](docs/architecture.md) and development documentation own
implementation conventions and commands. Surface contradictions for resolution
rather than silently rewriting another authoritative document.

### Amendment record

- 2026-10-19: Adopted the initial constitution, including explicit domain-driven
  design, ubiquitous language, and architectural dependency boundaries.
- 2026-10-19: §8 no longer assumes exactly two clients or names push notifications
  as iOS-only. Rationale: the Home Screen app proposal
  ([PRD](docs/prds/home-screen-app.md)) makes the web app the phone client and
  delivers Notifications through Web Push, with the native iOS app parked. The
  parity obligation is unchanged whenever more than one learner client exists.
