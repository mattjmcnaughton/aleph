"""Unit tests for ``scripts/record_retrieval_fixtures.py`` (code-review FIX 4
on AL-550).

``docs/evals.md`` says the recorder is "written, **tested for its argument
handling and idempotency**, and ready to run the moment the key lands" — but
until this file, nothing under ``tests/`` referenced it at all. Both
behaviours were true (verified by hand, per the review), so the claim was
correct; the word "tested" was not. These tests cover exactly what that
sentence promises, cheaply and with no key: a fake retriever stands in for
the live Exa call (monkeypatched onto ``aleph.services.retrieval.
ExaRetriever``, which ``_record_one``'s own local import reads), and
``tmp_path`` stands in for ``evals/fixtures/retrieval/``.

A second group pins code-review FIX 6 (dedupe-by-URL and the per-document
text budget) directly against ``_record_one``, since this is the file that
changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from aleph.agents.researcher import RetrievedDocument
from scripts import record_retrieval_fixtures as recorder

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from pathlib import Path


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeExaRetriever:
    """A deterministic, offline stand-in for ``ExaRetriever``.

    Constructed exactly the way ``_record_one`` constructs the real thing
    (``ExaRetriever(api_key, max_documents=max_documents)``) — ``since`` rides
    on ``search`` rather than the constructor, because one retriever instance
    serves every query and production threads the plan's own ``since`` the
    same way. ``since_calls`` records what each search was asked for, so
    monkeypatching the name in ``aleph.services.retrieval`` is a drop-in
    swap. ``calls`` is a list shared across every instance created in one
    test (via the factory below) so a test can assert whether the retriever
    was ever actually asked anything — the one signal that distinguishes
    "skipped" from "recorded".
    """

    def __init__(self, api_key: str, *, max_documents: int) -> None:
        self.api_key = api_key
        self.max_documents = max_documents

    async def search(
        self, queries: Sequence[str], *, since: date | None = None
    ) -> list[RetrievedDocument]:
        raise NotImplementedError  # overridden per-instance by the factory


def _fake_retriever_factory(
    calls: list[list[str]],
    *,
    documents_per_query: dict[str, list[RetrievedDocument]] | None = None,
) -> type[_FakeExaRetriever]:
    """Build a ``_FakeExaRetriever`` subclass bound to ``calls`` and an
    optional per-query document map (default: one synthetic document per
    query, named after the query so distinct queries never collide)."""

    class _Bound(_FakeExaRetriever):
        async def search(
            self, queries: Sequence[str], *, since: date | None = None
        ) -> list[RetrievedDocument]:
            calls.append(list(queries))
            self.since = since
            if documents_per_query is not None:
                return list(documents_per_query.get(queries[0], []))
            query = queries[0]
            return [
                RetrievedDocument(
                    url=f"https://example.com/{abs(hash(query))}",
                    publisher="example.com",
                    title=f"Result for {query!r}",
                    published_on=self.since,
                    text="synthetic article text",
                )
            ]

    return _Bound


@pytest.fixture
def _no_network(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Point the recorder at a fresh ``tmp_path`` fixtures dir and a fake
    retriever, and return the list every ``search()`` call is recorded into.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "aleph.services.retrieval.ExaRetriever", _fake_retriever_factory(calls)
    )
    return calls


def _set_key(monkeypatch: pytest.MonkeyPatch, key: str = "") -> None:
    from aleph.config import settings

    monkeypatch.setattr(settings, "exa_api_key", key)


# --- argument handling -----------------------------------------------------------


def test_no_key_exits_two_and_never_touches_the_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    _no_network: list[list[str]],
) -> None:
    _set_key(monkeypatch, "")
    monkeypatch.setattr(recorder, "BRIEF_FIXTURES_DIR", tmp_path)

    exit_code = recorder.main([])

    assert exit_code == 2
    assert "EXA_API_KEY" in capsys.readouterr().err
    assert _no_network == []  # never even got to construct a retriever


def test_only_with_no_matching_beat_fixture_exits_two(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    _no_network: list[list[str]],
) -> None:
    _set_key(monkeypatch, "test-key")
    monkeypatch.setattr(recorder, "BRIEF_FIXTURES_DIR", tmp_path)

    exit_code = recorder.main(["--only", "this-beat-does-not-exist"])

    assert exit_code == 2
    assert "no matching cases" in capsys.readouterr().err
    assert _no_network == []


def test_only_filters_to_exactly_the_named_beat_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _no_network: list[list[str]],
) -> None:
    _set_key(monkeypatch, "test-key")
    monkeypatch.setattr(recorder, "BRIEF_FIXTURES_DIR", tmp_path)

    exit_code = recorder.main(["--only", "rust-async-runtimes-advanced"])

    assert exit_code == 0
    written = sorted(p.name for p in tmp_path.glob("*.yaml"))
    assert written == ["rust-async-runtimes-advanced.yaml"]
    # One `search()` call per query in that Beat's plan alone.
    assert _no_network, "the fake retriever was never called"


def test_only_is_repeatable_and_records_every_named_beat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _no_network: list[list[str]],
) -> None:
    _set_key(monkeypatch, "test-key")
    monkeypatch.setattr(recorder, "BRIEF_FIXTURES_DIR", tmp_path)

    exit_code = recorder.main(
        [
            "--only",
            "rust-async-runtimes-advanced",
            "--only",
            "fed-rate-policy-beginner",
        ]
    )

    assert exit_code == 0
    written = sorted(p.name for p in tmp_path.glob("*.yaml"))
    assert written == [
        "fed-rate-policy-beginner.yaml",
        "rust-async-runtimes-advanced.yaml",
    ]


# --- idempotency -------------------------------------------------------------------


def test_an_existing_fixture_is_skipped_without_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    _no_network: list[list[str]],
) -> None:
    _set_key(monkeypatch, "test-key")
    monkeypatch.setattr(recorder, "BRIEF_FIXTURES_DIR", tmp_path)
    existing = tmp_path / "rust-async-runtimes-advanced.yaml"
    existing.write_text(
        "beat: rust-async-runtimes-advanced\nqueries: [x]\nresults: {x: []}\n"
    )

    exit_code = recorder.main(["--only", "rust-async-runtimes-advanced"])

    assert exit_code == 0
    assert "skip" in capsys.readouterr().out
    # Untouched: still exactly the sentinel content this test wrote.
    assert "queries: [x]" in existing.read_text()
    # The idempotency this proves: running twice in a row costs nothing —
    # the retriever is never even constructed for an already-recorded Beat.
    assert _no_network == []


def test_force_re_records_an_existing_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _no_network: list[list[str]],
) -> None:
    _set_key(monkeypatch, "test-key")
    monkeypatch.setattr(recorder, "BRIEF_FIXTURES_DIR", tmp_path)
    existing = tmp_path / "rust-async-runtimes-advanced.yaml"
    existing.write_text(
        "beat: rust-async-runtimes-advanced\nqueries: [x]\nresults: {x: []}\n"
    )

    exit_code = recorder.main(["--only", "rust-async-runtimes-advanced", "--force"])

    assert exit_code == 0
    rewritten = yaml.safe_load(existing.read_text())
    assert rewritten["beat"] == "rust-async-runtimes-advanced"
    assert rewritten["queries"] != ["x"]  # the real query plan, not the sentinel
    assert _no_network, "the fake retriever was never called despite --force"


def test_running_twice_in_a_row_without_force_costs_nothing_the_second_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _no_network: list[list[str]],
) -> None:
    _set_key(monkeypatch, "test-key")
    monkeypatch.setattr(recorder, "BRIEF_FIXTURES_DIR", tmp_path)

    first = recorder.main(["--only", "rust-async-runtimes-advanced"])
    calls_after_first = len(_no_network)
    second = recorder.main(["--only", "rust-async-runtimes-advanced"])

    assert (first, second) == (0, 0)
    assert calls_after_first > 0
    assert len(_no_network) == calls_after_first  # no new calls on the re-run


# --- FIX 6: dedupe-by-URL and the per-document text budget -----------------------


@pytest.mark.anyio
async def test_record_one_dedupes_by_url_across_queries_and_truncates_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same URL returned by two different queries is written once, under
    the first query that found it; every kept document's text is capped at
    the configured budget."""
    shared = RetrievedDocument(
        url="https://example.com/shared",
        publisher="example.com",
        title="Shared across two queries",
        published_on=None,
        text="x" * 500,
    )
    only_in_second = RetrievedDocument(
        url="https://example.com/second-only",
        publisher="example.com",
        title="Only in the second query",
        published_on=None,
        text="y" * 500,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "aleph.services.retrieval.ExaRetriever",
        _fake_retriever_factory(
            calls,
            documents_per_query={
                "query one": [shared],
                "query two": [shared, only_in_second],
            },
        ),
    )
    from datetime import date

    from aleph.services.retrieval import QueryPlan

    monkeypatch.setattr(
        "aleph.services.retrieval.build_query_plan",
        lambda *a, **k: QueryPlan(queries=("query one", "query two")),
    )

    out_path = tmp_path / "probe.yaml"
    await recorder._record_one(
        beat="probe",
        topic="anything",
        guidance=None,
        since=date(2026, 1, 1),
        max_queries=6,
        max_documents=12,
        text_budget_chars=100,
        out_path=out_path,
        api_key="test-key",
    )

    payload = yaml.safe_load(out_path.read_text())
    all_urls = [doc["url"] for docs in payload["results"].values() for doc in docs]
    # The shared URL appears exactly once across the whole file, not twice.
    assert all_urls.count("https://example.com/shared") == 1
    assert all_urls.count("https://example.com/second-only") == 1
    # It is recorded under the FIRST query that found it, not the second.
    assert [doc["url"] for doc in payload["results"]["query one"]] == [
        "https://example.com/shared"
    ]
    assert [doc["url"] for doc in payload["results"]["query two"]] == [
        "https://example.com/second-only"
    ]
    # Every recorded document's text is capped at the budget.
    for docs in payload["results"].values():
        for doc in docs:
            assert len(doc["text"]) <= 100
