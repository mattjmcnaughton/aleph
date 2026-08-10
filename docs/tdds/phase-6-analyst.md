# TDD — Phase 6, slice 1: The analyst

**Status:** Draft (complete — §§1–16) · **Owner:** solo builder
**Companion to:** [Phase 6 PRD — The analyst](../prds/phase-6-analyst.md)
**References:** [`roadmap.md`](../roadmap.md) · [`CONTEXT.md`](../CONTEXT.md) ·
[Phase 1 TDD](phase-1-path-generation.md) · [Phase 2 TDD](phase-2-tutor.md) ·
[Phase 2B TDD](phase-2b-shape-your-path.md) · [Phase 3 TDD](phase-3-flashcards.md) ·
[Phase 5 streaks TDD](phase-5-streaks.md) · [`architecture.md`](../architecture.md) ·
[`api.md`](../api.md) · [`metrics.md`](../metrics.md) · [`evals.md`](../evals.md) ·
[`deploy.md`](../deploy.md)

> The PRD owns the product boundary — what a Beat is, what a Brief promises, what **Skipped**
> means, and what is deliberately never built. This TDD owns everything else: the two tables and
> why they are not an extension of `paths`, the cadence arithmetic and where it is evaluated, the
> retrieval seam and its three implementations, the research/write split and the validator that
> makes "never cite what you did not read" mechanical, the API, the flag, the frontend surfaces,
> the eval fixtures, and the delivery plan.

Decision numbers restart at D1, scoped to this document. References into earlier TDDs are always
qualified ("Phase 1 D5", "Phase 3 D7", "Phase 5 §5.2").

> **Scope: the first slice** (PRD §7.1), not the phase. Brief prefetch, inline citations, the
> streak union's wiring, the `brief_findings` eval kind, period grouping in the Beat rail, and
> three of the five workflows are out. Each is named below as a seam with its re-entry cost, so
> adding one back is a decision rather than a rediscovery.

## 1. Decision record

