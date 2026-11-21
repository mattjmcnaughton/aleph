"""Unit tests for the retrieval seam (TDD §5.2, §11; ticket AL-512).

Fakes over mocks (CLAUDE.md): `retrieve()`'s invariants are driven by a small
in-process `Retriever` fake, with no network and no fixture files.
"""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

import httpx
import pytest

from aleph.agents.researcher import RetrievedDocument
from aleph.services.retrieval import (
    FORCE_RETRIEVAL_FAILURE,
    ExaRetriever,
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

    async def search(
        self, queries: Sequence[str], *, since: date | None = None
    ) -> list[RetrievedDocument]:
        del queries, since
        return list(self._documents)


class _RaisingRetriever:
    """A `Retriever` that always fails — the propagation case."""

    async def search(
        self, queries: Sequence[str], *, since: date | None = None
    ) -> list[RetrievedDocument]:
        del queries, since
        raise RetrievalUnavailableError("boom")


@pytest.mark.anyio
async def test_teaching_allows_undated_references_but_briefs_require_dates() -> None:
    documents = [
        _doc("https://example.com/manual", text="manual text", published_on=None),
        _doc("https://example.com/manual", text="duplicate", published_on=None),
        _doc("https://example.com/empty", text=""),
        _doc("https://example.com/news", text="news text"),
    ]
    plan = QueryPlan(queries=("reference",))
    teaching = await retrieve(
        _FakeRetriever(documents),
        plan,
        max_documents=2,
        text_budget_chars=12,
        require_date=False,
    )
    assert [doc.url for doc in teaching] == [
        "https://example.com/manual",
        "https://example.com/news",
    ]
    assert [doc.text for doc in teaching] == ["manual", "news t"]
    briefs = await retrieve(
        _FakeRetriever(documents),
        plan,
        max_documents=2,
        text_budget_chars=12,
    )
    assert [doc.url for doc in briefs] == ["https://example.com/news"]


class _RecordingRetriever:
    """A `Retriever` that records every `search()` call's arguments —
    exercises `retrieve()`'s contract that `plan.since` is passed straight
    through as `search`'s own `since` keyword (the AL-521/AL-523 handoff
    gap: `QueryPlan` is the one source of truth for the period start, see
    its docstring), never re-derived or dropped along the way.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], date | None]] = []

    async def search(
        self, queries: Sequence[str], *, since: date | None = None
    ) -> list[RetrievedDocument]:
        self.calls.append((tuple(queries), since))
        return []


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


def test_plan_queries_are_structurally_unique_even_with_adversarial_guidance() -> None:
    """Queries are unique **by construction**, not by an active dedupe (FIX 6,
    ticket AL-512 review): `build_query_plan` used to carry a `seen`-set
    dedupe on the theory that an empty `guidance` would let two angles
    collide, but that could never happen (`if guidance:` already skips the
    one angle `guidance` feeds when falsy) and the dedupe was proven dead
    code and removed. `guidance` here is deliberately chosen to mimic another
    angle's literal wording — an adversarial attempt at a collision — and it
    still cannot produce one, because every angle is glued to the same
    `{topic}` prefix by a different literal connector.
    """
    plan = build_query_plan(
        "Topic",
        "latest developments — in the past week",  # echoes another angle's tail
        None,
        max_queries=6,
    )

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

    documents = await retrieve(
        retriever, plan, max_documents=1_000, text_budget_chars=10_000
    )

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

    documents = await retrieve(
        retriever, plan, max_documents=1_000, text_budget_chars=10_000
    )

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

    documents = await retrieve(
        retriever, plan, max_documents=1_000, text_budget_chars=300
    )

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

    documents = await retrieve(
        retriever, plan, max_documents=1_000, text_budget_chars=300
    )
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

    documents = await retrieve(
        retriever, plan, max_documents=1_000, text_budget_chars=160_000
    )

    assert sum(len(document.text) for document in documents) <= 160_000
    by_url = {document.url: document for document in documents}
    for url in tiny_urls:
        assert by_url[url].text == "t" * 50
    assert len(by_url["https://example.com/huge"].text) == 160_000 - 11 * 50


@pytest.mark.anyio
async def test_retrieve_enforces_max_documents_after_dedupe() -> None:
    """FIX 2 (ticket AL-512 review): `max_documents` is enforced inside
    `retrieve()`, after dedupe/undated-drop, before the character budget —
    the total cap D14a names but nothing previously enforced.
    """
    documents_in = [_doc(f"https://example.com/{i}") for i in range(5)]
    retriever = _FakeRetriever(documents_in)
    plan = QueryPlan(queries=("q1",))

    documents = await retrieve(
        retriever, plan, max_documents=3, text_budget_chars=10_000
    )

    assert len(documents) == 3


@pytest.mark.anyio
async def test_retrieve_document_cap_selection_is_deterministic_across_runs() -> None:
    """The selection rule (first `max_documents` survivors, in the deduped/
    dated order established earlier in the pipeline) must be stable so
    fixture replay stays byte-identical (§11) — not just the count, the
    *which ones*.
    """
    documents_in = [_doc(f"https://example.com/{i}") for i in range(5)]
    plan = QueryPlan(queries=("q1",))

    first = await retrieve(
        _FakeRetriever(documents_in), plan, max_documents=3, text_budget_chars=10_000
    )
    second = await retrieve(
        _FakeRetriever(documents_in), plan, max_documents=3, text_budget_chars=10_000
    )

    assert [document.url for document in first] == [document.url for document in second]
    assert [document.url for document in first] == [
        "https://example.com/0",
        "https://example.com/1",
        "https://example.com/2",
    ]


@pytest.mark.anyio
async def test_retrieve_document_cap_applies_after_dedupe_not_before() -> None:
    """A cap counted before dedupe would starve a plan whose queries all
    legitimately answer with the same handful of URLs — `max_documents` must
    bound the *distinct* survivors, not the raw pre-dedupe result count.
    """
    # 5 raw results, but only 2 distinct URLs — a pre-dedupe cap of 2 would
    # wrongly drop one of the two real documents.
    retriever = _FakeRetriever(
        [
            _doc("https://example.com/a"),
            _doc("https://example.com/a"),
            _doc("https://example.com/a"),
            _doc("https://example.com/a"),
            _doc("https://example.com/b"),
        ]
    )
    plan = QueryPlan(queries=("q1",))

    documents = await retrieve(
        retriever, plan, max_documents=2, text_budget_chars=10_000
    )

    assert sorted(document.url for document in documents) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


@pytest.mark.anyio
async def test_retrieve_drops_documents_with_empty_original_text() -> None:
    """FIX 3 (ticket AL-512 review): PRD §4.4's third retrieval requirement
    ("enough of the retrieved text to ground a quote") was enforced nowhere.
    A dated document with no text must never reach an agent's `Deps`.
    """
    retriever = _FakeRetriever(
        [
            _doc("https://example.com/empty", text=""),
            _doc("https://example.com/real", text="some real text"),
        ]
    )
    plan = QueryPlan(queries=("q1",))

    documents = await retrieve(
        retriever, plan, max_documents=1_000, text_budget_chars=10_000
    )

    assert [document.url for document in documents] == ["https://example.com/real"]


@pytest.mark.anyio
async def test_retrieve_drops_documents_zeroed_out_by_the_budget() -> None:
    """FIX 3 (ticket AL-512 review): a document allocated `0` characters by
    the water-filling budget (because the budget ran out before reaching it)
    must not survive in the returned list — its URL would sit in an agent's
    `Deps` with nothing behind it, exactly the D8/PRD §4.4 violation the
    review found (1,200 documents at budget 1,000 -> 200 zero-text survivors
    on the shipped code).
    """
    # budget=1: the first-visited (shortest) document absorbs it all, the
    # rest of the many equally-short documents are zeroed out by the time
    # remaining_budget hits 0.
    documents_in = [_doc(f"https://example.com/{i}", text="x" * 10) for i in range(5)]
    retriever = _FakeRetriever(documents_in)
    plan = QueryPlan(queries=("q1",))

    documents = await retrieve(
        retriever, plan, max_documents=1_000, text_budget_chars=1
    )

    assert len(documents) == 1
    assert all(document.text for document in documents)


@pytest.mark.anyio
async def test_retrieve_truncation_is_a_deterministic_prefix_across_runs() -> None:
    retriever = _FakeRetriever(
        [
            _doc("https://example.com/a", text="abcdefgh" * 100),
            _doc("https://example.com/b", text="z" * 5),
        ]
    )
    plan = QueryPlan(queries=("q1",))

    first = await retrieve(retriever, plan, max_documents=1_000, text_budget_chars=50)
    second = await retrieve(retriever, plan, max_documents=1_000, text_budget_chars=50)

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
        await retrieve(
            _RaisingRetriever(), plan, max_documents=1_000, text_budget_chars=1_000
        )


@pytest.mark.anyio
async def test_retrieve_passes_the_plans_since_through_to_search() -> None:
    """The AL-521/AL-523 handoff gap this closes: `retrieve()` must pass
    `plan.since` — never a hardcoded or re-derived value — as `search`'s own
    `since` keyword, so a provider with a structured date filter
    (`ExaRetriever`'s `startPublishedDate`) gets the Beat's real period start
    on every call, even though only one `Retriever` instance is bound for
    the whole process's lifetime (`services/lifecycle.py`).
    """
    retriever = _RecordingRetriever()
    plan = QueryPlan(queries=("q1", "q2"), since=date(2026, 7, 20))

    await retrieve(retriever, plan, max_documents=1_000, text_budget_chars=1_000)

    assert retriever.calls == [(("q1", "q2"), date(2026, 7, 20))]


@pytest.mark.anyio
async def test_retrieve_passes_none_through_for_a_beats_first_run() -> None:
    """`QueryPlan.since` defaults to `None` (a Beat's first-ever run, PRD
    §3) and `retrieve()` must pass that `None` through unchanged too — not
    substitute a value of its own.
    """
    retriever = _RecordingRetriever()
    plan = QueryPlan(queries=("q1",))

    await retrieve(retriever, plan, max_documents=1_000, text_budget_chars=1_000)

    assert retriever.calls == [(("q1",), None)]


# --- ExaRetriever (D6; ticket AL-523) -------------------------------------------
#
# A FAKE HTTP layer (`httpx.MockTransport`), never a mock library — CLAUDE.md's
# "Fakes over mocks", applied to the one seam in this module that talks to a
# real network in production. No test here reaches the actual Exa API.


def _exa_result(
    *,
    url: str = "https://example.com/article",
    title: str | None = "Some article",
    published_date: str | None = "2026-07-30",
    author: str | None = "Jane Reporter",
    text: str | None = "The full retrieved article body, not a snippet.",
) -> dict[str, object]:
    """One `results[i]` object, in Exa's documented `ResultWithContent` shape."""
    item: dict[str, object] = {"url": url}
    if title is not None:
        item["title"] = title
    if published_date is not None:
        item["publishedDate"] = published_date
    if author is not None:
        item["author"] = author
    if text is not None:
        item["text"] = text
    return item


def _exa_response(*, results: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(200, json={"results": results})


@pytest.mark.anyio
async def test_exa_retriever_maps_fields_correctly() -> None:
    """The core field-mapping contract: url, publisher (from the URL's
    domain — Exa has no publisher field and `author` is a byline, not an
    organization, FIX 1, ticket AL-512 review), title, `publishedDate` ->
    `published_on`, and the full `text` (not empty, not truncated to a
    snippet). `author` is deliberately present here (an organization name,
    which the adapter used to prefer) to demonstrate it is now ignored
    entirely — see `test_exa_retriever_publisher_is_the_domain_not_a_persons_byline`
    for the case that actually broke.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(
            results=[
                _exa_result(
                    url="https://example.com/northlake-review",
                    title="Ambient Documentation: 14-Month Review",
                    published_date="2026-07-30",
                    author="Northlake Health System",
                    text="The full retrieved body text, not a truncated snippet.",
                )
            ]
        )

    retriever = ExaRetriever(
        "exa-key",
        max_documents=12,
        transport=httpx.MockTransport(handler),
    )

    documents = await retriever.search(["EU AI regulation"])

    assert len(documents) == 1
    document = documents[0]
    assert document.url == "https://example.com/northlake-review"
    assert document.title == "Ambient Documentation: 14-Month Review"
    assert document.publisher == "example.com"
    assert document.published_on == date(2026, 7, 30)
    assert document.text == "The full retrieved body text, not a truncated snippet."


@pytest.mark.anyio
async def test_exa_retriever_maps_a_full_datetime_published_date() -> None:
    """FIX 2 (ticket AL-512 review): every EXAMPLE value in Exa's own
    OpenAPI spec for `publishedDate` is a full ISO 8601 datetime, not the
    bare `YYYY-MM-DD` its prose describes — `date.fromisoformat` raises on
    that string unsliced, so the `[:10]` slice in `_exa_published_on` is
    load-bearing for the actual production format, not defensive
    belt-and-braces. Before this test, no unit test used a datetime-shaped
    `publishedDate` at all — only `"2026-07-30"`, `"not-a-date"`, or absent.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(
            results=[_exa_result(published_date="2023-11-16T01:36:32.547Z")]
        )

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["q"])

    assert documents[0].published_on == date(2023, 11, 16)


@pytest.mark.anyio
async def test_exa_retriever_undated_documents_survive_the_adapter() -> None:
    """`ExaRetriever` must NOT drop an undated document itself — dropping is
    `retrieve()`'s one job (§5.2's "one owner" rule; acceptance criteria).
    A result with no `publishedDate` still comes back from `search()`, with
    `published_on` mapped to `None` rather than the document vanishing.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(results=[_exa_result(published_date=None)])

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["q"])

    assert len(documents) == 1
    assert documents[0].published_on is None


@pytest.mark.anyio
async def test_exa_retriever_unparseable_date_also_survives_as_undated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(results=[_exa_result(published_date="not-a-date")])

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["q"])

    assert documents[0].published_on is None


@pytest.mark.anyio
async def test_exa_retriever_empty_text_survives_the_adapter() -> None:
    """The same "one owner" rule, applied to `retrieve()`'s other drop
    condition: a result with no extracted text maps to `text=""` here
    rather than the document being dropped by this adapter.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(results=[_exa_result(text=None)])

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["q"])

    assert len(documents) == 1
    assert documents[0].text == ""


@pytest.mark.anyio
async def test_exa_retriever_falls_back_to_domain_when_author_is_absent() -> None:
    """Exa's response has no `publisher` field at all (INFERRED mapping,
    ticket AL-523's report): with no `author`, the adapter uses the
    result's own domain, `www.` stripped."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(
            results=[_exa_result(url="https://www.example.com/piece", author=None)]
        )

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["q"])

    assert documents[0].publisher == "example.com"


@pytest.mark.anyio
async def test_exa_retriever_publisher_is_the_domain_not_a_persons_byline() -> None:
    """FIX 1 (ticket AL-512 review): `author` is a BYLINE, not an
    organization — probed with Exa's own spec example values, preferring it
    over the domain produced `publisher = "Dan Milmon"` for a Guardian
    article. `brief_sources.publisher` is `Text` and lands verbatim in the
    rendered Sources block (PRD §3: "the part a learner checks us on"), so a
    person's name there reads as a bug, not a source identity. The domain is
    now the SOLE source, regardless of what `author` says — even when
    `author` is a real person's full name, exactly the shape that used to
    leak through.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(
            results=[
                _exa_result(
                    url="https://www.theguardian.com/technology/article",
                    author="Dan Milmon",
                )
            ]
        )

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["q"])

    assert documents[0].publisher == "theguardian.com"
    assert documents[0].publisher != "Dan Milmon"


