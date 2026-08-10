"""Unit tests for the stub model's researcher/analyst dispatch (Phase 6 TDD
§5.3/§5.4/§5.5/§11, ticket AL-560 code review FIX 2).

Mirrors `test_stub_model_flashcards.py`'s shape: a throwaway
`Agent[None, ResearchResult]`/`Agent[None, BriefResult]` bound to the stub
drives the real dispatch in `services/stub_model.py`, built off the real
prompt builders (`agents/researcher.py::build_researcher_prompt`,
`agents/analyst.py::build_analyst_prompt`) rather than hand-rolled prompt
strings, so these tests exercise the actual contract between the two prompt
builders and the stub's own marker/URL scanning.

Before this file, the researcher/analyst branch — unlike every other stub
branch (`test_stub_model.py`'s outline/lesson, `test_stub_model_flashcards
.py`, `test_stub_model_shaping.py`, `test_stub_model_stream.py`) — had no
unit coverage at all: only e2e (`w29.spec.ts`/`w31.spec.ts`) exercised it,
which turns a stub regression into an opaque 90s Playwright timeout instead
of a fast, attributable failure.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic_ai import Agent

from aleph.agents.analyst import (
    AnalystDeps,
    BriefBody,
    BriefResult,
    SkippedNote,
    build_analyst_prompt,
    validate_brief_result,
)
from aleph.agents.researcher import (
    Finding,
    Findings,
    ResearchResult,
    ResearcherDeps,
    RetrievedDocument,
    build_researcher_prompt,
    validate_research_result,
)
from aleph.services.stub_model import FORCE_NO_FINDINGS, StubModelForcedError, build_stub_model


def _doc(
    url: str = "https://example.com/stub-source/1",
    publisher: str = "Stub Wire",
    title: str = "Example Title",
    published_on: date | None = date(2026, 1, 1),
    text: str = "Some retrieved text.",
) -> RetrievedDocument:
    return RetrievedDocument(
        url=url, publisher=publisher, title=title, published_on=published_on, text=text
    )


def _researcher_agent() -> Agent[None, ResearchResult]:
    # Explicit specialization: ty otherwise mis-infers the agent's output type
    # (mirrors test_stub_model.py's _outline_agent/_lesson_agent).
    return Agent[None, ResearchResult](output_type=ResearchResult, model=build_stub_model())


def _analyst_agent() -> Agent[None, BriefResult]:
    return Agent[None, BriefResult](output_type=BriefResult, model=build_stub_model())


# --- the researcher branch: Findings pass validate_research_result -------------


def test_stub_findings_pass_validate_research_result() -> None:
    docs = [_doc(url="https://example.com/stub-source/1")]
    deps = ResearcherDeps(topic="Rust ownership", guidance=None, documents=docs)
    prompt = build_researcher_prompt(deps)

    result = _researcher_agent().run_sync(prompt).output

    assert isinstance(result, Findings)
    assert result.findings  # a real document was given; the stub finds something
    assert validate_research_result(docs, result) is result


def test_stub_findings_cite_only_the_documents_given() -> None:
    docs = [
        _doc(url="https://example.com/stub-source/1"),
        _doc(url="https://example.com/stub-source/2"),
    ]
    deps = ResearcherDeps(topic="Rust ownership", guidance=None, documents=docs)
    result = _researcher_agent().run_sync(build_researcher_prompt(deps)).output

    assert isinstance(result, Findings)
    available = {d.url for d in docs}
    for finding in result.findings:
        assert finding.source_urls
        assert all(url in available for url in finding.source_urls)


# --- [force-no-findings] yields Findings(findings=[]) ---------------------------


def test_force_no_findings_yields_empty_findings_from_real_documents() -> None:
    # TDD §11: the sentinel must leave retrieval genuinely successful (real,
    # non-empty `documents`) and empty only the *findings* — never a
    # zero-document prompt, which is `services/retrieval.py::StubRetriever`'s
    # own, separate `[force-retrieval-failure]` branch.
    docs = [_doc(url="https://example.com/stub-source/1")]
    deps = ResearcherDeps(
        topic=f"a quiet subject {FORCE_NO_FINDINGS}", guidance=None, documents=docs
    )
    prompt = build_researcher_prompt(deps)
    assert FORCE_NO_FINDINGS in prompt  # the sentinel really did ride into the prompt

    result = _researcher_agent().run_sync(prompt).output

    assert result == Findings(findings=[])
    assert validate_research_result(docs, result) is result


def test_without_the_sentinel_the_same_documents_yield_findings() -> None:
    # The control case: FORCE_NO_FINDINGS, not the mere presence of documents,
    # is what empties the findings list.
    docs = [_doc(url="https://example.com/stub-source/1")]
    deps = ResearcherDeps(topic="a quiet subject", guidance=None, documents=docs)
    result = _researcher_agent().run_sync(build_researcher_prompt(deps)).output
    assert isinstance(result, Findings)
    assert result.findings


# --- the analyst branch: BriefBody passes validate_brief_result ----------------


def test_stub_brief_body_passes_validate_brief_result() -> None:
    docs = [_doc(url="https://example.com/stub-source/1")]
    survivors = [
        Finding(
            claim="Something changed.",
            detail="More detail.",
            source_urls=["https://example.com/stub-source/1"],
            happened_on=None,
        )
    ]
    deps = AnalystDeps(
        topic="Rust ownership",
        level="intermediate",
        guidance=None,
        documents=docs,
        survivors=survivors,
        open_threads=[],
    )
    prompt = build_analyst_prompt(deps)

    result = _analyst_agent().run_sync(prompt).output

    assert isinstance(result, BriefBody)
    assert validate_brief_result(docs, survivors, result) is result


def test_stub_skipped_note_passes_validate_brief_result_when_no_survivors() -> None:
    deps = AnalystDeps(
        topic="a quiet subject",
        level="intermediate",
        guidance=None,
        documents=[],
        survivors=[],
        open_threads=[],
    )
    prompt = build_analyst_prompt(deps)

    result = _analyst_agent().run_sync(prompt).output

    assert isinstance(result, SkippedNote)
    assert validate_brief_result([], [], result) is result


# --- _extract_topic_line must not swallow the whole prompt (FIX 3) -------------


def test_extract_topic_line_reads_the_bare_topic_value() -> None:
    from aleph.services import stub_model

    assert (
        stub_model._extract_topic_line("Topic: Rust ownership\n\nDocuments...")
        == "Rust ownership"
    )


def test_extract_topic_line_takes_the_first_match_when_several_are_present() -> None:
    # A document's own text could in principle contain a line matching the
    # pattern; the builder's own line always comes first (offset 0).
    from aleph.services import stub_model

    text = "Topic: real topic\n\nDocuments you read:\n\n[1] ... Topic: not this one ..."
    assert stub_model._extract_topic_line(text) == "real topic"


def test_extract_topic_line_raises_rather_than_falling_back_to_the_whole_prompt() -> None:
    # FIX 3: the bug found during implementation, pinned. A prompt that does
    # not lead with a bare `Topic: ` line must raise, never silently
    # interpolate the whole prompt (document dump, URLs, everything) into
    # generated text.
    from aleph.services import stub_model

    no_topic_line_prompt = (
        "Documents you read (cite ONLY these URLs):\n\n"
        "[1] Stub Wire — 'Title' (2026-01-01) — https://example.com/stub-source/1\n"
        "Some retrieved text."
    )
    with pytest.raises(StubModelForcedError, match="Topic:"):
        stub_model._extract_topic_line(no_topic_line_prompt)


def test_a_prompt_missing_the_topic_line_raises_end_to_end() -> None:
    # The same failure, exercised through the real agent dispatch rather than
    # calling the private helper directly — proves the raise actually reaches
    # the caller instead of being swallowed somewhere in `_stub_respond`.
    docs = [_doc(url="https://example.com/stub-source/1")]
    prompt = (
        "Documents you read (cite ONLY these URLs):\n\n"
        f"[1] Stub Wire — 'Title' (2026-01-01) — {docs[0].url}\n"
        "Some retrieved text."
    )
    with pytest.raises(StubModelForcedError, match="Topic:"):
        _researcher_agent().run_sync(prompt)


# --- _extract_document_urls: trailing punctuation + Topic/Guidance scoping -----


def test_extract_document_urls_strips_a_trailing_comma() -> None:
    # FIX 4: `agents/analyst.py` renders `source_urls: {', '.join(...)}` — a
    # second URL on the same line leaves a bare comma stuck to the first.
    from aleph.services import stub_model

    text = (
        "    source_urls: https://example.com/stub-source/1, "
        "https://example.com/stub-source/2\n"
    )
    urls = stub_model._extract_document_urls(text)
    assert urls == [
        "https://example.com/stub-source/1",
        "https://example.com/stub-source/2",
    ]


def test_extract_document_urls_ignores_a_pasted_url_topic() -> None:
    # FIX 4: a Beat topic that is itself a pasted URL must never be mistaken
    # for one of the documents this run actually read.
    from aleph.services import stub_model

    text = (
        "Topic: https://not-a-retrieved-document.example/whatever\n\n"
        "Documents you read (cite ONLY these URLs):\n\n"
        "[1] Stub Wire — 'Title' (2026-01-01) — https://example.com/stub-source/1\n"
        "Some retrieved text."
    )
    urls = stub_model._extract_document_urls(text)
    assert urls == ["https://example.com/stub-source/1"]


def test_extract_document_urls_ignores_a_pasted_url_guidance() -> None:
    from aleph.services import stub_model

    text = (
        "Topic: Rust ownership\n\n"
        "Guidance from the learner: see https://not-a-document.example/page\n\n"
        "Documents you read (cite ONLY these URLs):\n\n"
        "[1] Stub Wire — 'Title' (2026-01-01) — https://example.com/stub-source/1\n"
        "Some retrieved text."
    )
    urls = stub_model._extract_document_urls(text)
    assert urls == ["https://example.com/stub-source/1"]


def test_a_url_topic_end_to_end_never_becomes_an_unbacked_citation() -> None:
    # End-to-end version of the above two: a Beat whose Topic is itself a URL
    # must not make the researcher cite a "document" that is not in
    # ResearcherDeps.documents.
    docs = [_doc(url="https://example.com/stub-source/1")]
    deps = ResearcherDeps(
        topic="https://a-pasted-url-topic.example/article", guidance=None, documents=docs
    )
    result = _researcher_agent().run_sync(build_researcher_prompt(deps)).output
    assert isinstance(result, Findings)
    assert validate_research_result(docs, result) is result