| # | Decision | Choice | Why |
| --- | --- | --- | --- |
| D1 | Aggregate | **Three new tables — `beats`, `briefs`, `brief_sources`. Nothing is added to `paths`, `lessons`, or `units`, and no existing column changes.** A Beat belongs to a `user`, exactly as a path does; a Brief belongs to a Beat; a Source belongs to a Brief. *(Amended from "two tables": §4 needs Sources queryable as rows, because D9's novelty gate reads **prior cited Source URLs** across a Beat's whole history — PRD §4.5's central continuity material. A JSONB column would make that hot, structured read an unnest.)* This row is about the aggregate's own shape and stays three tables; a fourth, unrelated table (`beat_research_runs`, a rate-limit counter log with no place in this aggregate) shipped later in migration `0013` — see D13's amendment | PRD §2's product argument, made structural — and the concrete form is stronger than the product one. `lessons` carries **four `NOT NULL` columns a Brief can never have a value for** — `unit_id` (FK), `path_id` (FK), `position_in_path`, `position_in_unit` — plus `uq_lessons_path_position_in_path` over the last of them. Reusing the table therefore means either fabricating a synthetic path and unit per Beat, or dropping `NOT NULL` on four columns of the busiest table in the product, on a live database, to store rows that will never use them. Neither is a migration worth writing. The shared thing is the **claim protocol** (D3), not the storage |
| D2 | **Skipped** | **A `briefs` row with `kind = 'skipped'`** — dated, **unnumbered**, one line of prose, no body, no Sources. Not a state on the Beat, not a second table, and **not** anywhere failure can reach. `number` is therefore **sparse over published Briefs only**: `NULL` on a Skipped row, under a partial `UNIQUE (beat_id, number) WHERE number IS NOT NULL` | PRD §4.6 makes Skipped a published outcome, and PRD §4.2 forbids it from ever becoming "a laundry slot for *we failed to run*". Both fall out of this: a Skipped entry is a row the learner sees in the rail, and failure is *structurally elsewhere* (D3 — it lives on the Beat's run state, which has no `briefs` row at all). The second payoff is D4's: the cadence floor and **Brief continuity** both key off "the last entry", which is one query over one table with one `ORDER BY` because a Skipped period *is* an entry. **The numbering is PRD §3's own**: its example Skipped line reads *"Nothing material since Brief #4"* — it references the last Brief and carries no number itself, so the rail reads `Brief #5 · 3 Aug` / `Skipped · 27 Jul` / `Brief #4 · 20 Jul`. It also makes `Builds on Brief #4` correct by construction, since continuity is about prior *claims* and a Skipped period has none |
| D2a | Rejected alternative to D2 | **A `beat_runs` table** — one row per research run carrying `published` / `skipped` / `failed`, with `briefs` holding published artifacts only | Genuinely competitive and recorded rather than dismissed. It would make PRD §5's **Skip rate** ("Skipped ÷ research runs") a database query rather than a Logfire one, keep `briefs` a table of nothing but immutable artifacts, and give per-run diagnostics (documents retrieved, findings surviving D9) a home. It loses on the read that matters most: the Beat rail — the feature's primary read — becomes a union or a join instead of one indexed `ORDER BY`, and D4's "last entry of either kind" has to look in two places. Skip rate comes from `brief_research_completed` (§9) instead, which is where **every** metric in this codebase already comes from (`docs/metrics.md`), so the DB-query advantage buys a capability nothing would use |
| D3 | Run state & claim | **The Beat carries the claim**, exactly as `paths` carries the outline claim: `research_state` (*idle → researching → idle*, with *failed* as the retryable branch **and *refused* as the terminal safety one**), `research_started_at`, `research_error`, `refusal_message`. Claimed with `repositories/_generation.py`'s existing `claimable_predicate` / `stale_cutoff` / `affected_rows`, **imported unchanged**. **Two claim methods, not one:** `claim_research` (automatic, claimable states `(idle,)`) and `claim_research_for_retry` (explicit, adding `failed`) | Phase 1 §5.4 verbatim, which is the single largest piece of leverage in this phase (PRD §2). The two-method split is `claim_outline` / `claim_outline_for_retry` and it matters **more** here than it does for paths: under D15 the trigger is *arrival*, so an auto-claimable `failed` state would mean a retrieval outage bills a fresh research run on every page load of the beats list. `repositories/paths.py`'s own words — "a systematically failing generation is not retry-burned … only the learner's explicit retry loops" — are the rule, and keeping `failed` out of the auto predicate is how it holds. Note the asymmetry with `paths`: a path's status is *terminal* on success (`ready`), a Beat's returns to `idle` because it reports again next week, which is why `research_state` is a new enum rather than a reuse of `PathStatus`. **`refused` is PRD §2's "same safety branch", made reachable:** the researcher's output is a union `Findings | Refusal` on Phase 1 D12's precedent, and an over-the-boundary Topic terminates the Beat with a graceful message rather than failing it — the distinction `paths` already draws |
| D4 | Cadence | **Derived, never stored.** No `next_claimable_at` column. `domains/cadence.py` (pure, stdlib only) answers `next_claimable_on(last_entry_on, anchor_weekday) -> date` and `is_claimable(last_entry_on, anchor_weekday, today) -> bool`; "the last entry" is `max(briefs.published_on)` for the Beat, of **either** kind (D2) | Phase 5 D1's grain — a stored copy of a derivable fact is how two answers to one question start disagreeing — and here the derivation is a pure function of a row that already exists. Three PRD rules fall out rather than being coded: a Beat with no entries is claimable immediately (PRD §3's "the first Brief is researched immediately"), a Skipped entry resets the floor (PRD §4.6) because it *is* an entry, and **W32** — a long absence produces one Brief, not a backlog — is what `today >= next_claimable_on(…)` means, with no catch-up loop to write or bound |
| D4a | A Brief's date | **Stamped at publication, not derived per request.** `published_at` (`timestamptz`, the event) **and** `published_on` (`date`, the label), the latter computed from the claiming arrival's `tz_offset_minutes`. D4's arithmetic reads `published_on` | PRD §4.1 calls a Brief "a dated, immutable record", and its date is part of the artifact rather than a view of it — `Brief #5 — Monday 3 August 2026` is content. Deriving it per request would make an immutable record's date mutable in practice: crossing a timezone would move both the rendered date and the cadence floor. This is a deliberate divergence from Phase 5 D3, and the reason it does not transfer is that a streak is a live computation over history while a Brief is a published document. **Accepted consequence, recorded so nobody "fixes" it:** a learner who deploys in London and reads in Tokyo sees each Brief dated where they were when it published |
| D5 | Where "today" lives, and the reconciler | **The Anchor day is evaluated on arrival only**, in the service, from the request's `tz_offset_minutes` — one owner of "today", exactly `services/progress_read.py`. **The reconciler is untouched: no Beats scan, no `ids_needing_reconciliation`, no dedup entry.** ~~`services/lifecycle.py` changes only to construct and bind D14's second semaphore~~ — **amended (docs sweep, AL-561): that undersold it.** `lifecycle.py` also constructs a live `ExaRetriever` (when `EXA_API_KEY` is configured) and binds it into `briefing_service` via `bind_runtime`'s new `retriever` parameter — the retriever-wiring fix that closed the AL-521/AL-523 handoff gap (see D6's amendment and §2's extension map). The reconciler claim this row makes stands: nothing here adds a Beats scan | PRD §4.2 states this as a product consequence; making it structural is this TDD's job, and the structure turns out to be *less* code than the draft assumed. A Beats scan would have exactly two jobs and neither survives: failed runs must **not** be auto-retried (D3), and a stale `researching` row is already recovered by the next arrival — `claimable_predicate`'s second arm makes it re-claimable, and `effective_state_case` makes it read as failed-with-retry meanwhile, both inherited rather than written. So there is no off-request code path that could evaluate a cadence *at all*, which is a stronger guarantee than a rule saying it must not. **Accepted cost:** a run that crashes mid-flight leaves the Beat reading `Researching…` until the learner returns, then self-heals. Re-adding the scan is one repository method and one line in `tick` |
| D6 | Retrieval seam | **A `Retriever` `Protocol` in `services/retrieval.py`** — `search(queries, *, since=None) -> list[RetrievedDocument]`, frozen (url, publisher, title, published_on, text) — with **three** implementations: `ExaRetriever` (live), `FixtureRetriever` (evals + integration), `StubRetriever` (e2e, beside `services/stub_model.py`). **All three ship in the first slice** — the live adapter is not deferred, so the slice adds one production secret (`EXA_API_KEY`, §12 and [`deploy.md`](../deploy.md)). **Amended (docs sweep, AL-561): `since` is a per-call keyword on `search`, not a constructor argument.** The design this row originally described bound a Beat's period start (`since`) once, at `ExaRetriever.__init__`, on the assumption that a fresh instance would be built per Beat. Nothing ever did that — `services/lifecycle.py` binds **one** `ExaRetriever` for the whole process's lifetime (the correct shape for a stateless adapter otherwise) — so a construction-time `since` would have pinned whichever Beat's period start happened to be passed first, forever, silently making D6's own "pass the plan's `since` through as Exa's date filter" unreachable. Moving `since` onto every `search()` call (sourced from `QueryPlan.since`, §5.2) is what makes one shared instance correct: the value that varies per Beat now rides the per-call argument instead of instance state, so no per-Beat construction is needed anywhere. `FixtureRetriever` and `StubRetriever` accept and ignore the parameter (D10: replay is keyed on the *recorded* queries, never a live `since`) | PRD §4.4's three requirements — resolving URLs, a publication date, enough text to ground a quote — are met natively by Exa's document text plus `publishedDate`, which is why it is the named adapter rather than a general web-search API whose snippets and date coverage would make the third a best-effort. The Protocol is what makes that a swap rather than a rewrite. It lives in `services/` because `agents/` imports no application layer: an agent receives documents as plain frozen dataclasses in its `Deps` and never learns a provider exists |
| D6a | Retrieval is **deterministic**, and no agent calls a tool | A **pure** `build_query_plan(topic, guidance, since, *, max_queries)` in `services/retrieval.py` derives the queries from the Beat's frozen standing orders plus the period start; the service executes the plan through the `Retriever`; the documents are then **read by a model**. Two model calls in the whole pipeline (D7), zero tools, zero agentic loops. The pipeline is: **plan (pure) → retrieve (I/O) → find (model) → gate (pure, D9) → write (model) → validate (pure, D8)** | The PRD says "tool-using" (§4.4); this keeps its substance — the research/write split it actually cares about — while dropping a mechanism §4.4 delegates. **Fixture stability is *an* argument, not the decisive one — see the correction under D10.** The reasons that survive scrutiny are narrower: one fewer model call on a path where the learner is already waiting (PRD §4.2's accepted cost, which a third call makes worse), less machinery in the first slice, and spend that is countable before it is spent. Those argue for shipping deterministic **first**, not for deterministic being right. Three things follow for free: spend is countable *before* it is spent rather than after (PRD §4.7's most expensive generation), `agents/` stays import-free because retrieval is never reachable from inside an agent, and the retrieval step needs no eval because it has no model in it. **What this gives up is query diversity** — a model is better than a template at asking the same subject three different ways, and Exa's neural search is what makes the template viable at all, since it takes a natural-language description plus a `since` date rather than needing keyword engineering. If Briefs come back thin, the named upgrade is a query-proposal call ahead of the plan — and D10's fixture format is shaped so that upgrade costs no fixture migration |
| D7 | Agents & model slots | **Two agents, two slots, one model call each** — `agents/researcher.py` (documents → structured findings) and `agents/analyst.py` (surviving findings → the Brief). Backed by `model_research` and `model_brief`, both added to `MODEL_SLOTS`. Neither binds a tool. The admin per-request picker **does** reach them, stored as `beats.model_research` / `beats.model_brief` | PRD §8 Q6, answered on the `outline`/`lesson` precedent (Phase 1 D14): the choice is stored on the row rather than held on the request precisely because the claim is DB-driven — a retried run must research with the model the admin chose, and a request-scoped override cannot survive to that run. Membership in `MODEL_SLOTS` is what puts both slots behind `config.py`'s production stub guard, which is the whole reason that tuple exists. Two slots rather than one because the calls have opposite profiles: reading documents is mechanical, huge-input, and cheap to get right; writing is quality-sensitive and short — the same argument that split `outline` from `lesson`. **The split is load-bearing for D9, not only D8:** the novelty gate needs structured findings with claims and URLs to compare against prior Briefs, and a single agent that read and wrote in one pass would leave it nothing to gate on but prose |
| D8 | Provenance | **"The analyst never cites what it did not read" is an output validator, not a prompt line.** The writing agent's `Deps` carry *only* documents whose text was actually fetched; a validator asserts every cited URL is in that set and retries on violation. The predicate is importable and shared with the eval layer-1 pre-filters | PRD §4.4 is the phase's central quality rule and PRD §7.1 keeps the research/write split specifically to make it enforceable. A prompt instruction is a hope; a set-membership check against the agent's own inputs is a fact — and D6a is what guarantees the input set is exactly what was read, since the only way a document reaches the writer is by having come back from the `Retriever` and survived D9's gate. `agents/flashcard.py`'s shared-validator pattern (Phase 3 §5.2) is the shape, verbatim. Its sibling — **a Brief with no Sources is not publishable** — is the same check's degenerate case, and resolves to a `failed` run (D3), never to a published body |
| D9 | Novelty gate | **Pure: `domains/novelty.py`.** Takes prior Source URLs + prior claim fingerprints and candidate findings; returns the survivors. **Skipped is "no survivors"** (D2), computed here and nowhere else | PRD §7.1 already argues the gate is mostly deterministic — Source-URL overlap plus claim dedup — and that it belongs as a layer-1 predicate rather than judge spend. Making it a `domains/` module buys three things at once: the rule that decides whether a Brief exists at all is unit-testable to exhaustion with no model and no I/O, the eval harness imports the *same function* it ships (never a second spelling), and the layering test covers it for free. It is also the rule PRD §4.6 says will be argued away under pressure — a pure module with its own test file is the cheapest way to make that argument visible |
| D10 | Evals | **`brief` is the fourth `ArtifactKind`; `brief_findings` is deferred** (PRD §7.1). **Recorded retrieval fixtures are not deferred** — one YAML format under `evals/fixtures/retrieval/`, **keyed on the Beat and recording the query plan alongside the results** (`beat` → `queries` → `results`), read by `FixtureRetriever` and written by a `just` recipe that hits Exa once and dumps the file | PRD §6's new constraint: live retrieval makes an eval measure the news rather than the agent. The fixtures are the phase's largest single new piece of harness work and the thing it cannot ship *or boot* without, so they are their own ticket rather than a line on the agent's. **Correcting D6a's stated reasoning.** D6a first claimed deterministic queries were *required* for stable fixtures, because a model-proposed query would make the fixture key a model output that drifts on any prompt edit. Keying on the **Beat** and recording the **plan** dissolves that: on replay the proposal call is skipped entirely and the recorded queries are executed, so the key is frozen at deployment whether or not a model proposed them. The honest position is that a query proposer is **untestable offline no matter what we do** — freezing its output costs nothing a fixture could have measured. So the plan is recorded now, while it is still a pure function and its queries are trivially reproducible, purely to buy the option: upgrading to a proposer becomes additive rather than a format migration plus a full re-record. On a phase whose fixtures are its largest single piece of harness work, that difference is what decides whether a planned upgrade ever happens. A fixture whose key drifts is still worse than no fixture — it fails by quietly missing rather than by erroring — which is why the key is the Beat and never the query text. `APPLICABLE_ITEMS["brief"]` plus an `ARTIFACT_NOTES` reading of the existing **Grounded** item — pointed at a Source instead of a Read passage — is the whole rubric change; no sixth `RubricItem`, on Phase 3 §10's reasoning |
| D11 | Read tracking | **Two columns and an event, and nothing more: `briefs.read_at` + `briefs.sources_seen_at` + a `brief_read` product event.** `services/progress_read.py` is **untouched** — the streak union stays deferred (PRD §7.1) | PRD §7.1 splits these deliberately and the split is worth keeping sharp: §5's north-star question ("does a Brief bring a learner back on a day nothing else would have?") needs the read timestamps and the event, and needs them from day one because they cannot be backfilled. Feeding Brief reads into **Active day** needs neither, and `CONTEXT.md` already says nothing reads the third signal yet. Re-entry cost is one `UNION` arm in Phase 5 §5.2's query |
| D12 | Rollout | **`FeatureFlag.ANALYST`**, the fifth flag — `False` in `FLAG_DEFAULTS`, present in `ADMIN_DEFAULT_FLAGS`, gated **router-level** so a future route cannot forget it. Off → `404` | The `tutor` / `shaping` / `streaks` / `flashcards` playbook verbatim ([`deploy.md`](../deploy.md)). A kill switch on a feature whose per-run cost is the highest in the product is worth more here than in any prior phase, and it stays registered after launch |
| D13 | Migration | **`0012_analyst`** — three tables (D1), **two** new enums (`beat_research_state`, `brief_kind`; `level` is reused), no change to any existing table or column | The additive shape D1 buys. Rollback is dropping three tables nothing else references; no backfill, no reconciliation, no column to unwind on a live table. *(Amended from "two tables, four enums" — the count was wrong in both directions: `brief_sources` is a table D9 needs, and `Level` is reused rather than redeclared.)* **Second amendment (docs sweep, AL-561): a fourth table, `beat_research_runs`, shipped in a follow-on migration, `0013_beat_research_runs`.** Code-review on AL-521 found that the daily research cap (`RATE_LIMIT_BRIEF_RESEARCH_PER_DAY`, D14) as originally specified — counting `beats` rows whose `research_started_at` fell today — could never fire at production settings: a claim *overwrites* that single stamp on every (re-)claim, so the count could never exceed a learner's live Beat count, which `MAX_BEATS_PER_LEARNER` (3) already holds below the cap (5). `beat_research_runs` is one append-only row per **won** claim (auto or retry alike), inserted in the same transaction as the claim's own `UPDATE`; the cap now counts real runs. It is deliberately **not** a revival of D2a's rejected `beat_runs` table — it carries no `outcome`/`kind`, only that a claim happened, for whom, and when, and nothing routes it into the Beat rail read. See `models/beat_research_run.py` and `alembic/versions/0013_beat_research_runs.py` for the full write-up, including the one accepted gap it does not close (a process death between the claim's commit and the spawn actually starting still consumes a cap unit for work that never ran) |
| D14 | Bounds | **Its own semaphore** (`MAX_CONCURRENT_BRIEF_RESEARCH = 2`), never `max_concurrent_generations`; plus `MAX_BEATS_PER_LEARNER = 3` (PRD §4.7's cap, config not constant) and `RATE_LIMIT_BRIEF_RESEARCH_PER_DAY = 5` on the existing `DailyRateLimiter` | Phase 2 D9's argument, one workload over: research is the most expensive generation in the product per unit of output (PRD §4.7), and it must not be able to starve lesson generation — a learner waiting on lesson 3 of their path should never queue behind three analysts. Two pools is the only arrangement where neither can, and 2 against `max_concurrent_generations`'s 8 says which one yields. The daily cap is the sixth counter on a limiter built for exactly this shape (admins exempt, as everywhere), and it is what bounds *per-learner* spend where D14a bounds *per-Brief* |
| D14a | The cost ceiling | **A Brief costs at most $0.50, and the binding constraint is a character budget on retrieved text — not a document count.** `BRIEF_RETRIEVAL_MAX_QUERIES = 6`, `BRIEF_RETRIEVAL_MAX_DOCUMENTS = 12`, and **`BRIEF_RETRIEVAL_TEXT_BUDGET_CHARS = 160_000`**, allocated evenly across returned documents and truncated at the `Retriever` seam before anything reaches a model. **Amended (docs sweep, AL-561): `BRIEF_RETRIEVAL_MAX_DOCUMENTS` needed its own enforcer, and now has one.** §5.2's `retrieve()` originally showed only `text_budget_chars` in its signature; nothing enforced the document cap, because the character budget alone does not bound *count* — `url`/`publisher`/`title`/`published_on` are unbounded per document and outside the budget's reach (measured at 136,890 uncounted characters for 1,200 documents), and a per-query cap at the `Retriever` cannot reconstruct a total cap after cross-query dedupe. `retrieve()`'s signature now takes `max_documents` alongside `text_budget_chars` and enforces it directly (§5.2) | The owner's ceiling, made structural. A document count does **not** bound cost: twelve 2 000-token articles is ~24k input tokens on D6a's second call (a few cents), and twelve 20 000-token ones is ~240k (several times the whole budget, on one call). Since the length of what a retrieval API returns is not ours to choose, the only bound that holds is on the text itself. 160 000 chars is ≈40k tokens, which puts a typical Brief around **$0.20** across D6a's whole pipeline — retrieval ~$0.04, the research read ~$0.12, the write ~$0.05, and *nothing* for query planning, which D6a made a pure function — with a worst case near **$0.35**, leaving margin for a pricier slot without renegotiating the ceiling. Two honest caveats: the dollar figures assume current Sonnet-class pricing on both slots and current Exa pricing, so they are a **design target, not an invariant**; what is enforced is the character budget, and what *verifies* the ceiling is §9's `brief_research_completed` carrying `usage_tokens` (the helper `services/generation.py` already has) plus the retrieval counts, which is what makes PRD §5's **Cost per read Brief** guardrail computable rather than estimated. Worst-case exposure for one learner is the daily cap times the worst case — 5 × $0.35 ≈ $1.75/day — and that product is the number to watch, not either factor alone |
| D15 | Delivery | **Trigger + poll, verbatim** (Phase 1 D5). `POST /api/v1/beats` returns immediately; the client polls `GET /api/v1/beats/{id}`. **The arrival drain is a side effect of the reads** — listing or opening a Beat evaluates D4's cadence and claims what is due, exactly as reaching a lesson kicks its generation today. The frontend reuses `lib/polling.ts` unchanged | PRD §4.2's trigger 1, which at current scale is the only trigger that fires. Reusing the polling module rather than adding an interval means the researching state gets Phase 1's backoff, its jitter, and its stop conditions for nothing |
| D16 | Workflow numbers | **W29–W33, not W28–W32.** The PRD's "W28 is the next free number" is stale — AL-410 shipped `w28.spec.ts` (kept-card management) after it was written. **W29** (a cited Brief) and **W31** (Skipped, not padded) are the two browser journeys; W30/W32/W33 are integration cases (PRD §7.1) | Recorded as a decision rather than fixed silently, because the PRD's §5 and §7.1 both name numbers and a reader holding the PRD needs the mapping. §14 carries it as a correction |

## 2. Extension map

| Concern | Existing asset | Analyst change |
| --- | --- | --- |
| The claim protocol | `repositories/_generation.py` — `claimable_predicate`, `stale_cutoff`, `effective_state_case`, `affected_rows`; the DB clock as the one clock | **Reuse unchanged.** `BeatRepository.claim_research` is `PathRepository.claim_outline` with a different state column. The single biggest reuse in the phase, and the reason PRD §4.2 needs no deployment change |
| Background execution | `services/lifecycle.py` — `TaskRegistry`, the process-wide semaphore, `GenerationLifecycle` | **Extend — amended (docs sweep, AL-561): more than a semaphore.** A second semaphore (D14) constructed and bound alongside the first, **and** (the AL-521/AL-523 handoff gap's fix) a single process-lifetime `ExaRetriever` constructed here and passed to `briefing_service.bind_runtime`'s new `retriever` parameter — `None` when `EXA_API_KEY` is unset, so startup still succeeds and a Beat's runs fail visibly instead (§12). Neither AL-521 nor AL-523 wired a live retriever into production; this is that wiring, landed as a cross-ticket fix. The registry and the lifespan wiring are reused as-is |
| The reconciler | `Reconciler.tick` → `PathRepository.ids_needing_reconciliation` | **Untouched** (D5). A Beats scan would have exactly two jobs and neither survives: failed runs must not be auto-retried, and a stale `researching` row is already recovered by the next arrival — both inherited from the claim protocol rather than written |
| Generation orchestration | `services/generation.py::GenerationOrchestrator` — `bind_runtime`, claim → run → persist → emit, failure mapping | **Pattern, not the object.** A new `services/briefing.py` in the same shape rather than a sixth concern on a 1 250-line class. It binds its own `spawn`/`model_slot` seams from the same lifecycle |
| Agent purity | `agents/*.py` — no bound model, no config, no application imports, frozen `*Deps`, importable validators | **New** `agents/researcher.py`, `agents/analyst.py` under the same rules; auto-covered by the layering test. Retrieved documents arrive as frozen dataclasses, so `agents/` still imports nothing (D6) |
| Pure derivation | `domains/` — `progression`, `engagement`, `streaks`, `scheduling`, `grading`, `changes` | **New** `domains/cadence.py` (D4) and `domains/novelty.py` (D9), same contract: stdlib only, frozen inputs, no I/O |
| Model resolution | `services/openrouter.py` resolving a slot id; `MODEL_SLOTS` + the production stub guard; the admin picker stored on the row | **Extend:** two slots, two allowlist-bound columns on `beats` (D7). The guard's slot arm iterates `MODEL_SLOTS`, so both are covered by construction |
| Local-day arithmetic | `services/progress_read.py` — the injected `now`, `tz_offset_minutes`, Phase 5 D3's `AT TIME ZONE 'UTC'` pin | **Reuse the pattern** for the Anchor day (D5). Same seam, same sign convention, same both-hemispheres test discipline |
| Rate limiting | `services/rate_limit.py::DailyRateLimiter` + the `UsageCounter` `Protocol` (five counters) | **Extend:** a sixth counter and `check_beat_creation`/`brief_research_capacity_available`/`check_brief_research_retry`, in the shape of the other five. **Amended (docs sweep, AL-561):** the sixth counter is backed by a **new table**, `beat_research_runs` (§4's amendment, migration `0013`), not by reading `beats.research_started_at` directly — a claim overwrites that single stamp on every re-claim, so counting the Beat row itself could never exceed `MAX_BEATS_PER_LEARNER` and would never bind the daily cap |
| Feature flags | `FeatureFlag` enum, `FLAG_DEFAULTS`, `ADMIN_DEFAULT_FLAGS`, `require_*_enabled`, `user.feature_flags` on the session payload | **Extend:** one enum member, two registry entries, one dependency — copied from `require_flashcards_enabled` |
| Router conventions | `routers/v1/`, `CurrentUser` / `Session` aliases, 404-never-403, the `errors.py` envelope | **New** `routers/v1/beats.py`; conventions verbatim. `docs/api.md` gains an `## Analyst` section |
| Markdown rendering | `components/markdown.tsx` — the security boundary for model-written text, with `mermaid.tsx` | **Reuse verbatim, and touch nothing.** PRD §2 and §7.1 both turn on this: a Brief is model-written text and must not get its own renderer, which is also why inline citations are deferred |
| Onboarding flow | `routes/new.tsx` — topic, level, guidance, the create mutation | **Pattern:** a Beat deployment form with one field added (Anchor day). The PRD asks for it to rhyme, and the cheapest way to rhyme is to share the component grammar |
| Home surface | `routes/index.tsx` — `paths-list`, `PathRow`, the due-cards line | **Extend:** a Beats section *beside* "Your paths", never merged (PRD §4.10) |
| Reading surface | `routes/lessons.$lessonId.tsx` — the Markdown body, the completion mutation | **Pattern:** `routes/briefs.$briefId.tsx` — the same reading column plus a Sources region and the `Builds on Brief #N` line; no Quick check, no completion, a read ping instead (D11) |
| Trigger + poll (client) | `lib/polling.ts` — backoff, jitter, stop conditions; `state-card.tsx` for generating/failed/retry | **Reuse verbatim.** `Researching… · started 30s ago` is `state-card.tsx`'s existing shape with different copy |
| MSW | `mocks/handlers.ts` composing per-domain modules with `configure*` / `reset*` | **New** `mocks/beats.ts` in the same shape |
| Deterministic backends | `services/stub_model.py` (sentinel topics force branches); `scripts/e2e_backend.py::create_stub_app` | **Extend:** stub dispatch for the two new agents' output shapes plus sentinels for the Skipped and retrieval-failure branches, and a `StubRetriever` bound in the stub factory only (D6, D10) |
| Eval harness | `evals/rubric.py` (`ArtifactKind`, `APPLICABLE_ITEMS`, `ARTIFACT_NOTES`), `evals/generation.py` (layer-1 evaluators + a judge per kind), `flashcard_seed_set.yaml` | **Extend:** a fourth kind, `brief_seed_set.yaml`, layer-1 predicates importing D8's and D9's shipped functions — **and the new fixture format** (D10), which is the part with no precedent |
| Events | `events.py::EVENT_FIELDS` + the typed emitters | **Extend:** `beat_deployed`, `brief_research_completed` (however it resolved — the `outcome` shape the tutor and drafting events already use), `brief_read` |

**Built new:** migration `0012` (three tables — **amended (docs sweep, AL-561):** was
misstated as two) plus migration `0013` (`beat_research_runs`, added by the AL-521
code-review fix — D1/D13/§4/§12's amendment), `models/beat.py` + `models/brief.py`
(`Brief`, `BriefSource`) + `models/beat_research_run.py`, `domains/cadence.py`,
`domains/novelty.py`, `services/retrieval.py` (+ three adapters), `agents/researcher.py`,
`agents/analyst.py`, `services/briefing.py`, `repositories/beats.py` +
`repositories/briefs.py`, `dtos/beats.py`, `routers/v1/beats.py`, the Beat deployment /
list / rail / Brief routes and components, `mocks/beats.ts`, `evals/fixtures/retrieval/`,
`brief_seed_set.yaml`, a saved Logfire query per §5 metric.

**Not built, and named so the absence is a decision:** no scheduler, no cron, no external trigger,
no `fly.toml` change and no `min_machines_running` flip (PRD §4.2); no stored timezone and no
delivery time (PRD §7); no change to `paths`, `lessons`, `units` or any existing column (D1); no
hibernation rule (PRD §4.8 — arrival-triggering removes the problem rather than managing it); no
`brief_findings` eval kind, no inline-citation renderer, no `markdown.tsx` change, no streak-union
wiring, no Beat-rail period grouping, no Brief prefetch (PRD §7.1).

## 3. Architecture overview

Layering unchanged: `routers → services → (agents, repositories)`, with `domains/` pure beneath.
Two structural claims this slice makes, both enforced by module topology rather than by review:

1. **`agents/` reaches no provider.** The retrieval seam lives in `services/retrieval.py`; an agent
   receives `RetrievedDocument`s as frozen stdlib dataclasses. `RetrievedDocument` is therefore
   *declared in `agents/researcher.py`* — it is part of that agent's `Deps` contract — and the
   service constructs the values. This is `agents/flashcard.py`'s `FlashcardCaps` precedent
   exactly: the shape belongs to the agent, the population belongs to the service, and the import
   runs services → agents and never back.
2. **Two model calls, no tools, no loops** (D6a, D7). The only I/O in the pipeline is one
   `Retriever.search` and the database.

```
src/aleph/
  domains/
    cadence.py          # next_claimable_on / is_claimable — pure (D4)
    novelty.py          # filter_new(findings, prior_urls, prior_claims) — pure (D9)
  agents/
    researcher.py       # documents -> Findings | Refusal   (+ RetrievedDocument)
    analyst.py          # survivors  -> BriefBody | SkippedNote
  services/
    retrieval.py        # Retriever Protocol, build_query_plan, retrieve(), 3 adapters
    briefing.py         # claim -> plan -> retrieve -> find -> gate -> write -> persist
  repositories/
    beats.py            # claim_research / claim_research_for_retry / reads
    briefs.py           # append-only; the rail read; prior claims + Source URLs
  routers/v1/
    beats.py            # router-gated on the analyst flag (D12)
  dtos/
    beats.py
```

The research path, end to end:

```
GET /beats?tz_offset_minutes=-120        (or GET /beats/{id})
  → require_analyst_enabled              (404 if off, before any work)
  → drain_claimable(session, user_id=…, tz_offset_minutes=…)     ← the arrival trigger (D15)
      today = (now(UTC) - offset).date()                          ← one owner of "today" (D5)
      for each beat: is_claimable(last_entry_on, anchor_weekday, today)   ← pure (D4)
        → claim_research(beat_id)        atomic; idle-or-stale only (D3)
        → spawn(run_research(beat_id, local_today))               ← TaskRegistry, unchanged
  → load_beats(...) / session.refresh(beat)   the read the learner actually wanted, taken
                                               AFTER the drain so it reflects what this
                                               request's own arrival just changed
  → the response returns immediately; the client polls (D15)

run_research(beat_id, local_today):
  permit = MAX_CONCURRENT_BRIEF_RESEARCH                          ← its own bound (D14)
  plan      = build_query_plan(topic, guidance, since=last_entry_on, max_queries=…)   pure
  documents = retrieve(retriever, plan, max_documents=…, text_budget_chars=…)   ← the only cost ceiling (D14a)
  result    = researcher.run(…, model=beat.model_research or settings.model_research)
      Refusal → research_state = refused (terminal), refusal_message set        [PRD §2]
  survivors = novelty.filter_new(result.findings, prior_urls, prior_claims)     pure (D9)
  written   = analyst.run(deps with survivors only, model=…)
      SkippedNote → briefs(kind='skipped', number=NULL, published_on=local_today)
      BriefBody   → briefs(kind='published', number=next) + brief_sources rows
  research_state = idle                                           ← ready to report again
```

**Amended (docs sweep, AL-561): the diagram above now drains before it reads.** The original
diagram showed `load_beats(...)` preceding `drain_claimable(...)` on the `GET` path. The shipped
router does the opposite (`src/aleph/routers/v1/beats.py`, code-review FIX 1 on AL-522) and its
module docstring names this section as the thing that was wrong. The order is not stylistic:
reading before draining returns a response built from the **pre-drain** row, and a Beat's
pre-run state (`idle`) is also its post-success state, so `lib/polling.ts` — which stops the
instant it sees any terminal state, `idle` included — never starts polling. A learner opening
`/beats/{id}` for the first time would see `research_state: "idle"` for a run the same request
just started, and the Brief lands with nothing on screen: the AL-522 review's own finding,
verbatim the defect AL-521's FIX 1 had already eliminated one layer down. `docs/api.md` already
documented the shipped (correct) order; only this diagram was stale. Also added: the
`max_documents` argument to `retrieve(...)`, D14a and §5.2's amendment, which this diagram had
not carried.

**One purity note, recorded rather than left to be noticed.** `build_query_plan` is a pure
function but lives in `services/retrieval.py`, not `domains/`. It encodes retrieval-provider
concepts — a `since` filter, a result cap — and provider concepts in `domains/` would be the
actual layering violation. The cost is that the layering test does not cover it, so its purity is
convention; the compensating control is that it takes no `session`, performs no I/O, and its unit
tests pass no fakes.

## 4. Data model & storage schema (migration `0012_analyst`, plus `0013_beat_research_runs`)

`down_revision = "0011_flashcard_management"`. Three tables, two enums, additive throughout —
nothing on an existing table, so nothing here is online-risky on Neon.

**Amended (docs sweep, AL-561): a fourth table shipped in a follow-on migration.** Code-review on
AL-521 found the daily research cap (`RATE_LIMIT_BRIEF_RESEARCH_PER_DAY`, D14) as this section
originally specified it — counting `beats` rows whose `research_started_at` fell today — could
never actually fire: a claim overwrites that one stamp on every (re-)claim, so the count could
never exceed a learner's live Beat count (bounded at `MAX_BEATS_PER_LEARNER = 3`), which sits
strictly below the cap (`5`). `0013_beat_research_runs` (down-revision `0012_analyst`) adds one
new table, purely additive, nothing touched on any existing table:

```
beat_research_runs
  id, created_at, updated_at                      (UUIDAuditMixin)
  beat_id     UUID → beats ON DELETE CASCADE
  user_id     UUID → users ON DELETE CASCADE
  started_at  TIMESTAMPTZ NOT NULL   -- the claim's own fencing stamp, never re-derived
  INDEX ix_beat_research_runs_user_id_started_at (user_id, started_at)
  INDEX ix_beat_research_runs_beat_id
```

One row is inserted every time `BeatRepository._claim` **wins** a claim — auto or explicit retry
alike (D3) — in the same transaction as the claim's own `UPDATE`, so the two writes commit or
roll back together; the daily cap now counts real runs (`UsageRepository.
count_brief_research_runs_since`) instead of Beats. This is deliberately **not** a revival of
D2a's rejected `beat_runs` table: it carries no `outcome`/`kind` and is read by exactly one query
(the cap's own `COUNT`), so it cannot become a second Beat-rail source and D2a's reasoning for
rejecting a run-outcomes table is untouched. **Accepted cost, stated in the migration rather than
left implicit:** a process death between the claim's commit and the spawn actually starting still
consumes a cap unit for work that never ran — narrower than, but the same shape as, D5's own
accepted stale-recovery window. See `models/beat_research_run.py` for the full write-up.

```
beats
  id, user_id → users ON DELETE CASCADE, created_at, updated_at    (UUIDAuditMixin)
  topic               TEXT      NOT NULL       -- frozen generation input, as paths.topic
  guidance            TEXT      NULL           -- frozen, as paths.guidance
  level               level     NOT NULL       -- the existing enum, reused (PRD §4.3)
  anchor_weekday      SMALLINT  NOT NULL       -- CHECK 0..6, Python's Monday = 0
  research_state      beat_research_state NOT NULL DEFAULT 'idle'
  research_started_at TIMESTAMPTZ NULL         -- the claim fence (D3)
  research_error      TEXT      NULL
  refusal_message     TEXT      NULL
  model_research      TEXT      NULL           -- admin picker, stored (D7)
  model_brief         TEXT      NULL
  INDEX ix_beats_user_id

briefs
  id, beat_id → beats ON DELETE CASCADE, created_at, updated_at
  kind            brief_kind  NOT NULL         -- published | skipped (D2)
  number          INTEGER     NULL             -- sparse: published only (D2)
  published_at    TIMESTAMPTZ NOT NULL         -- the event
  published_on    DATE        NOT NULL         -- the immutable label (D4a)
  title           TEXT        NULL
  body_markdown   TEXT        NULL
  skip_line       TEXT        NULL
  claims          TEXT[]      NOT NULL DEFAULT '{}'   -- D9's dedup material (PRD §4.5)
  read_at         TIMESTAMPTZ NULL             -- D11; the north-star signal
  sources_seen_at TIMESTAMPTZ NULL             -- PRD §5's Depth of read (§9)
  UNIQUE (beat_id, number) WHERE number IS NOT NULL
  INDEX ix_briefs_beat_id_published_on (beat_id, published_on DESC)

brief_sources
  id, brief_id → briefs ON DELETE CASCADE
  position      INTEGER NOT NULL
  url           TEXT    NOT NULL
  publisher     TEXT    NOT NULL
  title         TEXT    NOT NULL
  published_on  DATE    NOT NULL
  UNIQUE (brief_id, position)
  INDEX ix_brief_sources_brief_id
```

**Two `CHECK` constraints make D2 structural rather than conventional**, which is the point of
choosing a discriminated row over two tables:

```sql
CHECK (kind <> 'published' OR (number IS NOT NULL AND title IS NOT NULL
                               AND body_markdown IS NOT NULL AND skip_line IS NULL))
CHECK (kind <> 'skipped'   OR (number IS NULL AND body_markdown IS NULL
                               AND skip_line IS NOT NULL))
```

A padded Brief cannot be written as a Skipped row and a Skipped period cannot acquire a body, at
the storage layer, whatever any service does. PRD §4.6 calls its own rule the one most likely to be
argued away later; this is the cheapest place to make that argument cost a migration.

**Three things deliberately absent**, each because it is derivable (Phase 5 D1's grain):

- **No `next_claimable_at`.** D4 computes it from `max(published_on)` and `anchor_weekday`.
- **No `builds_on_brief_id`.** "Builds on Brief #4" is the highest-numbered published Brief below
  this one — a `WHERE number < :n ORDER BY number DESC LIMIT 1`, not a stored edge that could
  disagree with the numbering.
- **No `title` on `beats`.** A path has one because a learner may rename it; nothing in the PRD
  asks to rename a Beat, and the Topic is the label. Adding it later is a nullable column and a
  `display_title` property, exactly as `paths` did in `0008`.

**`claims` is a Postgres array, not a table.** It is only ever read as a whole set for one Beat
(D9's input), never queried element-wise and never indexed — so a table would buy a join and no
capability. `brief_sources` is a table for the opposite reason: its rows are rendered individually
with four structured fields, and D9 reads `url` across a Beat's whole history.

**Growth and the plan.** The rail read is `WHERE beat_id = ? ORDER BY published_on DESC`, served by
`ix_briefs_beat_id_published_on`. At weekly cadence a two-year-old Beat holds ~104 rows, so every
read in this phase is a small index scan on the learner's own data. D9's prior-URL read joins
`brief_sources` to that same bounded set. There is no query here whose cost grows with anything but
one learner's own history, which is why §7 adds no cache.

## 5. The pipelines

### 5.1 Cadence (`domains/cadence.py`)

Stdlib only, frozen inputs — the `domains/__init__.py` contract verbatim.

```python
def next_claimable_on(last_entry_on: date | None, anchor_weekday: int) -> date | None:
    """The first Anchor day strictly after ``last_entry_on``. ``None`` in, ``None`` out —
    a Beat with no entries is claimable immediately (PRD §3)."""

def is_claimable(last_entry_on: date | None, anchor_weekday: int, *, today: date) -> bool:
    """``last_entry_on is None or today >= next_claimable_on(...)``."""
```

Three PRD rules are consequences of that `>=` rather than code:

- **W32 — a long absence produces one Brief, not a backlog.** A Beat idle for six weeks satisfies
  the predicate exactly once, and the single run's period is "since the last Brief" (PRD §4.1).
  There is no catch-up loop to write, and therefore none to bound.
- **A Skipped period resets the floor** (PRD §4.6), because `last_entry_on` is `max(published_on)`
  over rows of *either* kind (D2) — the next arrival does not immediately re-research the same
  empty week.
- **The first Brief is immediate** (PRD §3), from the `None` case.

### 5.2 Retrieval (`services/retrieval.py`)

The `Retriever` `Protocol`, the pure plan builder, and **one entry point that owns every
invariant** — so a second provider cannot ship without them:

```python
async def retrieve(
    retriever, plan, *, max_documents, text_budget_chars
) -> list[RetrievedDocument]:
    """search → dedupe by URL → drop undated/empty-text → cap at max_documents →
    apply the character budget."""
```

- **Dedupe by URL** — one document legitimately answers several of the plan's queries.
- **Drop undated** — PRD §4.4 requires a publication date we can show and reason about; a
  document without one cannot be a Source, so it never reaches a model.
- **Cap at `max_documents`.** Kept in the order established by dedupe/drop-undated (effectively
  the plan's query order, most-valuable-angle-first).
- **The character budget is D14a's cost-ceiling enforcement point.** Even shares, then
  redistribute what short documents did not use; truncation is a deterministic prefix so a
  fixture replays identically. Every model-bound character in this phase passes through this
  function.

`RetrievalUnavailableError` maps to a `failed` run (D3) — visible, retryable, never a published
Brief and **never Skipped**.

**Amended (docs sweep, AL-561): the signature above now shows `max_documents`, which the
original pseudocode omitted.** `BRIEF_RETRIEVAL_MAX_DOCUMENTS` (D14a, §13) is a real configured
value, and nothing enforced it before this parameter existed: the character budget alone does
not bound document *count*, because it counts only `document.text` — `url`, `publisher`,
`title`, and `published_on` are unbounded per document and all enter the researcher's prompt,
measured at 136,890 uncounted characters for 1,200 documents at shipped field widths. A per-query
cap at the `Retriever` cannot reconstruct a *total* cap after cross-query dedupe either, since
the same document can answer several of the plan's queries. `max_documents` is therefore enforced
inside `retrieve()` itself, alongside the character budget it was always meant to sit beside — a
deliberate amendment to this section's signature, landed during AL-512's build rather than left
as a silent gap. See `services/retrieval.py::retrieve`'s own docstring for the exhaustive
reasoning this section summarizes.

**The `FixtureRetriever` raises on a miss rather than returning `[]`.** Downstream, an empty result
and "nothing material happened" are the same value: the gate finds no survivors and the analyst
publishes Skipped. A stale or mistyped fixture key would therefore manufacture a Skipped entry
silently — precisely the conflation PRD §4.2 forbids. Raising is what keeps the two facts apart.

### 5.3 Research (`agents/researcher.py`)

Output is a union — `Findings | Refusal`, Phase 1 D12's shape — so an over-the-boundary Topic
terminates the Beat as **refused** rather than failed (D3).

```python
class Finding(BaseModel):
    claim: str                 # one sentence: the thing that changed
    detail: str                # 2-4 sentences the writer may draw on
    source_urls: list[str]     # validated ⊆ the documents in Deps
    happened_on: date | None   # when the development is dated, not when retrieved
```

The layer-2 validator (`ModelRetry`, shared with the eval layer-1 pre-filters) asserts every
finding cites at least one URL and cites nothing outside `ctx.deps.documents` — D8, as a
set-membership check against the agent's own inputs rather than a prompt instruction.

### 5.4 The novelty gate and Skipped (`domains/novelty.py`)

```python
def filter_new(findings, prior_urls: AbstractSet[str],
               prior_claims: Sequence[str]) -> list[Finding]: ...
```

Pure, deterministic, and therefore both a layer-1 eval predicate and a unit test rather than judge
spend (PRD §7.1). Two mechanisms: **Source-URL overlap** (a finding whose every URL was already
cited by a prior Brief of this Beat is not new) and **claim dedup** (normalized token overlap
against `briefs.claims`, the `restates_stem` technique from `agents/flashcard.py` pointed at a
different pair of strings).

**Skipped is `filter_new` returning empty**, and this is where the design's best property falls
out. With no survivors, `AnalystDeps.documents` is empty — so any Brief the model invents cites
nothing, and §5.5's "a Brief with no Sources is not publishable" rejects it. `SkippedNote` becomes
the **only output that can pass validation**. PRD §4.6's rule is not enforced by a prompt that a
later edit could soften; there is nothing in the model's context to pad from.

The analyst is still *called* when skipping, with `open_threads` carried from prior Briefs — which
is how PRD §3's example line (*"Nothing material since Brief #4 — the Commission's consultation is
still open, closing 11 Sept"*) is reachable at all. A template would produce only its first clause.

### 5.5 Writing and provenance (`agents/analyst.py`)

Output is `BriefBody | SkippedNote`. The validator enforces three things: the branch matches the
input state (survivors present → a skip is a `ModelRetry`), `cited_urls` is non-empty, and
`cited_urls ⊆ {d.url for d in deps.documents}`.

**A Source's metadata is never model-written.** The writer emits URLs only; publisher, title and
publication date are joined from the `RetrievedDocument`s the retriever returned, and
`brief_sources` rows are materialized from those. The block a learner checks us on cannot contain a
hallucinated publication date — which matters more than the citation rule it sits beside, because a
plausible wrong date is the failure a reader cannot catch.

### 5.6 The arrival drain (`services/briefing.py`)

The trigger is a side effect of a read the learner already wanted (D15): listing Beats or opening
one evaluates §5.1 for each of the learner's Beats — bounded by `MAX_BEATS_PER_LEARNER` — claims
what is due, and spawns through the existing `TaskRegistry`. The response returns immediately;
`lib/polling.ts` drives the rest. The claim is atomic (D3), so two concurrent arrivals cannot both
research, and `local_today` is passed *into* the spawned task so the Brief's `published_on` is the
date the arrival decided (D4a) rather than one a background frame would have to re-derive.

### 5.7 Failure semantics

| Case | Run outcome | Learner sees |
| --- | --- | --- |
| Retrieval unavailable | `failed` + `research_error` | `Couldn't reach sources` with a Retry — `state-card.tsx`'s existing shape. **Never** a Skipped entry |
| Zero documents after the §5.2 filters | `failed`, not Skipped | The same retry. "We found nothing to read" is not "nothing happened" (PRD §4.2) |
| Findings, none novel | `idle` + a `skipped` row | A dated Skipped line in the rail. **The feature working correctly** (PRD §4.6) |
| Researcher returns `Refusal` | `refused` (terminal) + `refusal_message` | The graceful message `paths` already uses for a refused topic; no retry, because retrying is not the answer |
| Writer exhausts validator retries | `failed` | Retry. No partial Brief is ever persisted — the row is written once, after validation |
| Process dies mid-run | stays `researching`, reads as failed | Retry on arrival, and the next claim re-runs it (stale recovery, D5) |
| Flag off | — | `404` before any work (D12) |
| Beat cap or daily cap reached | — | `429` through the shared envelope (D14) |

The load-bearing row is the second. Every other system in this codebase treats "no results" as an
empty success; here it must be a failure, because the one thing PRD §4.2 will not tolerate is an
infrastructural miss wearing Skipped's clothes.

## 6. API design

New router `routers/v1/beats.py`, prefix `/api/v1`, every convention verbatim: cookie auth,
404-never-403, the `errors.py` envelope, router-level flag gating (D12). `docs/api.md` gains an
`## Analyst` section.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/beats?tz_offset_minutes=<int>` | Deploy an analyst. Body: `topic`, `level`, `anchor_weekday`, optional `guidance`, optional admin `model_research` / `model_brief`. Returns the Beat immediately; the first research run is claimed in the same request via the same arrival drain the two `GET` routes use (PRD §3 — researched immediately, not at the first anchor day), so this route needs `tz_offset_minutes` for the drain's own `today` derivation (D5) — it stamps the first Brief's `published_on` in the learner's local day (D4a), which sets D4's cadence floor |
| `GET /api/v1/beats?tz_offset_minutes=<int>` | The learner's Beats with unread counts and research state. **Drains claimable Beats** (D15) |
| `GET /api/v1/beats/{id}?tz_offset_minutes=<int>` | One Beat: standing orders, research state, and the rail — entries newest first, both kinds. **Drains this Beat** |
| `DELETE /api/v1/beats/{id}` | Delete. This is also how standing orders change (PRD §4.11 — delete and redeploy) |
| `POST /api/v1/beats/{id}/retry?tz_offset_minutes=<int>` | The explicit retry claim (D3). The **only** path that re-claims a `failed` run — and, added during the build, a **genuine no-op** on any other state (`idle`, `researching`, `refused`; §7's amendment), rate-limited by its own **raising** daily-cap check |
| `GET /api/v1/briefs/{id}` | A Brief: body Markdown, Sources, `builds_on` |
| `POST /api/v1/briefs/{id}/read?tz_offset_minutes=<int>` | The read ping. Body: `marker: "opened" \| "sources"` (D11, §9). **Amended (docs sweep, AL-561):** this route also takes `tz_offset_minutes` — added during the build so `brief_read`'s `age_days` field (§9) compares `published_on` against the learner's *local* day rather than UTC's, which could otherwise go negative for a learner east of UTC reading a fresh Brief in their own morning |

```jsonc
// GET /api/v1/beats/{id}
{
  "id": "…",
  "topic": "EU AI regulation",
  "level": "some_experience",
  "guidance": "policy and enforcement, not stock moves",
  "anchor_weekday": 0,                    // Monday, Python's convention
  "cadence": "weekly",                    // constant in this slice (PRD §4.11)
  "research_state": "researching",        // idle | researching | failed | refused
  "research_started_at": "2026-08-03T09:14:02Z",
  "refusal_message": null,
  "entries": [                            // newest first, never locked (PRD §3)
    { "id": "…", "kind": "published", "number": 5, "published_on": "2026-08-03",
      "title": "The ambient-documentation backlash arrived", "read_at": null },
    { "id": "…", "kind": "skipped", "number": null, "published_on": "2026-07-27",
      "skip_line": "Nothing material since Brief #4 — the consultation is still open." }
  ]
}

// GET /api/v1/briefs/{id}
{
  "id": "…", "beat_id": "…", "number": 5, "published_on": "2026-08-03",
  "title": "…", "body_markdown": "…",
  "builds_on": { "id": "…", "number": 4, "published_on": "2026-07-27" },
  "sources": [
    { "position": 1, "publisher": "Northlake Health System",
      "title": "Ambient Documentation: 14-Month Post-Deployment Review",
      "published_on": "2026-07-30", "url": "https://example.com/northlake-review" }
  ]
}
```

`401` unauthenticated · `404` flag off, not found, or not owned · `422` bad `anchor_weekday` or
offset · `429` Beat cap or daily research cap (D14).

**DTOs** (`dtos/beats.py`): `DeployBeatRequest`, `BeatSummaryDTO`, `BeatDetailDTO`,
`BriefEntryDTO`, `BriefDetailDTO`, `SourceDTO`, `ReadPingRequest`. `TzOffsetMinutes` is **imported
from `dtos/progress.py`**, not re-declared — one constrained alias, one `ge=-900, le=900`, one
place for Phase 5 D3's sign convention to be wrong. `AnchorWeekday = Annotated[int, Field(ge=0,
le=6)]` is the one new alias. Mapping is explicit construction, as `_progress_dto` does.

**`entries` is one list of both kinds, not two.** A Skipped period is an entry in the rail (D2), and
splitting the payload would push the interleaving into the client — where it would be re-derived
from two arrays and their dates, which is a merge nobody should write twice.

**The read ping is first-write-wins.** `UPDATE briefs SET read_at = now() WHERE id = :id AND
read_at IS NULL`, and the same shape for `sources_seen_at`. That is
`mark_completed_and_finalize`'s guard, and it matters for the same reason: §9's north-star metric
asks *when a learner first opened a Brief*, so a re-read must not move the timestamp.

## 7. Load, caching & rate limiting

**Rate limiting.** Three checks, all on the existing `DailyRateLimiter` (D14), against
`RATE_LIMIT_BRIEF_RESEARCH_PER_DAY`'s sixth counter (backed by `beat_research_runs`, §4's
amendment) or `MAX_BEATS_PER_LEARNER`. Admins exempt, as everywhere. **Amended (docs sweep,
AL-561): this was shipped as three checks with three different shapes, not the two this section
originally described, and "never at the route" turned out not to hold for the explicit retry.**

- `check_beat_creation` — the **stock** cap (`MAX_BEATS_PER_LEARNER`, the count of live Beats, not
  a daily flow) — raises `429`, checked at `POST /beats` before the row is created.
- `brief_research_capacity_available` — **non-raising**, checked **inside the arrival drain,
  before each claim**, exactly as originally specified: the drain is a side effect of a read the
  learner did not explicitly ask to be billed for, so hitting the cap must degrade to "no research
  this time," not to a `429` on a `GET` that would break the beats list.
- `check_brief_research_retry` — **raising** `429`, checked at `POST /beats/{id}/retry`, and only
  when the Beat is actually `failed` (§6's no-op branch for any other state means a stray retry
  costs no quota unit at all). §7's original text stated the "never at the route" rule as a
  blanket rule for the whole research cap; the correct scope is narrower — it is the *drain's*
  rule, because the drain's own reasoning ("a side effect of a read the learner did not
  explicitly ask to be billed for") does not extend to an explicit `POST` the learner asked for by
  name, the `POST /paths/{id}/retry` precedent (`check_outline_generation`, checked before
  triggering, for the identical reason).

**A `GET` with a side effect** (D15) is unusual enough to defend. It is Phase 1's poll-as-trigger
verbatim — reaching a lesson kicks its generation — and it is safe here for the same three reasons:
the effect is idempotent (the claim is atomic, D3), bounded (`MAX_BEATS_PER_LEARNER` iterations of
a pure predicate), and never changes what the response says. A `GET` that returns the same body
whether or not it triggered work is cacheable-in-principle and safe to retry, which is the property
that actually matters.

**Polling** reuses `lib/polling.ts` unchanged: the beat detail query polls only while
`research_state === "researching"` and stops on any terminal state, with the existing backoff and
jitter. Nothing polls the beats *list* — a Beat that starts researching does so because this
learner's own arrival triggered it, so the client already knows.

**Cache keys** are `["beats"]`, `["beats", id]`, `["briefs", id]`. The read ping invalidates
`["beats", beatId]` so the unread count and the rail's read state move in the same interaction.
**No `staleTime` tuning and no optimistic writes** — unlike Phase 5 D10 there is no number a
learner watches increment, so optimism would buy nothing and cost a divergence.

**No new cache layer and no rate limiter on the reads.** Every query in §4 is an index scan bounded
by one learner's own history.

## 8. Frontend

Specified from PRD §3 against Nocturne tokens; **no dedicated mock**, on the Phase 5 precedent. The
surfaces deliberately rhyme with existing ones, so most of this is placement rather than invention.

**Routes**

- `routes/beats.new.tsx` — the deployment form: Topic, Level (the existing three-way control
  verbatim), **`Reports on ▾ Monday`**, optional Guidance. Primary action `Deploy analyst`. It is
  `routes/new.tsx`'s grammar with one field added, and the two are deliberately separate routes —
  a shared component with a mode flag would make the path flow carry a branch it never takes.
- `routes/beats.$beatId.tsx` — the **Beat rail** in the path rail's position and shape: flat,
  newest first, each row dated, nothing ever locked. Standing orders in one line at the head
  (`Weekly · EU AI regulation · policy and enforcement`). No month subheadings (PRD §7.1).
- `routes/briefs.$briefId.tsx` — the lesson reading surface, near-identically: title, date,
  `Builds on Brief #4` as a link, the Markdown body through **`markdown.tsx` untouched**, then the
  Sources block.
- `routes/index.tsx` — a Beats section **beside** "Your paths", never merged (PRD §4.10). A card
  reads `3 new briefs · weekly`, or `Researching… · started 30s ago` while a run is in flight.

**Components** — `beat-card.tsx`, `beat-rail.tsx`, `standing-orders.tsx`, `brief-sources.tsx`,
`builds-on-line.tsx`, `skipped-row.tsx`. Reused as-is: `markdown.tsx`, `mermaid.tsx`,
`state-card.tsx` (researching / failed / retry — the shape already exists), `lib/polling.ts`.

**The Sources block is a first-class region**, not a footnote in small grey type (PRD §3): body
text size, `elevated` surface, publisher and date beside each title, the URL a real link. It is
the part a learner checks us on, and PRD §5's **Depth of read** measures whether they reach it —
so it fires the `sources` read ping via an `IntersectionObserver`, once, on first visibility.

**A Skipped row is quiet and legible**, not an error: `mist` text, a date, one line, no flame, no
badge, no retry affordance. It must not read as a failure, because it isn't one (PRD §4.6).

**Gating** — `useFeatureFlag("analyst")` feeds the options factories' `enabled`; off means
`skipToken`, no request, no rendered surface, and no Beats section on home.

**Restraint, structurally** (PRD §3's *Never* list). There is no client scheduler, no service
worker, no notification permission request, no badge count on any app-level chrome, and the rail is
a plain list with no infinite scroll — a Beat's whole history is one bounded query. None of those
absences needs a rule, because nothing in §6 or this section can express them.

**MSW** — `mocks/beats.ts` exporting `beatHandlers`, `configureBeats({…})`, `resetBeats()`,
composed into `handlers.ts` and reset in `tests/setup.ts`, matching `mocks/flashcards.ts`.

## 9. Instrumentation & observability

**Three new events** in `events.py`, with `EVENT_FIELDS` entries (the "computable is verified, not
assumed" contract, AL-070):

| Event | Fields | Answers |
| --- | --- | --- |
| `beat_deployed` | `account_id`, `beat_id`, `beat_level`, `anchor_weekday`, `has_guidance` | The denominator for **Beat survival**; the deployment-mix datum |
| `brief_research_completed` | `account_id`, `beat_id`, `outcome` (`published`/`skipped`/`failed`/`refused`), `duration_ms`, `queries`, `documents_retrieved`, `documents_after_filters`, `findings`, `survivors`, `prompt_tokens`, `completion_tokens`, `total_tokens` | **Skip rate**, **Cost per read Brief**, **Wait tolerance**'s duration half, and D14a's verification |
| `brief_read` | `account_id`, `beat_id`, `brief_id`, `marker` (`opened`/`sources`), `age_days` | The **north star**, **Brief read rate**, **Depth of read** |

**Corrected (code-review, FIX 10): `beat_deployed`'s Level field is `beat_level`, not `level`, above.**
An earlier version of this table (and the code) used the bare `level` — structlog's `add_log_level`
processor owns that key (the log severity), and silently clobbers any field emitted under the same
name, exactly the `path_created`/`path_level` reason `services/generation.py` already documents.
`docs/metrics.md` and `events.py`'s `EVENT_FIELDS` were both corrected; this table, the spec the
ticket cites, was not — leaving it wrong here would have reintroduced the bug the next time someone
built from this document instead of the code.

`brief_research_completed` fires **however it resolved**, the shape `lesson_generation_completed`
and `tutor_reply_completed` already use — one event with an `outcome`, never a success event and a
separate failure event, because a rate needs both arms from one source.

`usage_tokens` (`services/generation.py`) supplies the three token fields unchanged. Those fields
are what make D14a's $0.50 ceiling **verified rather than asserted**: the design target is
arithmetic, the event is the measurement, and if they disagree the event wins.

**Six saved queries** in `queries/logfire/`, in the existing header style:

| Query | Question |
| --- | --- |
| `brief_return.sql` | **The north star.** Among learners with a Beat, the share of Active days whose first action is opening a Brief, and whether their Return exceeds their own pre-Beat baseline |
| `brief_read_rate.sql` | Briefs opened ÷ Briefs published |
| `brief_depth_of_read.sql` | Share of opened Briefs reaching the Sources (`marker = 'sources'` ÷ `marker = 'opened'`) |
| `brief_skip_rate.sql` | Skipped ÷ research runs, per Beat — calibrates PRD §4.6 in both directions |
| `brief_wait_tolerance.sql` | Share of researching Beats the learner is still present for when the Brief lands. **The first metric to read** (PRD §5): with prefetch deferred, the first slice waits every time, so this is the worst case rather than an average |
| `cost_per_read_brief.sql` | Dollar spend (split prompt/completion tokens plus the retrieval-call count, priced from rate constants in the query's own header, code-review FIX 3) ÷ Briefs read — the guardrail that decides viability against the $0.50 ceiling directly |

`docs/metrics.md` gains the three event rows and the six query rows, and inherits
`return_rate.sql`'s standing UTC-vs-local-day caveat.

**Beat survival** (Beats with a read Brief in 30 days) is computed from `beat_deployed` and
`brief_read` without a query of its own until there is enough history to make one meaningful —
named here so its absence is a decision.

## 10. Evals

**`brief` is the fourth `ArtifactKind`.** `brief_findings` stays deferred (PRD §7.1).

```python
APPLICABLE_ITEMS["brief"] = ("accurate", "level_appropriate", "in_scope", "continuous", "safe")
```

No new `RubricItem` — the `Literal` is shared across kinds, so adding one would change what the
outline and lesson judges are asked. PRD §6's two new dimensions map onto existing items through
`ARTIFACT_NOTES`, which exists for exactly this:

| PRD §6 dimension | Item | The `ARTIFACT_NOTES` reading |
| --- | --- | --- |
| **Grounded** | `accurate` | Every claim about the world traces to a cited Source and none exceeds what that Source supports. `CONTEXT.md`'s existing **Grounded**, pointed at a Source instead of a Read passage |
| **Delta** | `continuous` | Reports change against prior Briefs rather than re-establishing the subject. Lesson continuity prevents re-*teaching*; this prevents re-*reporting* |
| Level-appropriate, Safe | unchanged | Inherited |

`check_valid` is omitted rather than auto-passed — a Brief has no Quick check.

**Layer 1 imports the shipped functions, never a second spelling:** `cites_only_read_documents`
(D8) and `filter_new` (D9) are the pre-filters. That is the whole reason D9 is a pure `domains/`
module — the gate the product runs and the gate the harness checks are one function.

**`brief_seed_set.yaml`** — four cases over subjects that genuinely move, each pinned to a
**recorded retrieval fixture** and each carrying a synthetic prior Brief (claims + Source URLs) so
`continuous` and the novelty gate have something to be a delta *of*. A seed set without prior
Briefs would test the first Brief forever, which is the one Brief the phase's central claim does
not describe.

**The fixtures** (`evals/fixtures/retrieval/*.yaml`) are keyed on the Beat and record the query
plan beside the results, recorded by an opt-in `just record-retrieval-fixtures` that hits Exa once
and writes the file:

```yaml
beat: eu-ai-regulation-some-experience
queries: ["…", "…", "…"]        # what the planner emitted at record time
results:
  "…": [ …documents… ]
```

Replay executes `queries` and never re-derives them. Today that is redundant — the planner is pure,
so the recorded queries and the live ones are identical — and it is written down anyway, because it
is what makes D6a's named upgrade (a model query-proposer) additive instead of a re-record (D10).

**Honest limit:** a fixture freezes the world on the day it was recorded, so these evals measure
the *agent* and never our recall. Whether retrieval surfaces the right documents at all is a
production question, answered by **Skip rate** and by reading Briefs — not by this harness. Naming
that is the point of PRD §6's new constraint.

Opt-in as always: `just evals`, never `just gate` or the CI gate.

## 11. Testing strategy

Red-green TDD, fakes over mocks (CLAUDE.md).

**Unit — `tests/unit/test_cadence.py`** (pure, and the cheapest place to buy confidence):

| Case | Expected |
| --- | --- |
| No entries | Claimable, on any day |
| Last entry yesterday, anchor is today | Claimable |
| Last entry today, anchor is today | **Not** claimable — the floor is strictly after the last entry |
| Last entry six weeks ago | Claimable **once**; one run, not six (W32) |
| Last entry is a Skipped row | Floor resets exactly as a published one does (PRD §4.6) |
| Every weekday as anchor × every weekday as today | 49 combinations, table-driven — the off-by-one lives here |

**Unit — `tests/unit/test_novelty.py`:** a finding whose every URL was cited before is dropped; a
finding with one new URL survives; a claim restating a prior claim in new words is dropped; an
empty result is the Skipped signal; ordering and duplicates cannot change the outcome.

**Unit — `tests/unit/test_retrieval.py`:** the plan is deterministic for fixed orders; dedupe by
URL; undated documents dropped; **the budget is never exceeded** and short documents' unused share
is redistributed; truncation is a prefix, so two runs of the same fixture are byte-identical; and
**a `FixtureRetriever` miss raises rather than returning `[]`** — the test that stops a stale
fixture from manufacturing a Skipped entry (§5.2).

**Unit — the agents' validators:** a finding citing an unread URL raises `ModelRetry`; a Brief with
no `cited_urls` raises; a Brief citing outside its Deps raises; a `SkippedNote` raises when
survivors exist. Plus **the padding test**, which is the phase's signature case: with empty
`documents` in Deps, *no* `BriefBody` can pass validation (§5.4).

`tests/unit/test_agents_layering.py` covers the two new agents for free.

**Integration (real Postgres) — `tests/integration/test_beats_api.py`, `test_briefing.py`,**
with `@pytest.mark.workflow(…)` on the relevant cases:

- **W29** deploy → the first run is claimed in the same request → a Brief with resolving Sources.
- **W30** a second Brief cites the earlier Brief and re-reports none of its claims.
- **W31** no novel findings → a `skipped` row, no body, `number IS NULL`, and the next arrival does
  not immediately re-research.
- **W32** a Beat left claimable for several anchor days produces **one** Brief.
- **W33** retrieval unavailable → `failed`, visible, retryable, and **no Brief row exists** — the
  test that pins "never an uncited essay".
- Two concurrent arrivals claim once (the atomic claim, D3); a `failed` Beat is **not** re-claimed
  by an ordinary arrival and **is** by `POST /retry` (the retry-burn guard).
- Another learner's Beats are never reachable (404-never-403).
- The `CHECK` constraints reject a bodied skip and a numberless publish (§4).
- Caps → `429`; flag off → `404`; a re-read does not move `read_at`.
- `test_schema.py` sees the three tables and their constraints; `test_migrations.py` runs `0012`
  up and down.

**Frontend unit (vitest + MSW):** the rail interleaves published and skipped rows by date; a
Skipped row renders no retry and no error styling; the Sources block renders and fires the
`sources` ping exactly once; `Builds on Brief #4` links; the researching card polls and stops on a
terminal state; flag off → no request.

**E2E (Playwright, `mobile-390x844`)** — the two journeys PRD §7.1 keeps:

- **`w29.spec.ts` (`@w29`)** — deploy an analyst, wait through `Researching…`, read a Brief, assert
  the Sources block is present and its links resolve to the stub's URLs.
- **`w31.spec.ts` (`@w31`)** — a Beat whose topic carries `[force-no-findings]` publishes a
  **Skipped** row: dated, one line, no body, and no retry affordance.

**Stub sentinels** in the topic string, on the Phase 1 precedent: `[force-retrieval-failure]`
(→ W33's branch), `[force-no-findings]` (→ Skipped).

**Amended (docs sweep, AL-561): what `[force-no-findings]` actually forces.** This section
originally said the sentinel "returns documents the gate rejects" — i.e. that it would drive
`domains/novelty.py::filter_new` to reject findings against prior cited URLs/claims. The shipped
mechanism is narrower, for a reason that was structural rather than an oversight: `w31.spec.ts`
is a Beat's **first-ever** research run (a fresh deploy, exactly like `w29.spec.ts`), and a first
run has no prior Brief — no earlier-cited Source URLs, no earlier claims — so there is nothing for
the novelty gate to reject *against*. Genuinely exercising the gate's rejection branch needs a
*second* run on the same Beat, and that is **W30**, an integration case per PRD §7.1's own table
(§11 above), never a Playwright journey. So instead, `[force-no-findings]` makes
`services/stub_model.py`'s researcher dispatch report **zero `Findings`** from documents the run
genuinely, non-emptily retrieved (`StubRetriever` still returns real-looking, dated stub
documents for every query — "not zero documents, a stub returning nothing would prove the
easier, wrong thing" still holds exactly as stated below: zero *documents* is `services/
briefing.py`'s own load-bearing *failed* row, §5.7's second row, and would prove nothing about
Skipped). The mechanism still lands on §5.7's third row (idle + a `skipped` row) — which is
precisely what `w31.spec.ts` asserts — it just gets there via the researcher finding nothing
worth reporting rather than via the gate rejecting something it found. See
`services/retrieval.py::StubRetriever`'s own docstring for the full reasoning.

**External — `tests/external/test_exa.py`, `@pytest.mark.external`:** one live contract test
asserting Exa returns document text and a `publishedDate`, and that `since` filters. This is what
`tests/external/` is for: PRD §4.4's three requirements are a *contract with a vendor*, and a
fixture can never notice when that contract changes.

## 12. Deployment & ops

**One new secret.** `EXA_API_KEY` joins `docs/deploy.md`'s **Required** table beside
`OPENROUTER_API_KEY`, with the same note shape: startup succeeds without it, but research — the
feature — cannot run, and a Beat's runs fail visibly rather than publishing uncited (PRD §4.4).

**Migration `0012` is additive** — three new tables, two new enums, nothing touched on an existing
table. Online-safe on Neon at any size, because no existing row is read or rewritten. Rollback is
dropping three tables nothing else references. **`0013_beat_research_runs` followed it** (§4's
amendment) — one more additive table, same online-safety and rollback shape, adding the daily
research cap's real counter.

**No `fly.toml` change.** `min_machines_running = 0` and `auto_stop_machines = 'stop'` stay exactly
as they are — PRD §4.2's central design outcome, and the reason this phase needs no second
deployment artifact, no external cron and no public trigger endpoint. Flipping to always-on remains
one config line, with PRD §4.8's hibernation rule as its price.

**Launch** follows the flagged-phase runbook
([`deploy.md`](../deploy.md#launching-a-flagged-phase-al-270--al-370)) — the fifth flag through
`tutor`/`shaping`/`streaks`/`flashcards`'s playbook: dark through the build-out, `ADMIN_DEFAULT_FLAGS`
for dogfooding, then one committed `FEATURE_FLAG_DEFAULTS` entry. The kill switch stays registered
afterward, and it matters more here than in any prior phase: it is the only thing that stops spend
in one action.

**Read the cost guardrail before the flip.** `cost_per_read_brief.sql` and
`brief_wait_tolerance.sql` are dogfood gates, not post-launch curiosities — the first says whether
the economics work, the second whether PRD §4.2's accepted tradeoff is survivable at its worst
case. Neither can be answered before real Briefs exist, which is why the live adapter ships in this
slice.

## 13. Configuration

| Setting | Default | Notes |
| --- | --- | --- |
| `EXA_API_KEY` | — | Required secret (§12). No default, never committed |
| `MODEL_RESEARCH` | `anthropic/claude-sonnet-5` | D7. In `MODEL_SLOTS`, so the production stub guard covers it |
| `MODEL_BRIEF` | `anthropic/claude-sonnet-5` | D7. Likewise |
| `MAX_BEATS_PER_LEARNER` | `3` | PRD §4.7's cap, as config not a constant |
| `RATE_LIMIT_BRIEF_RESEARCH_PER_DAY` | `5` | D14. Admins exempt |
| `MAX_CONCURRENT_BRIEF_RESEARCH` | `2` | D14 — its own pool, against generation's `8` |
| `BRIEF_RETRIEVAL_MAX_QUERIES` | `6` | D6a's plan size |
| `BRIEF_RETRIEVAL_MAX_DOCUMENTS` | `12` | D14a |
| `BRIEF_RETRIEVAL_TEXT_BUDGET_CHARS` | `160_000` | **D14a's real ceiling** — ≈40k tokens, the one number that binds cost |
| `BRIEF_RESEARCH_TIMEOUT_SECONDS` | `180` | Longer than `generation_timeout_seconds` (60): two model calls plus retrieval |
| `BRIEF_RESEARCH_STALE_AFTER_SECONDS` | `420` | Must exceed the timeout, as `config.py` already validates for generation |
| `FeatureFlag.ANALYST` | `False` in `FLAG_DEFAULTS`, in `ADMIN_DEFAULT_FLAGS` | D12 |

`BRIEF_RETRIEVAL_TEXT_BUDGET_CHARS` is the knob to move if the ceiling is wrong. Every other number
here bounds *how often*; only that one bounds *how much*.

## 14. Corrections to the PRD

Each is a place the shipped design differs from what the PRD says, recorded so the difference is a
record rather than a quiet rewrite. A reader holding the PRD needs the mapping.

| # | Correction | Where |
| --- | --- | --- |
| **R1** | **The workflows are W29–W33, not W28–W32.** PRD §5's "W28 is the next free number; W1–W27 are taken" was true when written; AL-410 then shipped `w28.spec.ts` (kept-card management). W29 = a cited Brief, W30 = the second builds on the first, W31 = Skipped not padded, W32 = one Brief not a backlog, W33 = retrieval failure never uncited | D16, §11 |
| **R2** | **Retrieval is deterministic, not tool-using.** PRD §4.4 describes research as "tool-using"; the queries are derived by a pure function of the Beat's frozen orders and executed by the service. The split the PRD actually argues for is kept in full. The reason is fixture stability: a query proposed by a model makes the fixture key a model output, and PRD §6 makes recorded fixtures the thing this phase cannot ship or boot without | D6a, §5.2 |
| **R3** | **A Brief's date is stamped at publication, not derived per request.** PRD §4.2's "the arrival carries it" implies deriving a local date the way the streak does. A Brief is an immutable dated record (§4.1) whose date is part of its content, so deriving it would let a learner's travel move a published document's date and the cadence floor with it | D4a, §4 |
| **R4** | **The reconciler gets no Beats scan at all.** PRD §4.2 lists it as trigger 2 ("one more scan drains claimable Beats for free") and then removes it four paragraphs later ("the reconciler does not deliver anchored Beats at all"). This TDD takes the later position to its conclusion: with failed runs deliberately un-retried and stale claims recovered by the next arrival, a scan has no work left, so ~~`services/lifecycle.py` changes only to bind the second semaphore~~ — **amended (docs sweep, AL-561): see D5's amendment.** `lifecycle.py` also constructs a process-lifetime `ExaRetriever` and binds it into `briefing_service` via `bind_runtime`'s new `retriever` parameter — the AL-521/AL-523 handoff-gap fix. The reconciler claim this row makes (no Beats scan) stands unchanged | D5, §3 |
| **R5** | **Skipped entries are unnumbered.** PRD §3's own example line reads "Nothing material since Brief #4" — referencing the last Brief, carrying no number — but the PRD never states the rule. `number` is sparse over published Briefs only, under a partial unique index | D2, §4 |
| **R6** | **PRD §5's Depth of read needs a signal the PRD does not name.** "Share of opened Briefs where the learner reaches the Sources" is not derivable from a read timestamp, so `briefs.sources_seen_at` and the `marker` field on the read ping exist to carry it | §4, §6, §9 |

## 15. Risks & open questions

- **Retrieval quality is this phase's single point of failure, and D6a makes it worse before it
  makes it better.** A template asks a subject one way where a model would ask three. Every
  integrity guarantee in this design has the form *don't exceed your inputs* — the researcher cites
  only its Deps, the analyst only what the researcher read, Source metadata is never model-written.
  Not one of them is *get good inputs*. Hand the pipeline three tangential documents and it will
  faithfully, with perfect provenance, report on three tangential documents.

  **What makes it dangerous is that the rigor disguises it.** A Brief built on three thin sources
  looks exactly as trustworthy as one built on eight good ones: same citations, same resolving
  links, same real dates, same confident voice. A learner has no signal — and the damage is
  asymmetric, because the one time they hear the week's real story elsewhere, it is not a quality
  complaint, it is the premise failing. There is also no ground truth available anywhere in the
  system: recall cannot be computed without knowing what was missed, and §10's fixtures freeze the
  world, so the harness measures the agent and never our recall.

  **The instrument is the funnel, not Skip rate alone.** `brief_research_completed` carries
  `documents_retrieved`, `documents_after_filters`, `findings` and `survivors` (§9) precisely so the
  cases separate — the table below **corrected (code-review, FIX 4)** to include
  `documents_after_filters`: the original two-column version (`documents_retrieved`, `findings`)
  could not tell a genuine retrieval PRECISION miss (chum reached the researcher) from a
  RECALL-shaped one `retrieve()`'s own dedupe/dated/non-empty filters manufactured out of a raw
  count that looked healthy — `documents_after_filters` is emitted for exactly this and, until the
  fix, was read by no query:

  | `documents_retrieved` | `documents_after_filters` | `findings` | `survivors` | Reading |
  | --- | --- | --- | --- | --- |
  | healthy | healthy | healthy | 0 | **A genuinely quiet week.** The gate working |
  | **low** | **low** | low | 0 | **Retrieval recall** — we found little to read |
  | healthy | **low** | low | 0 | **Recall-shaped, but the filters did it** — `retrieve()`'s own dedupe/dated/non-empty filters ate the batch, not the source |
  | healthy | healthy | **low** | 0 | **Retrieval precision** — we read plenty, it was chum |
  | healthy | healthy | healthy | healthy | Working |

  Raw Skip rate cannot tell those apart; the funnel can — read a Beat's own Skipped-side averages
  against that SAME Beat's Published-side averages (`brief_skip_rate.sql`'s `_when_published`
  columns, FIX 4), since a global sense of "healthy" does not substitute — retrieval volume varies
  by subject. What none of this can do is name the query that *would* have worked, or notice
  retrieval confidently returning good documents about the wrong
  half of a subject. **That case has one detector: a person reading the week's news on a Beat's
  topic and comparing.** It is a dogfooding ritual (AL-570), not a dashboard — named here so the
  funnel query is not mistaken for coverage it does not provide.
- **The novelty gate is calibrated on nothing.** Its thresholds are guesses until Briefs exist, and
  it fails silently in both directions — too strict looks like a quiet subject, too loose looks
  like a working feature that nobody re-opens. This is the risk PRD §4.6 names as the one that
  kills the feature, and the only honest mitigation is that the gate is pure, so re-calibrating is
  a constant and a unit test rather than a prompt archaeology session.
- **The $0.50 ceiling assumes today's prices.** D14a's dollar figures are arithmetic over current
  Sonnet-class and Exa pricing; what is *enforced* is a character budget. If either price moves,
  the budget is still enforced and the ceiling silently is not. `cost_per_read_brief.sql` is what
  notices, and it should be read before the flag flip rather than after.
- **Wait tolerance is measured at its worst case.** With prefetch deferred, every first-slice Brief
  is researched while the learner waits (PRD §4.2's accepted cost). If learners consistently leave
  and never come back, the fix order is prefetch first, always-on second — and always-on brings
  PRD §4.8's hibernation problem back with it.
- **Fixtures freeze the world.** The evals measure the agent, never our recall (§10). Nothing in
  the harness can tell us we are missing the story.
- **Open: does the analyst voice survive contact with a real week?** PRD Appendix A is aspirational
  — a worked example written to be argued with, not an observed output. The separation of fact from
  interpretation ("Expect that framing…" visibly being the analyst talking) is the hardest thing in
  the rubric and the least mechanical. If Briefs read like summaries rather than reporting, that is
  a prompt problem to solve before a scope problem to expand.
- **Open: is `MAX_BEATS_PER_LEARNER = 3` right?** It bounds cost but also bounds the Beats section
  on home, which PRD §4.7 argues is what makes multiple Beats worth building. Three is a guess that
  dogfooding should move in one direction or the other.
- **Open: does the Beat rail want grouping sooner than PRD §7.1 thinks?** Its trigger is "when a
  Beat's rail no longer fits on one screen", which at weekly cadence is about ten weeks after
  launch — sooner than the phase will be judged.
- **Settled, and worth stating as a standing rule:** reading a Brief never touches **Activated
  learner** (PRD §4.9, §2's point 3). Nothing in §6 or §9 can express it — `brief_read` is not in
  the activation query's event list, and adding it would be a deliberate act.

## 16. Tickets

**Cut: [epic #163](https://github.com/mattjmcnaughton/aleph/issues/163)** — seventeen tickets,
label `tdd-analyst`. **The issues are the source of truth** (the Phase 1 / 2 / 2B / 3 / 5 pattern);
the epic carries the shared context, the working conventions and the dependency graph, and each
ticket is a pointer into this document plus acceptance criteria. Where a ticket and this TDD
conflict, the TDD wins.

| Ticket | Scope |
| --- | --- |
| [AL-500](https://github.com/mattjmcnaughton/aleph/issues/164) | Accept the PRD, cross-link this TDD, land §14's six corrections **in the PRD itself**. First — the vocabulary is authoritative |
| [AL-501](https://github.com/mattjmcnaughton/aleph/issues/165) | Config: the two model slots, §13's settings block, `FeatureFlag.ANALYST` |
| [AL-502](https://github.com/mattjmcnaughton/aleph/issues/166) 👤 | Exa account + `EXA_API_KEY` + the `deploy.md` Required row |
| [AL-510](https://github.com/mattjmcnaughton/aleph/issues/167) | `domains/cadence.py` + `domains/novelty.py`. Pure, mergeable alone |
| [AL-511](https://github.com/mattjmcnaughton/aleph/issues/168) | Migration `0012`, three models, both repositories, the claim pair, the `CHECK`s |
| [AL-512](https://github.com/mattjmcnaughton/aleph/issues/169) | The retrieval seam: `Protocol`, `build_query_plan`, `retrieve()`, `Fixture` + `Stub`. **Not** Exa — testable without a key |
| [AL-520](https://github.com/mattjmcnaughton/aleph/issues/170) | Both agents + their validators, including the padding test |
| [AL-521](https://github.com/mattjmcnaughton/aleph/issues/171) | `services/briefing.py` — drain, claim, pipeline, failure mapping, the second semaphore. **The correctness heart** |
| [AL-522](https://github.com/mattjmcnaughton/aleph/issues/172) | DTOs, router, rate limits, `docs/api.md` |
| [AL-523](https://github.com/mattjmcnaughton/aleph/issues/173) | `ExaRetriever` + `tests/external/test_exa.py` |
| [AL-530](https://github.com/mattjmcnaughton/aleph/issues/174) | Frontend: deploy form, Beat rail, home section, MSW |
| [AL-531](https://github.com/mattjmcnaughton/aleph/issues/175) | Frontend: Brief surface, Sources block, read pings |
| [AL-540](https://github.com/mattjmcnaughton/aleph/issues/176) | Three events, `EVENT_FIELDS`, the six saved queries, `docs/metrics.md` |
| [AL-550](https://github.com/mattjmcnaughton/aleph/issues/177) | Evals: the `brief` kind, the fixture recipe, `brief_seed_set.yaml` |
| [AL-560](https://github.com/mattjmcnaughton/aleph/issues/178) | Stub sentinels + Playwright W29 / W31 |
| [AL-561](https://github.com/mattjmcnaughton/aleph/issues/179) | Docs verification sweep |
| [AL-570](https://github.com/mattjmcnaughton/aleph/issues/180) 👤 | Dogfood, the two guardrail queries, the retrieval-quality ritual, **flag flip (launch)** |

**Serialized spine:** AL-511 → AL-521 → AL-522 → AL-530 → AL-531 → AL-560 → AL-561 → AL-570.
Six day-one tickets share no files (AL-500, AL-501, AL-502, AL-510, AL-511, AL-512). Only one
`for-human` ticket blocks anything, and it blocks only AL-523. Launch is gated on §12's two
guardrail queries and on AL-570's manual retrieval-quality comparison — the one check no dashboard
covers (§15).

**Rough size:** ~1 400 lines of production code and ~1 200 of tests, split roughly evenly between
the backend pipeline and the frontend. Larger than Phase 5 and comparable to Phase 3 — the new
surface is the retrieval seam and its three adapters, not the machinery around it, which is
inherited almost entire.

## Appendix — traceability (PRD's TDD-owned items)

| PRD delegation | Here |
| --- | --- |
| Schema (§ preamble) | §4, D1, D13 |
| The claim protocol (§ preamble, §4.2) | D3, §5.6 — `_generation.py` imported unchanged |
| **Which retrieval provider** (§ preamble, §4.4) | D6 — Exa, behind a `Protocol`, with the three product constraints as the acceptance test |
| How findings are deduplicated against prior Briefs (§ preamble, §4.5) | D9, §5.4 — pure, and the same function the evals import |
| The API and instrumentation (§ preamble) | §6, §9 |
| A Beat is a standing assignment; a Brief is immutable (§4.1) | §4 — no update path, `CHECK`s, and the numbering |
| A Brief's period is "since the last Brief" (§4.1) | D4, §5.1 — and W32 falls out of `>=` |
| Whoever shows up drives the work (§4.2) | D15, §5.6, §7's `GET`-with-side-effect defence |
| Never silently skipped for infrastructural reasons (§4.2) | §5.7's second row; §5.2's raising fixture; the `outcome` field on `brief_research_completed` |
| No deployment change (§4.2) | §12 — `fly.toml` untouched |
| Level and Guidance (§4.3) | §4's columns, §5.3's Deps |
| Every claim traces to a Source (§4.4) | D8, §5.5 — a validator, plus Source metadata never model-written |
| A Brief with no Sources is not publishable (§4.4) | §5.5's validator; §5.7 maps it to `failed` |
| Brief continuity (§4.5) | §4's `claims` + `brief_sources`, D9, §10's seed set with prior Briefs |
| Nothing to report is first-class (§4.6) | D2, §4's `CHECK`s, §5.4 — and the padding test in §11 |
| Multiple Beats, with a cap (§4.7) | D14, §7, §13 |
| Cost bounded by attention (§4.8) | D15 — a Beat nobody opens is never claimed, so it is never billed |
| Reading a Brief is an Active day (§4.9) | D11 — the column and the event ship; the streak union stays deferred |
| Activation stays lesson-based (§4.9) | §15's standing rule — `brief_read` is absent from the activation query |
| A Beat is not a path (§4.10) | §8 — separate routes, separate home section, no merged list |
| Weekly only, learner-picked Anchor day (§4.11) | §4's `anchor_weekday`, §5.1, §6's `cadence` constant |
| Success metrics (§5) | §9's six queries |
| Evals and the fixture constraint (§6) | §10, D10 |
| The MVP boundary (§7.1) | Honoured throughout; each deferral named with its re-entry cost |