@pytest.mark.anyio
async def test_exa_retriever_since_becomes_start_published_date() -> None:
    """`since` (the prior Brief's `published_on`) is passed through as Exa's
    `startPublishedDate` date filter, an ISO 8601 datetime.

    **Passed on the `search()` call, not at construction** (the AL-521/
    AL-523 handoff gap this closes): a construction-time `since` only
    produces the right per-Beat filter if a fresh `ExaRetriever` is built per
    Beat, and production binds exactly one instance for the whole process's
    lifetime (`services/lifecycle.py`) — so `since` now rides `Retriever.
    search(queries, *, since)` on every call instead.
    """
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _exa_response(results=[])

    retriever = ExaRetriever(
        "exa-key",
        max_documents=12,
        transport=httpx.MockTransport(handler),
    )

    await retriever.search(["EU AI regulation"], since=date(2026, 7, 20))

    assert captured[0]["query"] == "EU AI regulation"
    assert captured[0]["startPublishedDate"] == "2026-07-20T00:00:00.000Z"
    assert captured[0]["contents"] == {"text": True}


@pytest.mark.anyio
async def test_exa_retriever_omits_the_date_filter_on_a_beats_first_run() -> None:
    """`since=None` (PRD §3 — a Beat's first-ever run) sends no date filter
    at all, rather than a filter no prior Brief could have derived."""
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _exa_response(results=[])

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    await retriever.search(["q"], since=None)

    assert "startPublishedDate" not in captured[0]


