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

import json
from datetime import date
from typing import TYPE_CHECKING

import httpx
import pytest

from aleph.agents.researcher import RetrievedDocument
from aleph.services.retrieval import (
    FORCE_RETRIEVAL_FAILURE,
    ExaRetriever,
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
    """The core field-mapping contract: url, publisher (from `author`), title,
    `publishedDate` -> `published_on`, and the full `text` (not empty, not
    truncated to a snippet)."""

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
        since=None,
        max_documents=12,
        transport=httpx.MockTransport(handler),
    )

    documents = await retriever.search(["EU AI regulation"])

    assert len(documents) == 1
    document = documents[0]
    assert document.url == "https://example.com/northlake-review"
    assert document.title == "Ambient Documentation: 14-Month Review"
    assert document.publisher == "Northlake Health System"
    assert document.published_on == date(2026, 7, 30)
    assert document.text == "The full retrieved body text, not a truncated snippet."


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
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
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
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
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
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["q"])

    assert len(documents) == 1
    assert documents[0].text == ""


@pytest.mark.anyio
async def test_exa_retriever_falls_back_to_domain_when_author_is_absent() -> None:
    """Exa's response has no `publisher` field at all (INFERRED mapping,
    ticket AL-523's report): with no `author`, the adapter falls back to the
    result's own domain, `www.` stripped."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(
            results=[_exa_result(url="https://www.example.com/piece", author=None)]
        )

    retriever = ExaRetriever(
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["q"])

    assert documents[0].publisher == "example.com"


@pytest.mark.anyio
async def test_exa_retriever_since_becomes_start_published_date() -> None:
    """`since` (the prior Brief's `published_on`) is passed through as Exa's
    `startPublishedDate` date filter, an ISO 8601 datetime."""
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _exa_response(results=[])

    retriever = ExaRetriever(
        "exa-key",
        since=date(2026, 7, 20),
        max_documents=12,
        transport=httpx.MockTransport(handler),
    )

    await retriever.search(["EU AI regulation"])

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
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
    )

    await retriever.search(["q"])

    assert "startPublishedDate" not in captured[0]


@pytest.mark.anyio
async def test_exa_retriever_sends_the_api_key_header() -> None:
    captured_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(request.headers)
        return _exa_response(results=[])

    retriever = ExaRetriever(
        "secret-exa-key",
        since=None,
        max_documents=12,
        transport=httpx.MockTransport(handler),
    )

    await retriever.search(["q"])

    assert captured_headers[0]["x-api-key"] == "secret-exa-key"


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
        "exa-key", since=None, max_documents=500, transport=httpx.MockTransport(handler)
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
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
    )

    documents = await retriever.search(["alpha", "beta"])

    assert sorted(document.url for document in documents) == [
        "https://example.com/alpha",
        "https://example.com/beta",
    ]


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
    del case  # documents which row of §5.7's table this parametrization covers

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status_code, json={"error": "nope"})

    retriever = ExaRetriever(
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
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
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RetrievalUnavailableError):
        await retriever.search(["q"])


@pytest.mark.anyio
async def test_exa_retriever_maps_a_timeout_to_retrieval_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    retriever = ExaRetriever(
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RetrievalUnavailableError):
        await retriever.search(["q"])


@pytest.mark.anyio
async def test_exa_retriever_maps_invalid_json_to_retrieval_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"not json at all")

    retriever = ExaRetriever(
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
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
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RetrievalUnavailableError):
        await retriever.search(["q"])


@pytest.mark.anyio
async def test_exa_retriever_maps_a_non_list_results_to_retrieval_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"results": "not-a-list"})

    retriever = ExaRetriever(
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RetrievalUnavailableError):
        await retriever.search(["q"])


@pytest.mark.anyio
async def test_exa_retriever_maps_a_result_missing_url_to_retrieval_unavailable() -> (
    None
):
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return _exa_response(results=[{"title": "no url on this one"}])

    retriever = ExaRetriever(
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RetrievalUnavailableError):
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
        "exa-key", since=None, max_documents=12, transport=httpx.MockTransport(handler)
    )
    plan = QueryPlan(queries=("q1",))

    with pytest.raises(RetrievalUnavailableError):
        await retrieve(retriever, plan, max_documents=12, text_budget_chars=10_000)


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


# --- FIX 1 (ticket AL-512 review): the malformed-fixture matrix ----------------
# The reviewer's 200,000-trial matrix found 5 of 7 malformed shapes returning
# `[]` silently. Each must now raise `RetrievalUnavailableError` at
# construction; the legitimate `results: {"q": []}` shape must NOT.


def test_fixture_retriever_raises_when_queries_key_is_an_empty_list(
    tmp_path: Path,
) -> None:
    """`build_query_plan` always emits at least one query, so a fixture
    recording zero is necessarily corrupt — not a legitimate `[]` replay.
    """
    body = """\
beat: no-queries-beat
queries: []
results:
  "some query": []
"""
    fixtures_dir = _write_fixture(tmp_path, "no-queries-beat", body)

    with pytest.raises(RetrievalUnavailableError, match="no-queries-beat"):
        FixtureRetriever(fixtures_dir, "no-queries-beat")


def test_fixture_retriever_raises_when_queries_key_is_missing(
    tmp_path: Path,
) -> None:
    body = """\
beat: missing-queries-beat
results:
  "some query": []
"""
    fixtures_dir = _write_fixture(tmp_path, "missing-queries-beat", body)

    with pytest.raises(RetrievalUnavailableError, match="missing-queries-beat"):
        FixtureRetriever(fixtures_dir, "missing-queries-beat")


def test_fixture_retriever_raises_when_queries_key_is_misspelled(
    tmp_path: Path,
) -> None:
    """`querys:` (a typo for `queries:`) leaves the real key unset — must
    raise exactly like a wholly missing key, not silently parse as `[]`.
    """
    body = """\
beat: typo-beat
querys:
  - "some query"
results:
  "some query": []
"""
    fixtures_dir = _write_fixture(tmp_path, "typo-beat", body)

    with pytest.raises(RetrievalUnavailableError, match="typo-beat"):
        FixtureRetriever(fixtures_dir, "typo-beat")


def test_fixture_retriever_raises_when_results_mapping_is_null(
    tmp_path: Path,
) -> None:
    body = """\
beat: null-results-beat
queries:
  - "some query"
results: null
"""
    fixtures_dir = _write_fixture(tmp_path, "null-results-beat", body)

    with pytest.raises(RetrievalUnavailableError, match="null-results-beat"):
        FixtureRetriever(fixtures_dir, "null-results-beat")


def test_fixture_retriever_raises_when_results_mapping_is_empty(
    tmp_path: Path,
) -> None:
    """`results: {}` — present but with no query recorded under it at all —
    is a different, and also corrupt, shape from a *present* per-query `[]`.
    """
    body = """\
beat: empty-results-beat
queries:
  - "some query"
results: {}
"""
    fixtures_dir = _write_fixture(tmp_path, "empty-results-beat", body)

    with pytest.raises(RetrievalUnavailableError, match="empty-results-beat"):
        FixtureRetriever(fixtures_dir, "empty-results-beat")


@pytest.mark.anyio
async def test_fixture_retriever_replays_an_explicit_empty_result_as_legitimate(
    tmp_path: Path,
) -> None:
    """The one shape that must NOT raise: `results: {"q": []}` is an
    affirmative recording — a query that was actually executed and
    genuinely returned nothing — and replays as an empty list for that
    query. This is the distinction FIX 1 keeps but names explicitly: a
    missing/absent entry is not a statement about anything; an explicit
    `[]` is.
    """
    body = """\
beat: legitimate-empty-beat
queries:
  - "a query with real results"
  - "a query that truly found nothing"
results:
  "a query with real results":
    - url: "https://example.com/found"
      publisher: "Wire One"
      title: "Something found"
      published_on: "2026-07-01"
      text: "real text"
  "a query that truly found nothing": []
"""
    fixtures_dir = _write_fixture(tmp_path, "legitimate-empty-beat", body)
    fixture_retriever = FixtureRetriever(fixtures_dir, "legitimate-empty-beat")

    documents = await fixture_retriever.search([])

    assert [document.url for document in documents] == ["https://example.com/found"]


@pytest.mark.anyio
async def test_fixture_retriever_normalizes_non_string_query_entries(
    tmp_path: Path,
) -> None:
    """FIX 9 (ticket AL-512 review): `results` keys are `str()`-normalized;
    `queries` entries must be too, or an unquoted numeric query (YAML parses
    `- 2026` as an `int`) raises a spurious str/int mismatch against its
    `results` entry instead of replaying it.
    """
    body = """\
beat: numeric-query-beat
queries:
  - 2026
results:
  "2026":
    - url: "https://example.com/numeric-query"
      publisher: "Wire One"
      title: "A numeric query, recorded unquoted"
      published_on: "2026-07-01"
      text: "text"
"""
    fixtures_dir = _write_fixture(tmp_path, "numeric-query-beat", body)
    fixture_retriever = FixtureRetriever(fixtures_dir, "numeric-query-beat")

    documents = await fixture_retriever.search([])

    assert [document.url for document in documents] == [
        "https://example.com/numeric-query"
    ]


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

    documents = await retrieve(
        fixture_retriever, plan, max_documents=1_000, text_budget_chars=10_000
    )

    assert [document.url for document in documents] == ["https://example.com/shared"]


@pytest.mark.anyio
async def test_fixture_replay_through_retrieve_is_byte_identical_with_truncation(
    tmp_path: Path,
) -> None:
    """FIX 5 (ticket AL-512 review): §11's determinism claim was never
    actually tested against a fixture through `retrieve()`. The prior
    fixture-comparison test reused a **single** `FixtureRetriever` instance
    (so the YAML parsed once) and never called `retrieve()` — so the
    truncation path, the only place bytes could drift, was not exercised.

    This is the real test: two **independently constructed**
    `FixtureRetriever`s over the same file, each driven through `retrieve()`
    with a budget that **actually truncates**, asserting the returned lists
    are equal field by field.
    """
    fixture_body = """\
beat: byte-identical-replay-beat
queries:
  - "query one"
results:
  "query one":
    - url: "https://example.com/long-a"
      publisher: "Wire One"
      title: "A long document"
      published_on: "2026-07-01"
      text: "abcdefghij"
    - url: "https://example.com/long-b"
      publisher: "Wire Two"
      title: "Another long document"
      published_on: "2026-07-02"
      text: "klmnopqrst"
"""
    fixtures_dir = _write_fixture(tmp_path, "byte-identical-replay-beat", fixture_body)
    plan = QueryPlan(queries=("a live query, ignored on replay",))

    # A budget well under the combined 20 chars of retrieved text, so both
    # documents are actually truncated — the path §11 claims is deterministic.
    first_retriever = FixtureRetriever(fixtures_dir, "byte-identical-replay-beat")
    first = await retrieve(
        first_retriever, plan, max_documents=1_000, text_budget_chars=8
    )

    second_retriever = FixtureRetriever(fixtures_dir, "byte-identical-replay-beat")
    second = await retrieve(
        second_retriever, plan, max_documents=1_000, text_budget_chars=8
    )

    assert first_retriever is not second_retriever  # independently constructed
    assert any(len(document.text) < 10 for document in first)  # truncation happened
    assert first == second  # byte-identical, field by field (frozen dataclass eq)


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
