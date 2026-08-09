"""Unit tests for ``aleph.services.briefing``'s pure helpers (AL-521, TDD §3).

The claim/spawn/DB-driven pipeline itself (``BriefingService.drain_claimable`` /
``run_research``) is exercised against real Postgres in
``tests/integration/test_briefing.py`` — the same split
``tests/unit/test_generation_service.py`` documents for
``GenerationOrchestrator``: these are the config-free pieces worth pinning
cheaply with no database.

**The source-grep structural guard that used to live here has moved and
changed shape (code-review FIX 8 on AL-521).** A bare
``assert ".search(" not in inspect.getsource(briefing)`` only ever caught one
literal spelling — a bound-method alias, a helper in another module, a second
``Retriever``, or even a comment containing the substring would each defeat
or spuriously trip it. ``tests/integration/test_briefing.py``'s
``test_documents_reaching_researcher_deps_are_capped_and_budget_truncated``
replaces it with a behavioral guard on the INVARIANT instead: a real pipeline
run, a ``Retriever`` fake that records its own calls, and an assertion that
exactly one ``search()`` happened and that what reached ``ResearcherDeps`` is
capped and character-budget-truncated — i.e. that it demonstrably came
through ``services/retrieval.py::retrieve()``, regardless of how the calling
code spells it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from aleph.agents.researcher import Finding, RetrievedDocument
from aleph.services.briefing import (
    _documents_for_survivors,
    _local_today,
    _materialize_sources,
    _render_skip_line,
)

# --------------------------------------------------------------------------- #
# _local_today — D5's arithmetic, reused verbatim from
# services/progress_read.py: (now - tz_offset_minutes).date().
# --------------------------------------------------------------------------- #


def test_local_today_matches_progress_reads_sign_convention() -> None:
    # A positive offset (behind UTC, e.g. US Pacific -420... here using the
    # documented sign: offset is what's subtracted) crossing midnight one way,
    # a negative one (ahead of UTC) crossing it the other — the two-hemisphere
    # discipline this codebase's streak tests already apply to the same
    # formula.
    just_before_utc_midnight = datetime(2026, 8, 3, 23, 30, tzinfo=UTC)

    # tz_offset_minutes = -120 (UTC+2): local time is 01:30 on Aug 4.
    assert _local_today(-120, just_before_utc_midnight) == date(2026, 8, 4)
    # tz_offset_minutes = +480 (UTC-8): local time is 15:30 on Aug 3.
    assert _local_today(480, just_before_utc_midnight) == date(2026, 8, 3)


def test_local_today_defaults_now_to_utc_now() -> None:
    # No frozen clock injected: must not raise, and must return a real date
    # (the production call shape — no test ever exercises the exact value).
    today = _local_today(0, None)
    assert isinstance(today, date)


# --------------------------------------------------------------------------- #
# _render_skip_line — the second inherited contract: SkippedNote.detail is a
# sentence fragment, the service owns the "Nothing material since Brief #N"
# join, and the Brief number never comes from the model.
# --------------------------------------------------------------------------- #


def test_skip_line_joins_prefix_and_detail_with_an_em_dash() -> None:
    line = _render_skip_line(4, "the consultation is still open, closing 11 Sept")
    assert line == (
        "Nothing material since Brief #4 — the consultation is still open, "
        "closing 11 Sept"
    )


def test_skip_line_with_a_prior_brief_and_empty_detail_has_no_dangling_dash() -> None:
    line = _render_skip_line(4, "")
    assert line == "Nothing material since Brief #4"
    assert "—" not in line


def test_skip_line_with_no_prior_brief_is_detail_alone() -> None:
    """A Beat's first-ever run skips: there is no Brief to name at all."""
    line = _render_skip_line(None, "the filing window opened last week")
    assert line == "the filing window opened last week"
    assert "Brief #" not in line


def test_skip_line_with_no_prior_brief_and_empty_detail_is_empty_string_not_none() -> (
    None
):
    """``briefs.skip_line`` is NOT NULL (ck_briefs_skipped_shape) — "nothing to
    say" must render as ``""``, never ``None``."""
    line = _render_skip_line(None, "")
    assert line == ""
    assert line is not None


# --------------------------------------------------------------------------- #
# _documents_for_survivors — inherited contract #1: AnalystDeps.documents must
# be exactly this run's retrieved documents filtered to the URLs any survivor
# cites, never the researcher's full unfiltered batch and never a narrower
# "only new" set that would make AnalystDeps.__post_init__ raise.
# --------------------------------------------------------------------------- #


