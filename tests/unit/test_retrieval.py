"""Unit tests for the retrieval seam (TDD §5.2, §11; ticket AL-512).

Fakes over mocks (CLAUDE.md): `retrieve()`'s invariants are driven by a small
in-process `Retriever` fake, with no network and no fixture files.
`FixtureRetriever` itself is exercised against real (temp-dir) YAML files,
since parsing that exact format is the behavior under test — a fake there
would test nothing.

**The load-bearing case is `test_fixture_retriever_raises_rather_than_...`**
below: a `FixtureRetriever` miss must raise `RetrievalUnavailableError`, never
return `[]`. Downstream, an empty result and "nothing material happened" are
the same value — the novelty gate finds no survivors and the analyst
publishes Skipped — so a stale or mistyped fixture key returning `[]` would
silently manufacture a Skipped entry (§5.2).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest

from aleph.agents.researcher import RetrievedDocument
from aleph.services.retrieval import (
    FORCE_RETRIEVAL_FAILURE,
    FixtureRetriever,
    QueryPlan,
    RetrievalUnavailableError,
    StubRetriever,
    build_query_plan,
    retrieve,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _doc(
    url: str,
    *,
    text: str = "some retrieved text",
    published_on: date | None = date(2026, 7, 1),
    publisher: str = "Example Wire",
    title: str = "Example title",
) -> RetrievedDocument:
    return RetrievedDocument(
        url=url,
        publisher=publisher,
        title=title,
        published_on=published_on,
        text=text,
    )


class _FakeRetriever:
    """A `Retriever` returning a fixed, caller-supplied document list."""

    def __init__(self, documents: Sequence[RetrievedDocument]) -> None:
        self._documents = list(documents)

    async def search(self, queries: Sequence[str]) -> list[RetrievedDocument]:
        del queries
        return list(self._documents)


class _RaisingRetriever:
    """A `Retriever` that always fails — the propagation case."""

    async def search(self, queries: Sequence[str]) -> list[RetrievedDocument]:
        del queries
        raise RetrievalUnavailableError("boom")


def _write_fixture(tmp_path: Path, beat: str, body: str) -> Path:
    fixtures_dir = tmp_path / "retrieval"
    fixtures_dir.mkdir(exist_ok=True)
    (fixtures_dir / f"{beat}.yaml").write_text(body, encoding="utf-8")
    return fixtures_dir


# --- build_query_plan (pure, D6a) -----------------------------------------------


def test_plan_is_deterministic_for_fixed_standing_orders() -> None:
    args = ("EU AI regulation", "policy and enforcement", date(2026, 7, 20))
    first = build_query_plan(*args, max_queries=6)
    second = build_query_plan(*args, max_queries=6)

    assert first == second
    assert first.queries


def test_plan_respects_max_queries() -> None:
    plan = build_query_plan("Rust ownership", None, None, max_queries=2)

    assert len(plan.queries) <= 2
    assert len(plan.queries) == 2  # enough distinct angles exist to fill it


def test_plan_rejects_a_non_positive_max_queries() -> None:
    with pytest.raises(ValueError, match="max_queries"):
        build_query_plan("Topic", None, None, max_queries=0)


def test_plan_reflects_guidance_when_present() -> None:
    bare = build_query_plan("Topic", None, date(2026, 1, 1), max_queries=6)
    guided = build_query_plan(
        "Topic", "focus on enforcement", date(2026, 1, 1), max_queries=6
    )

    assert bare != guided
    assert any("focus on enforcement" in query for query in guided.queries)
    assert not any("focus on enforcement" in query for query in bare.queries)


def test_plan_queries_are_always_unique() -> None:
    plan = build_query_plan("Topic", "Topic", date(2026, 1, 1), max_queries=6)

    assert len(plan.queries) == len(set(plan.queries))


def test_plan_with_no_prior_entry_still_produces_queries() -> None:
    # A Beat's first-ever run has no prior published_on to derive `since` from
    # (PRD §3 — the first Brief is researched immediately).
    plan = build_query_plan("Topic", None, None, max_queries=6)

    assert plan.queries


# --- retrieve(): dedupe, undated drop, budget (§5.2) ----------------------------


@pytest.mark.anyio
async def test_retrieve_dedupes_by_url() -> None:
    retriever = _FakeRetriever(
        [
            _doc("https://example.com/a"),
            _doc("https://example.com/a"),
            _doc("https://example.com/b"),
        ]
    )
    plan = QueryPlan(queries=("q1",))

    documents = await retrieve(retriever, plan, text_budget_chars=10_000)

    assert sorted(document.url for document in documents) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


@pytest.mark.anyio
async def test_retrieve_drops_undated_documents() -> None:
    retriever = _FakeRetriever(
        [
            _doc("https://example.com/dated", published_on=date(2026, 1, 1)),
            _doc("https://example.com/undated", published_on=None),
        ]
    )
    plan = QueryPlan(queries=("q1",))

    documents = await retrieve(retriever, plan, text_budget_chars=10_000)

    assert [document.url for document in documents] == ["https://example.com/dated"]


@pytest.mark.anyio
async def test_retrieve_budget_is_never_exceeded() -> None:
    retriever = _FakeRetriever(
        [
            _doc("https://example.com/short", text="x" * 100),
            _doc("https://example.com/long", text="y" * 500),
        ]
    )
    plan = QueryPlan(queries=("q1",))

    documents = await retrieve(retriever, plan, text_budget_chars=300)

    assert sum(len(document.text) for document in documents) <= 300


@pytest.mark.anyio
async def test_retrieve_redistributes_unused_share_from_short_documents() -> None:
    # Two documents, budget 300: an even split gives each 150. The short
    # document only needs 100, so its unused 50 chars must flow to the long one.
    retriever = _FakeRetriever(
        [
            _doc("https://example.com/short", text="s" * 100),
            _doc("https://example.com/long", text="l" * 1000),
        ]
    )
    plan = QueryPlan(queries=("q1",))

    documents = await retrieve(retriever, plan, text_budget_chars=300)
    by_url = {document.url: document for document in documents}

    assert len(by_url["https://example.com/short"].text) == 100  # untouched
    assert by_url["https://example.com/long"].text == "l" * 200  # 300 - 100, a prefix


@pytest.mark.anyio
async def test_retrieve_budget_adversarial_one_huge_document_and_many_tiny_ones() -> (
    None
):
    """One 500,000-char document plus eleven 50-char documents, at D14a's real
    ceiling (160,000 chars). A naive even-share reading (13 * (160000/12) each)
    would needlessly clip every tiny document; the fair-share algorithm must
    instead let every tiny document through whole and hand the huge one
    whatever remains — and the total must never exceed the ceiling either way.
    """
    tiny_urls = [f"https://example.com/tiny-{i}" for i in range(11)]
    documents_in = [_doc(url, text="t" * 50) for url in tiny_urls]
    documents_in.append(_doc("https://example.com/huge", text="h" * 500_000))
    retriever = _FakeRetriever(documents_in)
    plan = QueryPlan(queries=("q1",))

    documents = await retrieve(retriever, plan, text_budget_chars=160_000)

    assert sum(len(document.text) for document in documents) <= 160_000
    by_url = {document.url: document for document in documents}
    for url in tiny_urls:
        assert by_url[url].text == "t" * 50
    assert len(by_url["https://example.com/huge"].text) == 160_000 - 11 * 50


@pytest.mark.anyio
async def test_retrieve_truncation_is_a_deterministic_prefix_across_runs() -> None:
    retriever = _FakeRetriever(
        [
            _doc("https://example.com/a", text="abcdefgh" * 100),
            _doc("https://example.com/b", text="z" * 5),
        ]
    )
    plan = QueryPlan(queries=("q1",))

    first = await retrieve(retriever, plan, text_budget_chars=50)
    second = await retrieve(retriever, plan, text_budget_chars=50)

    assert first == second  # byte-identical, run to run
    by_url = {document.url: document for document in first}
    truncated = by_url["https://example.com/a"].text
    assert ("abcdefgh" * 100).startswith(
        truncated
    )  # a real prefix, not a slice elsewhere
    assert by_url["https://example.com/b"].text == "z" * 5  # short doc, untouched


@pytest.mark.anyio
async def test_retrieve_propagates_retrieval_unavailable() -> None:
    """`RetrievalUnavailableError` PROPAGATES, never degrades to `[]` (§5.2)."""
    plan = QueryPlan(queries=("q1",))

    with pytest.raises(RetrievalUnavailableError):
        await retrieve(_RaisingRetriever(), plan, text_budget_chars=1_000)


# --- FixtureRetriever (TDD §10, D10) --------------------------------------------

_FIXTURE_YAML = """\
beat: eu-ai-regulation-some-experience
queries:
  - "EU AI regulation"
  - "EU AI regulation — latest developments since 2026-07-20"