@pytest.mark.anyio
async def test_exa_retriever_sends_the_api_key_header() -> None:
    captured_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(request.headers)
        return _exa_response(results=[])

    retriever = ExaRetriever(
        "secret-exa-key",
        max_documents=12,
        transport=httpx.MockTransport(handler),
    )

    await retriever.search(["q"])

    assert captured_headers[0]["x-api-key"] == "secret-exa-key"


@pytest.mark.anyio
async def test_exa_retriever_pins_neural_search_type() -> None:
    """FIX 7 (ticket AL-512 review): `type` must be pinned to `"neural"`,
    Exa's OpenAPI-documented, deterministically cheaper search type
    (`perRequestPrices.neuralSearch_1_25_results = $0.005`) — leaving it
    unset lets Exa's `auto` default select `deep`/`deep-reasoning`
    (`$0.015`-`$0.075`/request), which made every prior cost claim a lower
    bound rather than a figure.
    """
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _exa_response(results=[])

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    await retriever.search(["q"])

    assert captured[0]["type"] == "neural"


@pytest.mark.anyio
async def test_exa_retriever_clamps_num_results_into_exas_documented_range() -> None:
    """`retrieve()` owns the real, cross-query `max_documents` cap (D14a);
    this only keeps a single request within Exa's own documented `1..100`
    (CONFIRMED FROM DOCS) so a misconfigured cap can't build a request Exa
    would itself reject."""
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _exa_response(results=[])

    retriever = ExaRetriever(
        "exa-key", max_documents=500, transport=httpx.MockTransport(handler)
    )

    await retriever.search(["q"])

    assert captured[0]["numResults"] == 100


