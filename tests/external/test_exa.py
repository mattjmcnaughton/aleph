"""Live-provider contract test for the Exa retrieval adapter (TDD §12, D6/D10;
ticket AL-523).

`services/retrieval.py`'s `ExaRetriever` is exercised end to end against the
real Exa Search API — a drift canary for PRD §4.4's three retrieval
requirements, which a recorded fixture can never notice changing:

1. documents come back with usable **text** (not an empty string, not a
   truncated snippet) — `tests/unit/test_retrieval.py` proves the *mapping*
   (Exa's `text` field flows through unchanged) with a fake transport; only a
   live call proves Exa's own response actually satisfies "full text, not a
   snippet" for `contents: {"text": true}`.
2. `publishedDate` is populated for real search results.
3. the `since` filter (`startPublishedDate`) actually filters — a unit test
   already proves the request carries the field; only a live call proves Exa
   honors it.

Quarantined in `tests/external/`, exactly like `test_generation_contract.py`:
none of `just gate` / `test-integration` / `test-e2e` reach this directory,
and every test here is `@pytest.mark.external`, reachable only through
`just test-external`.

**Marker convention (this directory's contract, restated):** every test is
**keyless-safe** — it skips cleanly when `EXA_API_KEY` is unset, so
`just test-external` never hangs or spends money without credentials present.

**Not exercised here.** Field *mapping* correctness (url/title/publisher/
`published_on`/text, and every error class collapsing to
`RetrievalUnavailableError`) is `tests/unit/test_retrieval.py`'s job, against
a fake HTTP transport — this file only asks whether the real API's *content*
satisfies the three requirements above, which no fixture or fake can answer.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from aleph.config import settings
from aleph.services.retrieval import ExaRetriever

# Evergreen but genuinely active: virtually guaranteed to have both
# deep history (so a `since` filter has something to exclude) and coverage
# within the last week (so the filter test isn't starved of results to
# check). Deliberately not a Beat's real query text — this file tests the
# adapter/provider contract, not `build_query_plan`'s phrasing.
_LIVE_QUERY = "artificial intelligence industry news"

# "Full text, not a snippet" (PRD §4.4, TDD D6): a search-result snippet or
# a `highlights` extract is typically well under this; a real extracted
# article body comfortably clears it. Conservative on purpose — the point is
# to catch "Exa gave us a teaser", not to demand a minimum article length.
_SNIPPET_LENGTH_CEILING = 500


@pytest.mark.external
@pytest.mark.anyio
async def test_live_exa_search_returns_usable_text_and_a_published_date() -> None:
    """PRD §4.4's first two retrieval requirements, against the real API."""
    if not settings.exa_api_key:
        pytest.skip("EXA_API_KEY is unset; skipping live Exa contract test")

    retriever = ExaRetriever(
        settings.exa_api_key,
        since=None,
        max_documents=settings.brief_retrieval_max_documents,
    )

    documents = await retriever.search([_LIVE_QUERY])

    assert documents, "Exa returned no documents for a broad, active topic"

    # Requirement 1: usable text — not empty, not a truncated snippet.
    assert all(document.text for document in documents), (
        "at least one document came back with empty text"
    )
    substantial = [
        document
        for document in documents
        if len(document.text) > _SNIPPET_LENGTH_CEILING
    ]
    assert substantial, (
        "every document's text was under the snippet-length ceiling "
        f"({_SNIPPET_LENGTH_CEILING} chars) — Exa returned snippets, not the "
        "full retrieved body PRD §4.4 requires"
    )

    # Requirement 2: publishedDate populated.
    dated = [document for document in documents if document.published_on is not None]
    assert dated, "no document came back with a populated publishedDate"


@pytest.mark.external
@pytest.mark.anyio
async def test_live_exa_search_since_filter_actually_filters() -> None:
    """PRD §4.4's third requirement: `since` must genuinely constrain
    results, not merely appear in the request payload."""
    if not settings.exa_api_key:
        pytest.skip("EXA_API_KEY is unset; skipping live Exa contract test")

    since = date.today() - timedelta(days=7)
    retriever = ExaRetriever(
        settings.exa_api_key,
        since=since,
        max_documents=settings.brief_retrieval_max_documents,
    )

    documents = await retriever.search([_LIVE_QUERY])

    dated = [document for document in documents if document.published_on is not None]
    assert dated, "no dated documents came back to check the since filter against"

    stale = [
        document
        for document in dated
        if document.published_on is not None and document.published_on < since
    ]
    assert not stale, (
        f"since={since.isoformat()} did not filter: {len(stale)} document(s) "
        f"published before it came back anyway, e.g. {stale[0].url!r} "
        f"({stale[0].published_on})"
    )