results:
  "EU AI regulation":
    - url: "https://example.com/northlake-review"
      publisher: "Northlake Health System"
      title: "Ambient Documentation: 14-Month Post-Deployment Review"
      published_on: "2026-07-30"
      text: "The review found adoption climbing steadily across departments."
  "EU AI regulation — latest developments since 2026-07-20": []
"""


@pytest.mark.anyio
async def test_fixture_retriever_replays_recorded_results(tmp_path: Path) -> None:
    fixtures_dir = _write_fixture(
        tmp_path, "eu-ai-regulation-some-experience", _FIXTURE_YAML
    )
    fixture_retriever = FixtureRetriever(
        fixtures_dir, "eu-ai-regulation-some-experience"
    )

    documents = await fixture_retriever.search(["a completely different live query"])

    assert [document.url for document in documents] == [
        "https://example.com/northlake-review"
    ]
    assert documents[0].publisher == "Northlake Health System"
    assert documents[0].published_on == date(2026, 7, 30)


@pytest.mark.anyio
async def test_fixture_retriever_ignores_the_live_queries_argument(
    tmp_path: Path,
) -> None:
    """D10's correction: replay executes the RECORDED queries and never
    re-derives them — so two calls with different live `queries` arguments
    still replay identically.
    """
    fixtures_dir = _write_fixture(
        tmp_path, "eu-ai-regulation-some-experience", _FIXTURE_YAML
    )
    fixture_retriever = FixtureRetriever(
        fixtures_dir, "eu-ai-regulation-some-experience"
    )

    first = await fixture_retriever.search(["one live query"])
    second = await fixture_retriever.search(["an entirely different set", "of queries"])

    assert first == second


def test_fixture_retriever_raises_rather_than_returning_empty_on_a_miss(
    tmp_path: Path,
) -> None:
    """THE LOAD-BEARING TEST (§5.2). A `FixtureRetriever` constructed for a
    beat with **no recorded fixture file** must raise
    `RetrievalUnavailableError` at construction, never silently behave as
    though it would return `[]` on `search()`. A stale or mistyped fixture
    key manufacturing an empty result is indistinguishable, downstream, from
    a genuinely quiet week — and that conflation is exactly what PRD §4.2
    forbids (a Skipped entry standing in for an infrastructure/config miss).
    """
    empty_fixtures_dir = tmp_path / "retrieval"
    empty_fixtures_dir.mkdir()

    with pytest.raises(RetrievalUnavailableError):
        FixtureRetriever(empty_fixtures_dir, "some-beat-with-no-recorded-fixture")


@pytest.mark.anyio
async def test_fixture_retriever_raises_on_a_beat_key_mismatch(tmp_path: Path) -> None:
    """A file present under the wrong beat's filename is also a miss, not a
    result to replay — it must not silently serve another Beat's documents.
    """
    fixtures_dir = tmp_path / "retrieval"
    fixtures_dir.mkdir()
    # Filename says "wrong-beat", but the file's own `beat:` field says
    # something else — a copy-paste mistake this must catch.
    (fixtures_dir / "wrong-beat.yaml").write_text(_FIXTURE_YAML, encoding="utf-8")

    with pytest.raises(RetrievalUnavailableError):
        FixtureRetriever(fixtures_dir, "wrong-beat")


@pytest.mark.anyio
async def test_fixture_retriever_raises_when_a_recorded_query_has_no_results_entry(
    tmp_path: Path,
) -> None:
    """A malformed fixture — a recorded query with nothing under `results` for
    it — must also raise, not silently drop that query's (unknown) documents.
    """
    malformed = """\