@pytest.mark.anyio
async def test_exa_retriever_issues_one_request_per_query_and_concatenates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return _exa_response(
            results=[_exa_result(url=f"https://example.com/{body['query']}")]
        )

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["alpha", "beta"])

    assert sorted(document.url for document in documents) == [
        "https://example.com/alpha",
        "https://example.com/beta",
    ]


@pytest.mark.anyio
async def test_exa_retriever_sizes_each_query_relative_to_max_documents() -> None:
    """FIX 6 (ticket AL-512 review): each query asks for `max_documents /
    len(queries)` (rounded up, with 50% headroom), not a flat
    `max_documents` per query — the old rule over-fetched ~6x at shipped
    config (`max_queries=6`, `max_documents=12`: 6 requests x 12 results
    instead of 6 x 3).
    """
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _exa_response(results=[])

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    await retriever.search([f"query {i}" for i in range(6)])

    # ceil((12 / 6) * 1.5) == 3, for every one of the 6 requests.
    assert [body["numResults"] for body in captured] == [3, 3, 3, 3, 3, 3]


@pytest.mark.anyio
async def test_retrieve_with_exa_samples_across_queries_under_the_cap() -> None:
    """FIX 6 (ticket AL-512 review): `retrieve()`'s first-`max_documents` cap
    must sample ACROSS angles, not drain a single query's results —
    `ExaRetriever.search()`'s round-robin interleave is what makes that
    true, since `Retriever.search()` returns a flat list with no query
    attribution and `retrieve()` itself has no way to interleave.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query_slug = str(body["query"]).replace(" ", "-")
        num_results = int(body["numResults"])  # type: ignore[arg-type]
        return _exa_response(
            results=[
                _exa_result(
                    url=f"https://example.com/{query_slug}-{i}",
                    published_date="2026-07-01",
                )
                for i in range(num_results)
            ]
        )

    retriever = ExaRetriever(
        "exa-key", max_documents=6, transport=httpx.MockTransport(handler)
    )
    plan = QueryPlan(queries=("angle one", "angle two", "angle three"))

    documents = await retrieve(
        retriever, plan, max_documents=3, text_budget_chars=100_000
    )

    represented_queries = {document.url.rsplit("-", 1)[0] for document in documents}
    assert len(documents) == 3
    assert len(represented_queries) > 1, (
        "the capped batch came entirely from one query — interleaving is "
        "not sampling across angles"
    )


# --- ExaRetriever error mapping: every failure -> RetrievalUnavailableError -----
#
# The acceptance criterion, verbatim: "Any transport / auth / quota /
# rate-limit / malformed-response error -> RetrievalUnavailableError, so it
# lands as a visible, retryable `failed` run and NEVER as a Skipped entry or
# an uncited Brief." One test per row of that mapping.


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "case"),
    [
        (400, "unsupported-parameters"),
        (401, "auth"),
        (402, "quota"),
        (403, "quota"),
        (429, "rate-limit"),
        (500, "server"),
        (503, "server"),
    ],
)
async def test_exa_retriever_maps_http_error_status_to_retrieval_unavailable(
    status_code: int, case: str
) -> None:
    """400 (per the spec, unsupported parameters) is deterministic and
    permanent rather than transient, but the status mapping stays uniform
    (ticket AL-512 review, FIX 4's note) — this row just proves it is
    covered, not that its retryability differs from the others.
    """
    del case  # documents which row of §5.7's table this parametrization covers

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status_code, json={"error": "nope"})

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RetrievalUnavailableError):
        await retriever.search(["q"])


@pytest.mark.anyio
async def test_exa_retriever_maps_a_connection_failure_to_retrieval_unavailable() -> (
    None
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RetrievalUnavailableError):
        await retriever.search(["q"])


@pytest.mark.anyio
async def test_exa_retriever_maps_a_timeout_to_retrieval_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RetrievalUnavailableError):
        await retriever.search(["q"])


@pytest.mark.anyio
async def test_exa_retriever_maps_invalid_json_to_retrieval_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"not json at all")

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RetrievalUnavailableError):
        await retriever.search(["q"])


@pytest.mark.anyio
async def test_exa_retriever_maps_a_missing_results_key_to_retrieval_unavailable() -> (
    None
):
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"requestId": "abc"})  # no 'results' key

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RetrievalUnavailableError):
        await retriever.search(["q"])


@pytest.mark.anyio
async def test_exa_retriever_maps_a_non_list_results_to_retrieval_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"results": "not-a-list"})

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RetrievalUnavailableError):
        await retriever.search(["q"])


@pytest.mark.anyio
async def test_exa_retriever_skips_a_result_missing_url_rather_than_raising() -> None:
    """FIX 3 (ticket AL-512 review) supersedes the old behavior here: a
    single malformed result (no `url`) used to fail the WHOLE query
    (`RetrievalUnavailableError`); now it is skipped and `search()` returns
    whatever remains — `[]` when it was the only result. Zero documents is
    `retrieve()`'s "zero documents after the filters" case (§5.7), not this
    adapter's to raise.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(results=[{"title": "no url on this one"}])

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["q"])

    assert documents == []