def _doc(url: str, *, published_on: date = date(2026, 7, 30)) -> RetrievedDocument:
    return RetrievedDocument(
        url=url, publisher="Pub", title="Title", published_on=published_on, text="t"
    )


def _finding(claim: str, source_urls: list[str]) -> Finding:
    return Finding.model_validate(
        {"claim": claim, "detail": "d", "source_urls": source_urls}
    )


def test_documents_for_survivors_keeps_only_cited_urls() -> None:
    doc_a, doc_b, doc_c = _doc("https://a"), _doc("https://b"), _doc("https://c")
    survivors = [_finding("X happened", ["https://a"])]
    result = _documents_for_survivors([doc_a, doc_b, doc_c], survivors)
    assert result == [doc_a]


def test_documents_for_survivors_keeps_a_url_cited_by_more_than_one_survivor_once() -> (
    None
):
    doc_a = _doc("https://a")
    survivors = [
        _finding("X happened", ["https://a"]),
        _finding("Y happened", ["https://a", "https://a"]),
    ]
    result = _documents_for_survivors([doc_a], survivors)
    assert result == [doc_a]


def test_documents_for_survivors_covers_new_plus_prior_url_on_one_survivor() -> None:
    """A surviving finding may legitimately cite one previously-cited URL
    alongside a genuinely new one (``domains/novelty.py``'s "every URL
    already cited", not "any") — AnalystDeps must see BOTH documents, not
    just the new one, or its own construction-time check would raise."""
    doc_new, doc_prior = _doc("https://new"), _doc("https://prior")
    survivors = [_finding("X happened", ["https://new", "https://prior"])]
    result = _documents_for_survivors([doc_new, doc_prior], survivors)
    assert set(d.url for d in result) == {"https://new", "https://prior"}


def test_documents_for_survivors_empty_when_no_survivors() -> None:
    """The padding-test precondition (TDD §5.4/§11): a Skipped run must hand
    AnalystDeps an empty ``documents`` set, not the full retrieved batch."""
    result = _documents_for_survivors([_doc("https://a"), _doc("https://b")], [])
    assert result == []


# --------------------------------------------------------------------------- #
# _materialize_sources — a Source's metadata is never model-written (TDD
# §5.5): joined from the retrieved RetrievedDocuments by URL, in the writer's
# own cited_urls order.
# --------------------------------------------------------------------------- #


def test_materialize_sources_joins_metadata_from_the_retrieved_document() -> None:
    doc = RetrievedDocument(
        url="https://example.com/a",
        publisher="Northlake Health System",
        title="A 14-month review",
        published_on=date(2026, 7, 30),
        text="the full retrieved text",
    )
    sources = _materialize_sources(["https://example.com/a"], [doc])
    assert len(sources) == 1
    source = sources[0]
    assert source.url == "https://example.com/a"
    assert source.publisher == "Northlake Health System"
    assert source.title == "A 14-month review"
    assert source.published_on == date(2026, 7, 30)


def test_materialize_sources_preserves_cited_url_order() -> None:
    doc_a, doc_b = _doc("https://a"), _doc("https://b")
    sources = _materialize_sources(["https://b", "https://a"], [doc_a, doc_b])
    assert [s.url for s in sources] == ["https://b", "https://a"]


def test_materialize_sources_skips_a_cited_url_with_no_matching_document() -> None:
    """Structurally unreachable in production (AnalystDeps's own construction-
    time invariant guarantees every cited URL has a backing document) — this
    pins the defensive branch never invents a Source from nothing rather than
    raising and failing an otherwise-good Brief."""
    sources = _materialize_sources(["https://missing"], [_doc("https://a")])
    assert sources == []


def test_materialize_sources_dedupes_a_repeated_cited_url() -> None:
    """FIX 5 (AL-521 review): nothing upstream of this function dedupes
    ``cited_urls`` — a model listing the same URL twice must fold to ONE
    Source, not two ``brief_sources`` rows at two positions."""
    doc = _doc("https://a")
    sources = _materialize_sources(["https://a", "https://a"], [doc])
    assert len(sources) == 1
    assert sources[0].url == "https://a"


def test_materialize_sources_dedupe_preserves_first_occurrence_among_others() -> None:
    doc_a, doc_b = _doc("https://a"), _doc("https://b")
    sources = _materialize_sources(
        ["https://a", "https://b", "https://a"], [doc_a, doc_b]
    )
    assert [s.url for s in sources] == ["https://a", "https://b"]
