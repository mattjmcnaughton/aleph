"""Briefing: the arrival drain, the claim, the research pipeline (TDD §3/§5.6).

This is AL-521 — **the phase's correctness heart** (epic #163). It is
``services/generation.py``'s pattern, one workload over (TDD §2's extension
map: "a new ``services/briefing.py`` in the same shape rather than a sixth
concern on a 1 250-line class"), never its object: :class:`BriefingService`
owns claim -> run -> persist -> (no emit yet, AL-540) for one Beat's research
run, exactly the shape ``GenerationOrchestrator``/``FlashcardDraftingService``
already establish twice.

**The two entry points, and how they compose (TDD §3, §5.6).**

* :meth:`BriefingService.drain_claimable` — the arrival trigger (D15). Reads
  the caller's own ``session`` (a side effect of a read the learner already
  wanted — listing or opening Beats), derives ``local_today`` **once** (D5,
  the single owner of "today" — the ``services/progress_read.py`` seam,
  reused), evaluates :func:`aleph.domains.cadence.is_claimable` per Beat
  (bounded by ``MAX_BEATS_PER_LEARNER``), checks the research cap
  (non-raising — see :func:`aleph.services.rate_limit.DailyRateLimiter.
  brief_research_capacity_available`), and spawns :meth:`run_research` through
  the injected ``spawn`` seam (``TaskRegistry``, unchanged) for every Beat
  that clears both gates.
* :meth:`BriefingService.run_research` — the **already-claiming** pipeline
  target (mirrors ``run_outline_task``'s shape: the permit is acquired
  *before* the claim, and the claim happens *inside* this method, not before
  it is spawned). This is what makes two concurrent arrivals safe with no
  fence to thread through the spawn boundary: both ``drain_claimable`` calls
  may decide the same Beat is claimable and both may spawn
  ``run_research(beat_id, local_today)`` — the atomic ``UPDATE ... WHERE
  ...`` inside it (reused unchanged from ``repositories/beats.py``, itself
  built on ``repositories/_generation.py``) lets exactly one win, and the
  loser's call is a silent no-op. ``local_today`` rides the spawn (D4a): the
  Brief's ``published_on`` is the date the *arrival* decided, never one a
  background frame would have to re-derive.

**The pipeline inside one claimed run** (TDD §3's pseudocode, §5.3-§5.5):
plan (pure, ``services/retrieval.py::build_query_plan``) -> retrieve (I/O,
``services/retrieval.py::retrieve`` — **the only path from a ``Retriever`` to
a model**, see ``tests/unit/test_briefing_service.py``'s guard test) -> find
(model, ``agents/researcher.py``) -> gate (pure,
``domains/novelty.py::filter_new``) -> write (model, ``agents/analyst.py``)
-> validate (pure, the agents' own layer-2 validators, already run inside
``agent.run``) -> persist.

**Failure mapping is TDD §5.7's table, implemented row by row** — see each
branch's inline comment below; the load-bearing row is "zero documents after
the §5.2 filters -> failed, never Skipped", which is checked as its own
branch **before** the researcher is ever called (§5.7: "we found nothing to
read" is not "nothing happened").

**Two inherited contracts, satisfied here (not restated as new rules).**

1. ``AnalystDeps`` enforces at construction that every URL in every
   survivor's ``source_urls`` has a matching ``RetrievedDocument`` in
   ``documents``. Satisfied by filtering *this run's* retrieved documents to
   exactly the URLs any survivor cites (:func:`_documents_for_survivors`) —
   never a narrower "only new" set, and never the researcher's full batch
   unfiltered.
2. ``SkippedNote.detail`` is a sentence fragment; the service templates the
   "Nothing material since Brief #N" clause and owns the join
   (:func:`_render_skip_line`) — the Brief *number* never comes from the
   model (a model-invented "#4" is a provenance error nobody would catch).

**A Source's metadata is never model-written (TDD §5.5).** The writer emits
``cited_urls`` only; :func:`_materialize_sources` joins publisher/title/date
from the *retrieved* ``RetrievedDocument``s by URL, never from anything the
model said in prose — this is what the adversarial provenance test
(``tests/integration/test_briefing.py``) exercises directly.

**Open threads (a reading, recorded because the TDD does not spell one out).**
``AnalystDeps.open_threads`` has no dedicated schema column — §5.4 names only
that it "carries forward from prior Briefs". The only structurally available
candidate is the Beat's flattened prior claims
(``BriefRepository.prior_claims_for_beat``, D9's own novelty-gate input), so
this service passes the identical list to both the gate and the writer. If a
future ticket wants a narrower "still open" signal, this is the seam to
sharpen; nothing here assumes a stronger contract than "every claim ever
published on this Beat".
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from aleph.agents.analyst import (
    AnalystDeps,
    SkippedNote,
    build_analyst_agent,
    build_analyst_prompt,
)
from aleph.agents.outline import Refusal
from aleph.agents.researcher import (
    ResearcherDeps,
    build_researcher_agent,
    build_researcher_prompt,
)
from aleph.config import settings as global_settings
from aleph.db import new_session
from aleph.domains.cadence import is_claimable
from aleph.domains.novelty import filter_new
from aleph.repositories import BeatRepository, BriefRepository, NewSource
from aleph.services.generation import AGENT_LEVEL
from aleph.services.openrouter import resolve_model
from aleph.services.rate_limit import build_daily_rate_limiter
from aleph.services.retrieval import (
    RetrievalUnavailableError,
    build_query_plan,
    retrieve,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Coroutine, Sequence
    from contextlib import AbstractAsyncContextManager
    from datetime import date
    from typing import Any

    from pydantic_ai.models import Model
    from sqlalchemy.ext.asyncio import AsyncSession

    from aleph.agents.researcher import Finding, RetrievedDocument
    from aleph.config import Settings
    from aleph.models import Level
    from aleph.services.retrieval import Retriever

    SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
    Spawn = Callable[[Coroutine[Any, Any, Any]], Any]
    ResolveModel = Callable[[str], Model]
    # The seam ``services/lifecycle.py`` wraps to enforce D14's OWN bound
    # (``MAX_CONCURRENT_BRIEF_RESEARCH``) — never ``max_concurrent_generations``
    # (``GenerationOrchestrator``'s own ``ModelSlot``, a *different* semaphore
    # so research can never starve lesson generation). Same shape otherwise:
    # entering the context manager for the span of one claim + pipeline run.
    ModelSlot = Callable[[], AbstractAsyncContextManager[Any]]

logger = structlog.get_logger(__name__)

# Learner-facing failure text (``services/generation.py``'s advisory 9
# precedent verbatim): never the raw provider/exception text, which is logged
# with full context instead. Stored on ``beats.research_error`` and, per the
# TDD §6 payload sketch, never rendered on the wire beyond a generic label —
# kept generic anyway, the same discipline every pipeline in this codebase
# follows for its DB-column error text.
_RETRIEVAL_FAILED_MESSAGE = "Couldn't reach sources. Please retry."
_NO_DOCUMENTS_MESSAGE = "Couldn't reach sources. Please retry."
_RESEARCH_TIMEOUT_MESSAGE = "Research timed out. Please retry."
_RESEARCH_FAILED_MESSAGE = "Research failed. Please retry."


class _UnconfiguredRetriever:
    """The default ``Retriever`` until AL-523's ``ExaRetriever`` ships.

    Constructing :class:`BriefingService` must do no I/O and never fail — the
    ``generation_orchestrator``/``flashcard_drafting_service`` precedent — so
    this is what keeps the module-level singleton importable before a live
    retrieval adapter exists. Calling it raises immediately rather than
    returning ``[]``: a silent empty result here would be indistinguishable
    from "genuinely no results", which is exactly the conflation
    ``services/retrieval.py``'s own "a miss raises" rule (§5.2) exists to
    prevent — extended here to "no adapter is even wired". A caller (a test,
    or AL-522/523's eventual production wiring) always passes a real
    ``retriever=`` explicitly.
    """

    async def search(self, queries: Sequence[str]) -> list[RetrievedDocument]:
        del queries
        msg = (
            "BriefingService has no live Retriever configured — AL-523's "
            "ExaRetriever has not shipped yet. Construct "
            "BriefingService(retriever=...) explicitly (a FixtureRetriever/"
            "StubRetriever in tests, ExaRetriever in production once it lands)."
        )
        raise RetrievalUnavailableError(msg)


@dataclass(frozen=True)
class _ResearchContext:
    """Everything one claimed research run needs, loaded from the DB once."""

    account_id: uuid.UUID
    topic: str
    guidance: str | None
    level: Level
    model_research: str | None
    model_brief: str | None
    last_entry_on: date | None
    prior_urls: frozenset[str]
    prior_claims: tuple[str, ...]
    latest_published_number: int | None


def _local_today(tz_offset_minutes: int, now: datetime | None) -> date:
    """The arrival's local day (D5) — ``services/progress_read.py``'s exact
    arithmetic, reused rather than re-derived: ``(now - offset).date()``.
    """
    resolved_now = now if now is not None else datetime.now(UTC)
    return (resolved_now - timedelta(minutes=tz_offset_minutes)).date()


def _render_skip_line(prior_published_number: int | None, detail: str) -> str:
    """Render a Skipped rail line from the templated clause + the model's
    fragment (TDD §5.4, ``agents/analyst.py``'s ``SkippedNote`` contract).

    ``detail`` is a sentence *fragment* — no leading capital, no terminal
    period — meant to continue after an em dash; the Brief *number* the
    templated clause names is never model-written (a hallucinated "#4" is a
    provenance error nobody would catch), so it is threaded in here from
    ``BriefRepository.latest_published`` instead.

    Four cases, matching the class docstring exactly:

    - A prior published Brief exists and ``detail`` is non-empty: joined with
      " — " ("Nothing material since Brief #4 — the consultation is still
      open").
    - A prior published Brief exists and ``detail`` is empty: the templated
      clause alone, with no dangling " — ".
    - No prior published Brief (a first-ever run that skips) and ``detail``
      is non-empty: ``detail`` alone — there is no Brief number to name.
    - Neither: the empty string. ``briefs.skip_line`` is ``NOT NULL`` (the
      ``ck_briefs_skipped_shape`` CHECK), so "nothing to say" renders as
      ``""``, never ``NULL``.
    """
    prefix = (
        f"Nothing material since Brief #{prior_published_number}"
        if prior_published_number is not None
        else ""
    )
    if prefix and detail:
        return f"{prefix} — {detail}"
    return prefix or detail


def _documents_for_survivors(
    documents: Sequence[RetrievedDocument], survivors: Sequence[Finding]
) -> list[RetrievedDocument]:
    """The ``AnalystDeps.documents`` this run's survivors are allowed to cite.

    **Inherited contract 1** (earlier tickets' reviews): ``AnalystDeps``
    enforces at construction that every URL in every survivor's
    ``source_urls`` has a matching ``RetrievedDocument``. Satisfied here by
    filtering *this run's* retrieved ``documents`` down to exactly the URLs
    any survivor cites — never the researcher's full batch unfiltered (which
    would let the analyst cite a finding the novelty gate dropped) and never
    a narrower "only new" set (which would raise: the researcher's own
    validator means a finding can only cite what it read *this* run, so a
    surviving finding's URLs are always a subset of ``documents`` — including
    a URL cited before, if the finding also cites a genuinely new one, per
    ``domains/novelty.py``'s "every URL already cited" — not "any" — rule).
    """
    survivor_urls = {url for finding in survivors for url in finding.source_urls}
    return [document for document in documents if document.url in survivor_urls]


def _materialize_sources(
    cited_urls: Sequence[str], documents: Sequence[RetrievedDocument]
) -> list[NewSource]:
    """``brief_sources`` rows, joined from the *retrieved* documents by URL.

    **A Source's metadata is never model-written** (TDD §5.5): the writer's
    ``BriefBody.cited_urls`` are bare URLs, and publisher/title/publication
    date are read off the matching ``RetrievedDocument`` here — never parsed
    from anything the model said in prose. A cited URL with no matching
    document (structurally unreachable given ``AnalystDeps``'s own
    construction-time invariant, TDD §5.4/§5.5) or a document ``retrieve()``
    somehow left undated (also unreachable: §5.2 drops undated documents
    before any model sees them) is skipped defensively rather than raised —
    this function never invents a Source's metadata from nothing.
    """
    by_url = {document.url: document for document in documents}
    sources: list[NewSource] = []
    for url in cited_urls:
        document = by_url.get(url)
        if document is None or document.published_on is None:
            continue
        sources.append(
            NewSource(
                url=document.url,
                publisher=document.publisher,
                title=document.title,
                published_on=document.published_on,
            )
        )
    return sources


class BriefingService:
    """Drives one Beat's research run through claim -> pipeline -> persist.

    Constructed with injectable seams — ``session_factory``, ``spawn``,
    ``resolve_model_fn``, ``retriever``, ``config`` — exactly
    ``GenerationOrchestrator``/``FlashcardDraftingService``'s shape (TDD §2:
    "reuse the pattern, not the code"), so tests swap ``_spawn`` for a
    drainable collector, ``_resolve_model`` for the deterministic stub, and
    ``_retriever`` for a ``FixtureRetriever``/``StubRetriever``/fake, the same
    way ``tests/integration/conftest.py``'s ``CollectingSpawn``/``stub_resolver``
    already do for lesson generation.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory = new_session,
        spawn: Spawn = asyncio.create_task,
        resolve_model_fn: ResolveModel = resolve_model,
        config: Settings = global_settings,
        retriever: Retriever | None = None,
        research_timeout_seconds: float | None = None,
        stale_after_seconds: float | None = None,
        model_slot: ModelSlot = contextlib.nullcontext,
    ) -> None:
        self._session_factory = session_factory
        self._spawn = spawn
        self._resolve_model = resolve_model_fn
        self._config = config
        # The runtime seams ``services/lifecycle.py`` rebinds in the app
        # lifespan (mirrors ``GenerationOrchestrator.bind_runtime``): the
        # spawn (-> the SAME ``TaskRegistry`` generation uses, TDD §2's
        # "the registry ... reused as-is") and the model slot (-> D14's OWN
        # semaphore, never generation's). Defaults are unbound production
        # values so the service is fully usable — and testable — with no
        # lifecycle at all.
        self._model_slot = model_slot
        self._base_spawn = spawn
        self._base_model_slot = model_slot
        self._retriever: Retriever = (
            retriever if retriever is not None else _UnconfiguredRetriever()
        )
        self._timeout = (
            research_timeout_seconds
            if research_timeout_seconds is not None
            else config.brief_research_timeout_seconds
        )
        self._stale = (
            stale_after_seconds
            if stale_after_seconds is not None
            else config.brief_research_stale_after_seconds
        )

    # -- runtime wiring (services/lifecycle.py) ----------------------------- #

    def bind_runtime(self, *, spawn: Spawn, model_slot: ModelSlot) -> None:
        """Rebind the spawn and model-slot seams for the app's lifetime.

        Mirrors ``GenerationOrchestrator.bind_runtime`` exactly, including
        the "mutate in place" reasoning: ``services/lifecycle.py`` calls this
        on the module-level :data:`briefing_service` singleton, so a rebind
        of the module attribute would not reach references already imported.
        """
        self._spawn = spawn
        self._model_slot = model_slot

    def reset_runtime(self) -> None:
        """Restore the unbound construction-time seams (lifespan shutdown)."""
        self._spawn = self._base_spawn
        self._model_slot = self._base_model_slot

    # -- repository construction ---------------------------------------------#

    def _beats(self, session: AsyncSession) -> BeatRepository:
        return BeatRepository(session, stale_after_seconds=self._stale)

    # -- the arrival drain (TDD §5.6/D15) ------------------------------------ #

    async def drain_claimable(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        tz_offset_minutes: int,
        is_admin: bool = False,
        now: datetime | None = None,
    ) -> None:
        """Evaluate cadence for ``user_id``'s Beats and spawn what is due.

        Runs against the caller's own ``session`` (the read the learner
        already wanted — TDD §5.6/§7's "a GET with a side effect"): this
        method itself performs no claim and opens no transaction of its own,
        only reads. ``local_today`` is derived exactly once (D5) and carried
        into every spawned :meth:`run_research` call (D4a) — never re-derived
        by the background frame that eventually runs it.

        Bounded by ``MAX_BEATS_PER_LEARNER`` (D14, via
        ``BeatRepository.list_for_user``'s ``limit``) and, before any claim
        is attempted, by the research rate cap — checked **once per drain
        call**, before spawning any beat, and **non-raising**: hitting
        ``RATE_LIMIT_BRIEF_RESEARCH_PER_DAY`` here degrades to "no research
        this time" rather than turning a beats-list ``GET`` into a ``429``
        (TDD §7's explicit rule).

        The claim itself happens **inside** :meth:`run_research`, not here —
        so two concurrent ``drain_claimable`` calls may both decide the same
        Beat is claimable and both spawn a task for it; only one wins the
        atomic claim, and the other's task is a silent no-op (see the module
        docstring's "how they compose" note).
        """
        local_today = _local_today(tz_offset_minutes, now)
        beats = await self._beats(session).list_for_user(
            user_id=user_id, limit=self._config.max_beats_per_learner
        )
        if not beats:
            return

        last_entries = await BriefRepository(session).last_published_on_by_beat(
            [beat.id for beat in beats]
        )
        claimable_ids = [
            beat.id
            for beat in beats
            if is_claimable(
                last_entries.get(beat.id), beat.anchor_weekday, today=local_today
            )
        ]
        if not claimable_ids:
            return

        limiter = build_daily_rate_limiter(session, config=self._config)
        if not await limiter.brief_research_capacity_available(
            user_id=user_id, is_admin=is_admin
        ):
            return

        for beat_id in claimable_ids:
            self._spawn(self.run_research(beat_id, local_today))

    # -- the claimed pipeline (TDD §3/§5.3-§5.5/§5.7) ------------------------ #

    async def run_research(self, beat_id: uuid.UUID, local_today: date) -> None:
        """Claim -> plan -> retrieve -> find -> gate -> write -> persist.

        The permit (``MAX_CONCURRENT_BRIEF_RESEARCH``, D14) is acquired
        **before** the claim — ``run_outline_task``'s own reasoning, one
        workload over: a claim must never commit ``researching`` and then
        queue unbounded on the semaphore, which under a spike could let a
        healthy claimed row go stale mid-queue and be double-claimed. The
        claim itself is the atomic, idle-or-stale-only ``UPDATE`` reused
        unchanged from ``repositories/beats.py`` (D3) — a lost race (already
        ``researching``/``failed``/``refused``) is a silent no-op, exactly
        ``run_outline_task``'s shape.

        Any escape from the claimed body (an infra blip after the model ran,
        a persist error) is recorded ``failed`` best-effort by the top-level
        handler, mirroring ``run_outline_task``'s own invariant: the spawned
        task never leaks an unretrieved exception, and a row never wedges in
        ``researching`` until the stale window when a fenced mark could have
        recorded the real cause.
        """
        async with self._model_slot():
            fence = await self._claim(beat_id)
            if fence is None:
                return
            try:
                await self._run_claimed(beat_id, local_today, fence=fence)
            except Exception:
                logger.exception("brief_research_task_failed", beat_id=str(beat_id))
                with contextlib.suppress(Exception):
                    await self._mark_failed(beat_id, fence, _RESEARCH_FAILED_MESSAGE)

    async def _claim(self, beat_id: uuid.UUID) -> datetime | None:
        async with self._session_factory() as session:
            fence = await self._beats(session).claim_research(beat_id)
            await session.commit()
        return fence

    async def _run_claimed(
        self, beat_id: uuid.UUID, local_today: date, *, fence: datetime
    ) -> None:
        context = await self._load_context(beat_id)
        if context is None:
            # The Beat vanished (deleted) between the claim and now — a
            # referential-breakage case with no row left to mark, mirroring
            # ``services/generation.py``'s own vanished-lesson posture.
            logger.warning("brief_research_beat_vanished", beat_id=str(beat_id))
            return

        try:
            async with asyncio.timeout(self._timeout):
                # -- plan (pure) + retrieve (I/O, D14a's ONLY cost ceiling) -- #
                plan = build_query_plan(
                    context.topic,
                    context.guidance,
                    since=context.last_entry_on,
                    max_queries=self._config.brief_retrieval_max_queries,
                )
                documents = await retrieve(
                    self._retriever,
                    plan,
                    max_documents=self._config.brief_retrieval_max_documents,
                    text_budget_chars=self._config.brief_retrieval_text_budget_chars,
                )

                # §5.7's LOAD-BEARING row: zero documents after the §5.2
                # filters is a FAILED run, never Skipped — checked, and
                # returned on, before the researcher is ever called. "We
                # found nothing to read" is not "nothing happened" (PRD
                # §4.2); every other system in this codebase treats an empty
                # result as an empty success, and this is deliberately not
                # one of them.
                if not documents:
                    await self._mark_failed(beat_id, fence, _NO_DOCUMENTS_MESSAGE)
                    return

                # -- find (model): documents -> Findings | Refusal ---------- #
                researcher_deps = ResearcherDeps(
                    topic=context.topic,
                    guidance=context.guidance,
                    documents=documents,
                )
                researcher_run = await build_researcher_agent().run(
                    build_researcher_prompt(researcher_deps),
                    deps=researcher_deps,
                    model=self._resolve_model(
                        context.model_research
                        if context.model_research is not None
                        else self._config.model_research
                    ),
                )
                research_output = researcher_run.output
                if isinstance(research_output, Refusal):
                    # PRD §2's safety branch: terminal, never retried — an
                    # over-the-boundary Topic is not an infrastructural
                    # failure (TDD D3).
                    await self._mark_refused(beat_id, fence, research_output.message)
                    return

                # -- gate (pure, D9): Skipped is "no survivors", computed
                # here and nowhere else --------------------------------------
                survivors = filter_new(
                    research_output.findings,
                    context.prior_urls,
                    context.prior_claims,
                )
                analyst_documents = _documents_for_survivors(documents, survivors)

                # -- write (model): survivors -> BriefBody | SkippedNote ---- #
                analyst_deps = AnalystDeps(
                    topic=context.topic,
                    level=AGENT_LEVEL[context.level],
                    guidance=context.guidance,
                    documents=analyst_documents,
                    survivors=survivors,
                    open_threads=list(context.prior_claims),
                )
                analyst_run = await build_analyst_agent().run(
                    build_analyst_prompt(analyst_deps),
                    deps=analyst_deps,
                    model=self._resolve_model(
                        context.model_brief
                        if context.model_brief is not None
                        else self._config.model_brief
                    ),
                )
        except RetrievalUnavailableError:
            # §5.7's first row: visible, retryable, never a published Brief
            # and never Skipped.
            await self._mark_failed(beat_id, fence, _RETRIEVAL_FAILED_MESSAGE)
            return
        except TimeoutError:
            await self._mark_failed(beat_id, fence, _RESEARCH_TIMEOUT_MESSAGE)
            return
        except Exception:  # noqa: BLE001 - §5.5/§5.7: any agent/infra error -> failed
            # Covers a researcher/analyst retry-budget exhaustion
            # (``UnexpectedModelBehavior``) alongside any other provider or
            # infra error — §5.7's "writer exhausts validator retries ->
            # failed" row, generalized the way ``run_outline_task``'s own
            # blanket ``except Exception`` is.
            logger.exception("brief_research_pipeline_failed", beat_id=str(beat_id))
            await self._mark_failed(beat_id, fence, _RESEARCH_FAILED_MESSAGE)
            return

        # -- persist (§5.4/§5.5/§5.6): the ONLY output that survived
        # validation determines the branch; no partial Brief is ever written.
        result = analyst_run.output
        if isinstance(result, SkippedNote):
            skip_line = _render_skip_line(
                context.latest_published_number, result.detail
            )
            persisted = await self._persist_skipped(
                beat_id, fence, local_today=local_today, skip_line=skip_line
            )
        else:
            sources = _materialize_sources(result.cited_urls, documents)
            number = (context.latest_published_number or 0) + 1
            persisted = await self._persist_published(
                beat_id,
                fence,
                local_today=local_today,
                number=number,
                title=result.title,
                body_markdown=result.body_markdown,
                claims=[finding.claim for finding in survivors],
                sources=sources,
            )
        if not persisted:
            # Lost the fence between the model call and the persist (a stale
            # re-claim raced in) — the loser drops silently, mirroring
            # ``GenerationOrchestrator._persist_outline``'s own rollback-and-
            # drop for the identical race.
            logger.warning("brief_research_lost_fence_on_persist", beat_id=str(beat_id))

    # -- context load (own session, TDD §5.3/§5.4's inputs) ------------------ #

    async def _load_context(self, beat_id: uuid.UUID) -> _ResearchContext | None:
        async with self._session_factory() as session:
            beat = await self._beats(session).get(beat_id)
            if beat is None:
                return None
            briefs = BriefRepository(session)
            last_entries = await briefs.last_published_on_by_beat([beat_id])
            prior_urls = await briefs.prior_source_urls_for_beat(beat_id)
            prior_claims = await briefs.prior_claims_for_beat(beat_id)
            latest_published = await briefs.latest_published(beat_id)
            return _ResearchContext(
                account_id=beat.user_id,
                topic=beat.topic,
                guidance=beat.guidance,
                level=beat.level,
                model_research=beat.model_research,
                model_brief=beat.model_brief,
                last_entry_on=last_entries.get(beat_id),
                prior_urls=frozenset(prior_urls),
                prior_claims=tuple(prior_claims),
                latest_published_number=(
                    latest_published.number if latest_published is not None else None
                ),
            )

    # -- fenced marks (own session each, TDD §5.4's short-transaction
    # discipline, mirrored from ``GenerationOrchestrator``) ------------------ #

    async def _mark_failed(
        self, beat_id: uuid.UUID, fence: datetime, error: str
    ) -> bool:
        async with self._session_factory() as session:
            ok = await self._beats(session).mark_failed(
                beat_id, fence=fence, error=error
            )
            await self._commit_or_rollback(session, ok=ok)
        return ok

    async def _mark_refused(
        self, beat_id: uuid.UUID, fence: datetime, message: str
    ) -> bool:
        async with self._session_factory() as session:
            ok = await self._beats(session).mark_refused(
                beat_id, fence=fence, message=message
            )
            await self._commit_or_rollback(session, ok=ok)
        return ok

    @staticmethod
    async def _commit_or_rollback(session: AsyncSession, *, ok: bool) -> None:
        if ok:
            await session.commit()
        else:
            await session.rollback()

    async def _persist_published(
        self,
        beat_id: uuid.UUID,
        fence: datetime,
        *,
        local_today: date,
        number: int,
        title: str,
        body_markdown: str,
        claims: Sequence[str],
        sources: Sequence[NewSource],
    ) -> bool:
        """Mark ``idle`` then insert the published Brief, atomically.

        The mark runs **first**, mirroring
        ``GenerationOrchestrator._persist_outline``: a lost fence (a stale
        re-claim slipped in) makes ``mark_idle`` return ``False``, so this
        rolls back *before* inserting a Brief the loser has no business
        writing, rather than racing a winner's own insert.
        """
        async with self._session_factory() as session:
            marked = await self._beats(session).mark_idle(beat_id, fence=fence)
            if not marked:
                await session.rollback()
                return False
            await BriefRepository(session).create_published(
                beat_id=beat_id,
                number=number,
                published_at=datetime.now(UTC),
                published_on=local_today,
                title=title,
                body_markdown=body_markdown,
                claims=claims,
                sources=sources,
            )
            await session.commit()
            return True

    async def _persist_skipped(
        self,
        beat_id: uuid.UUID,
        fence: datetime,
        *,
        local_today: date,
        skip_line: str,
    ) -> bool:
        """Mark ``idle`` then insert the Skipped entry, atomically (see
        :meth:`_persist_published`'s docstring for the ordering reason)."""
        async with self._session_factory() as session:
            marked = await self._beats(session).mark_idle(beat_id, fence=fence)
            if not marked:
                await session.rollback()
                return False
            await BriefRepository(session).create_skipped(
                beat_id=beat_id,
                published_at=datetime.now(UTC),
                published_on=local_today,
                skip_line=skip_line,
            )
            await session.commit()
            return True


# A module-level default instance, mirroring ``generation_orchestrator`` /
# ``flashcard_drafting_service``: production wiring constructed once, cheaply
# (no I/O — ``build_researcher_agent``/``build_analyst_agent`` bind no model,
# TDD D7), that ``services/lifecycle.py`` binds and a future router (AL-522)
# imports directly. Its ``retriever`` stays the safe, import-time-inert
# placeholder until AL-522/523 wire a real one in.
briefing_service = BriefingService()