@pytest.mark.anyio
async def test_exa_retriever_skips_a_malformed_result_and_keeps_the_good_ones() -> None:
    """FIX 3 (ticket AL-512 review): one malformed result (missing `url`)
    among three must not discard the two good, dated, full-text documents
    alongside it — confirmed empirically to raise and drop all three on the
    prior code, presenting the learner "Couldn't reach sources" with a
    Retry that keeps failing while Exa keeps returning that result.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(
            results=[
                _exa_result(url="https://example.com/first"),
                {"title": "no url on this one"},
                _exa_result(url="https://example.com/third"),
            ]
        )

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["q"])

    assert sorted(document.url for document in documents) == [
        "https://example.com/first",
        "https://example.com/third",
    ]


@pytest.mark.anyio
async def test_exa_retriever_skips_a_result_with_an_unparseable_url() -> None:
    """FIX 3 (ticket AL-512 review): a URL malformed enough that `urlparse`
    raises (`ValueError: Invalid IPv6 URL`) inside `_exa_publisher` must
    also be skipped, not escalated into a whole-query failure.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(
            results=[
                _exa_result(url="https://example.com/good"),
                _exa_result(url="https://[bad"),
            ]
        )

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["q"])

    assert [document.url for document in documents] == ["https://example.com/good"]


