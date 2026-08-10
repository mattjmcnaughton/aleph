"""Opt-in recorder for ``evals/fixtures/retrieval/*.yaml`` (Phase 6 TDD §5.2/§10,
AL-550).

Hits the live Exa API **once per query** of every Beat named in
``evals/brief_seed_set.yaml`` whose fixture file does not already exist (see
``--force``), and writes the Beat-keyed YAML :class:`~aleph.services.
retrieval.FixtureRetriever` reads: ``beat``, ``queries`` recorded beside
``results`` (TDD §5.2: "record the query plan beside the results"; D10:
replay executes the recorded ``queries`` and never re-derives them).

**Never run in CI, never part of `just gate` or `just evals`.** It spends real
money against the Exa API and needs ``EXA_API_KEY`` — the same opt-in posture
``evals/`` already has for ``OPENROUTER_API_KEY`` (docs/evals.md).

**Idempotent.** A fixture file that already exists is skipped (printed, not
re-recorded) unless ``--force`` is passed, so running this recipe twice in a
row costs nothing the second time, and re-recording one Beat is a deliberate,
explicit action rather than something a stray re-run does by accident.

**Sizing note (a deliberate, documented divergence from production).**
:class:`~aleph.services.retrieval.ExaRetriever` sizes its per-query
``numResults`` off the *whole* query plan at once
(``_exa_per_query_num_results(len(queries), max_documents)``, D14a's headroom
math), because in production one ``search(queries)`` call shares
``BRIEF_RETRIEVAL_MAX_DOCUMENTS`` across every query in the plan. This script
calls ``retriever.search([query])`` **one query at a time** — the only way to
capture each query's own results separately for the fixture's
``results: {query: [...]}`` mapping — which means every call here computes its
per-query share as if it were the plan's only query. The result is
**conservatively larger**, never smaller, than what a live plan-wide call
would fetch per query: a recorded fixture may hold *more* raw documents than
production would have asked Exa for, which only gives ``retrieve()``'s own
dedupe/cap/budget more (still-safe) material to filter from — it does not
change what ``FixtureRetriever`` replays or what invariant it satisfies. Kept
simple and against :class:`ExaRetriever`'s public ``search()`` method rather
than reaching into its private per-query sizing helper, so this script does
not need to track that method's internals across a refactor.

**Fixture size (code-review FIX 6, AL-550).** The sizing note above means one
Beat's six single-query calls can return up to ``ceil(12 * 1.5) = 18`` raw
results EACH — up to 108 raw extractions, un-deduped, before this section's
two mitigations: **(1)** every document's ``text`` is truncated to
``BRIEF_RETRIEVAL_TEXT_BUDGET_CHARS`` (160 000 chars, D14a) before it is
written — a live replay would truncate it further anyway (that budget is
divided across at most ``BRIEF_RETRIEVAL_MAX_DOCUMENTS`` = 12 documents), so
nothing a real run would have read is lost; **(2)** a document already
recorded under an earlier query in this Beat's plan is **not written again**
under a later query that also returned it — deduped by URL, first occurrence
wins, exactly `retrieve()`'s own ``_dedupe_by_url`` order — while every
query still gets its own ``results`` entry (an explicit ``[]`` when every one
of its documents was already claimed), so ``queries``/``results`` stay keyed
1:1 and `FixtureRetriever` cannot tell the difference from a query that
genuinely found nothing new. **Expected size:** real news/reference articles
run a few KB to a few tens of KB of extracted text, so a typical Beat
(6 queries, meaningful cross-query overlap on one subject) is expected to
land in the **tens to low hundreds of KB**, not megabytes; the 160 000-char
per-document ceiling only binds against a pathological single extraction, and
the true worst case — 108 entirely distinct URLs, each hitting the full
160 000-char ceiling — is a **~17 MB theoretical bound** no real Beat here
approaches. `docs/evals.md`'s cost-estimate section repeats this so it is a
decision made in advance, not a surprise discovered at commit time.

Usage::

    just record-retrieval-fixtures
    just record-retrieval-fixtures --force
    just record-retrieval-fixtures --only eu-ai-regulation-enforcement-intermediate
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

import yaml

from evals.generation import BRIEF_FIXTURES_DIR, load_brief_seed_set

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

    from aleph.agents.researcher import RetrievedDocument


def _document_to_mapping(
    document: RetrievedDocument, *, text_budget_chars: int
) -> dict[str, object]:
    """One ``results[query][i]`` entry, matching what
    :class:`~aleph.services.retrieval.FixtureRetriever`'s own
    ``_document_from_mapping`` reads back.

    ``text`` is truncated to ``text_budget_chars`` — a plain prefix, mirroring
    ``retrieve()``'s own ``_apply_text_budget`` (code-review FIX 6, AL-550;
    see the module docstring's "Fixture size" section for why nothing a real
    replay would have read is lost by capping here too).
    """
    return {
        "url": document.url,
        "publisher": document.publisher,
        "title": document.title,
        "published_on": (
            document.published_on.isoformat()
            if document.published_on is not None
            else None
        ),
        "text": document.text[:text_budget_chars],
    }


async def _record_one(
    *,
    beat: str,
    topic: str,
    guidance: str | None,
    since: date,
    max_queries: int,
    max_documents: int,
    text_budget_chars: int,
    out_path: Path,
    api_key: str,
) -> None:
    # Imported here, not at module level: constructing an ``ExaRetriever`` is
    # cheap and side-effect-free, but importing ``aleph.config`` at module
    # level would read the environment (and fail import-time linting/tests
    # that never intend to touch Exa) before ``main`` has even parsed argv.
    from aleph.services.retrieval import ExaRetriever, build_query_plan

    plan = build_query_plan(topic, guidance, since=since, max_queries=max_queries)
    retriever = ExaRetriever(api_key, since=since, max_documents=max_documents)

    # FIX 6 (code review, AL-550): dedupe by URL ACROSS this Beat's queries —
    # first occurrence wins, in `plan.queries` order, matching `retrieve()`'s
    # own `_dedupe_by_url` — and truncate every kept document's text. Every
    # query still gets its own `results[query]` entry, even when every one of
    # its documents was already claimed by an earlier query: an explicit
    # `[]` is the same affirmative-empty-result shape `FixtureRetriever`
    # already treats as legitimate, so `queries`/`results` stay keyed 1:1 and
    # a query fully deduped away reads identically to one Exa returned
    # nothing for. See the module docstring's "Fixture size" section.
    seen_urls: set[str] = set()
    results: dict[str, list[dict[str, object]]] = {}
    raw_documents = 0
    recorded_documents = 0
    for query in plan.queries:
        documents = await retriever.search([query])
        raw_documents += len(documents)
        kept: list[dict[str, object]] = []
        for document in documents:
            if document.url in seen_urls:
                continue
            seen_urls.add(document.url)
            kept.append(
                _document_to_mapping(document, text_budget_chars=text_budget_chars)
            )
        results[query] = kept
        recorded_documents += len(kept)

    payload = {"beat": beat, "queries": list(plan.queries), "results": results}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    query_word = "query" if len(plan.queries) == 1 else "queries"
    duplicates = raw_documents - recorded_documents
    print(
        f"recorded {out_path} ({len(plan.queries)} {query_word}, "
        f"{recorded_documents} document(s) after cross-query dedupe "
        f"({duplicates} duplicate(s) dropped), each capped at "
        f"{text_budget_chars:,} chars)"
    )


async def _main_async(args: argparse.Namespace) -> int:
    from aleph.config import settings

    if not settings.exa_api_key:
        print(
            "EXA_API_KEY is not set. Recording hits the live Exa API and needs "
            "the key (env or .env).",
            file=sys.stderr,
        )
        return 2

    try:
        dataset = load_brief_seed_set()
    except (ValueError, yaml.YAMLError) as error:
        print(
            f"evals/brief_seed_set.yaml cannot be loaded — "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2

    only = set(args.only) if args.only else None
    cases = [
        case
        for case in dataset.cases
        if only is None or case.inputs.beat_fixture in only
    ]
    if not cases:
        message = (
            f"no matching cases in evals/brief_seed_set.yaml (--only {sorted(only)})"
            if only
            else "evals/brief_seed_set.yaml has no cases"
        )
        print(message, file=sys.stderr)
        return 2

    for case in cases:
        out_path = BRIEF_FIXTURES_DIR / f"{case.inputs.beat_fixture}.yaml"
        if out_path.exists() and not args.force:
            print(f"skip {out_path} (already recorded; pass --force to re-record)")
            continue
        await _record_one(
            beat=case.inputs.beat_fixture,
            topic=case.inputs.topic,
            guidance=case.inputs.guidance,
            since=case.inputs.prior_brief.published_on,
            max_queries=settings.brief_retrieval_max_queries,
            max_documents=settings.brief_retrieval_max_documents,
            text_budget_chars=settings.brief_retrieval_text_budget_chars,
            out_path=out_path,
            api_key=settings.exa_api_key,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Record evals/fixtures/retrieval/*.yaml from the live Exa API for "
            "every Beat named in evals/brief_seed_set.yaml. Opt-in, costs "
            "money, never run in CI — see this module's docstring."
        )
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-record a fixture even if the file already exists",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="BEAT_FIXTURE",
        help="record only this beat_fixture key (repeatable)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