beat: malformed-beat
queries:
  - "query one"
  - "query two — never recorded in results"
results:
  "query one": []
"""
    fixtures_dir = _write_fixture(tmp_path, "malformed-beat", malformed)
    fixture_retriever = FixtureRetriever(fixtures_dir, "malformed-beat")

    with pytest.raises(RetrievalUnavailableError):
        await fixture_retriever.search([])


@pytest.mark.anyio
async def test_retrieve_applies_its_invariants_over_a_fixture_retriever(
    tmp_path: Path,
) -> None:
    """`retrieve()`'s dedupe/undated-drop/budget invariants apply uniformly —
    a `FixtureRetriever` is not a special case.
    """
    fixture_body = """\
beat: dedupe-and-undated-beat
queries:
  - "query one"
  - "query two"
results:
  "query one":
    - url: "https://example.com/shared"
      publisher: "Wire One"
      title: "Shared document"
      published_on: "2026-07-01"
      text: "shared document text"
  "query two":
    - url: "https://example.com/shared"
      publisher: "Wire One"
      title: "Shared document"
      published_on: "2026-07-01"
      text: "shared document text"
    - url: "https://example.com/undated"
      publisher: "Wire Two"
      title: "No date document"
      published_on: null
      text: "undated document text"
"""
    fixtures_dir = _write_fixture(tmp_path, "dedupe-and-undated-beat", fixture_body)
    fixture_retriever = FixtureRetriever(fixtures_dir, "dedupe-and-undated-beat")
    # A live plan whose query text has nothing to do with the fixture's
    # recorded queries — proving `retrieve()` still gets the fixture's
    # documents via the recorded-queries replay, not these.
    plan = QueryPlan(queries=("an unrelated live query",))

    documents = await retrieve(fixture_retriever, plan, text_budget_chars=10_000)

    assert [document.url for document in documents] == ["https://example.com/shared"]


# --- StubRetriever (TDD §5.2, §11) ----------------------------------------------


@pytest.mark.anyio
async def test_stub_retriever_is_deterministic() -> None:
    stub = StubRetriever()
    queries = ["a stub query", "another stub query"]

    first = await stub.search(queries)
    second = await stub.search(queries)

    assert first == second


@pytest.mark.anyio
async def test_stub_retriever_returns_dated_documents() -> None:
    stub = StubRetriever()

    documents = await stub.search(["a stub query"])

    assert documents
    assert all(document.published_on is not None for document in documents)


@pytest.mark.anyio
async def test_stub_retriever_force_retrieval_failure_sentinel_raises() -> None:
    stub = StubRetriever()

    with pytest.raises(RetrievalUnavailableError):
        await stub.search([f"some topic {FORCE_RETRIEVAL_FAILURE}"])


@pytest.mark.anyio
async def test_stub_retriever_through_the_whole_pipeline() -> None:
    """`build_query_plan` -> `StubRetriever` -> `retrieve()`, end to end, with
    no network and no API key — the constraint this whole seam exists for.
    """
    plan = build_query_plan(
        "EU AI regulation", "policy and enforcement", date(2026, 7, 20), max_queries=6
    )

    documents = await retrieve(StubRetriever(), plan, text_budget_chars=160_000)

    assert documents
    assert all(document.published_on is not None for document in documents)
    assert sum(len(document.text) for document in documents) <= 160_000