@pytest.mark.anyio
async def test_exa_retriever_does_not_disguise_a_mapping_bug_as_retrieval_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FIX 4 (ticket AL-512 review): the `try` around the HTTP call must NOT
    also swallow a bug in OUR OWN mapping code. Monkeypatching
    `_document_from_exa_result` to raise `TypeError` — not one of FIX 3's
    two named malformed-result cases (`KeyError`/`ValueError`) — must
    propagate as a real `TypeError`, not get disguised as
    `RetrievalUnavailableError` behind a Retry that could never fix a code
    bug.
    """

    def _broken_mapper(item: object) -> RetrievedDocument:
        del item
        msg = "simulated wrong-arity bug"
        raise TypeError(msg)

    monkeypatch.setattr(
        "aleph.services.retrieval._document_from_exa_result", _broken_mapper
    )

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(results=[_exa_result()])

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(TypeError, match="simulated wrong-arity bug"):
        await retriever.search(["q"])


@pytest.mark.anyio
async def test_exa_retriever_failure_through_retrieve_never_becomes_skipped() -> None:
    """The end-to-end shape of §5.7's load-bearing row: an Exa outage
    propagates through `retrieve()` as `RetrievalUnavailableError`, exactly
    like `_RaisingRetriever`'s case above — never an empty list a caller
    could mistake for "nothing material happened this week"."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    retriever = ExaRetriever(
        "exa-key", max_documents=12, transport=httpx.MockTransport(handler)
    )
    plan = QueryPlan(queries=("q1",))

    with pytest.raises(RetrievalUnavailableError):
        await retrieve(retriever, plan, max_documents=12, text_budget_chars=10_000)


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
async def test_stub_retriever_strips_sentinels_from_document_title_and_text() -> None:
    """FIX 8 (ticket AL-512 review): TDD §11's `[force-no-findings]` lives in
    the Beat topic and therefore in every planned query — it must not flow
    verbatim into a stub document's title or text (e.g. a Source titled
    `Deterministic stub coverage: My Topic [force-no-findings] news since
    …`). AL-560 uses this sentinel in exactly this way, so the strip is
    implemented, not just documented.
    """
    stub = StubRetriever()

    documents = await stub.search(
        ["My Topic [force-no-findings] news since 2026-07-20"]
    )

    assert documents
    for document in documents:
        assert "[force-no-findings]" not in document.title
        assert "[force-no-findings]" not in document.text
        assert "My Topic" in document.title  # the rest of the query survives


@pytest.mark.anyio
async def test_stub_retriever_through_the_whole_pipeline() -> None:
    """`build_query_plan` -> `StubRetriever` -> `retrieve()`, end to end, with
    no network and no API key — the constraint this whole seam exists for.
    """
    plan = build_query_plan(
        "EU AI regulation", "policy and enforcement", date(2026, 7, 20), max_queries=6
    )

    documents = await retrieve(
        StubRetriever(), plan, max_documents=1_000, text_budget_chars=160_000
    )

    assert documents
    assert all(document.published_on is not None for document in documents)
    assert sum(len(document.text) for document in documents) <= 160_000
