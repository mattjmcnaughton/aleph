"""The retrieval seam (TDD §3, §5.2, D6/D6a/D10/D14a; ticket AL-512).

Three things live here: the `Retriever` `Protocol` every provider implements,
the pure query planner, and `retrieve()` — the single entry point that owns
every invariant a Brief's retrieved text must satisfy, so a second provider
can never ship without them.

**`ExaRetriever` (the live adapter) is NOT built here** — that is AL-523. This
module ships everything the pipeline needs to be fully testable with no API
key and no network: the Protocol, the planner, `retrieve()`,
`RetrievalUnavailableError`, `FixtureRetriever` (evals + integration), and
`StubRetriever` (e2e, beside `services/stub_model.py`).

**Purity note, carried from the TDD verbatim.** `build_query_plan` is a pure
function but lives here rather than in `domains/`, because it encodes
retrieval-provider concepts — a `since` filter, a result cap — and a provider
concept in `domains/` would be the actual layering violation (TDD §3). The
cost is that the layering test does not cover it, so its purity is
*convention*, not enforcement: the compensating control is that it takes no
`session`, performs no I/O, and no test may pass it a fake.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Protocol

from aleph.agents.researcher import RetrievedDocument

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


class RetrievalUnavailableError(RuntimeError):
    """A `Retriever` could not return results for a research run.

    Maps to a `failed` run (D3) — visible, retryable, never a published Brief
    and **never Skipped** (§5.2, §5.7's load-bearing row). It must
    **propagate** out of `retrieve()`/`search()` rather than being caught and
    turned into `[]`: downstream, an empty document list and "nothing
    material happened this week" are the same value — the novelty gate finds
    no survivors and the analyst publishes Skipped either way — so degrading
    an infrastructure failure to `[]` would silently manufacture a Skipped
    entry for a reason that has nothing to do with the subject going quiet.
    """


# --- the query plan (D6a) — PURE: no session, no I/O, no clock -----------------


@dataclass(frozen=True)
class QueryPlan:
    """The frozen output of `build_query_plan` — what a `Retriever` executes.

    Just the queries: `Retriever.search()` takes no separate `since` or cap
    parameter (D6's Protocol shape), so `build_query_plan` folds the period
    start into the query text itself rather than carrying it as a second
    field here.
    """

    queries: tuple[str, ...]


def build_query_plan(
    topic: str,
    guidance: str | None,
    since: date | None,
    *,
    max_queries: int,
) -> QueryPlan:
    """Derive up to `max_queries` search queries for one research run.

    **PURE** (D6a): no `session`, no I/O, no clock — every input is an
    explicit parameter, so the same `(topic, guidance, since, max_queries)`
    always produces the same `QueryPlan`, byte for byte. Its purity is
    *convention*, not enforced by the layering test (see the module
    docstring's purity note), which is exactly why no test may pass it a
    fake and it must never grow a `session` parameter.

    Queries are derived from the Beat's frozen standing orders (`topic` +
    optional `guidance`) plus the period start (`since` — the prior Brief's
    `published_on`, or `None` for a Beat's first-ever run, PRD §3). `since`
    is folded into the query text as a recency phrase rather than passed to
    the `Retriever` as a separate filter, because D6's `Retriever.search()`
    takes only `queries` — a provider that wants a structured date filter
    (Exa's `since`, AL-523) parses it back out of its own query text, or is
    handed it a different way entirely; that adapter's problem, not this
    function's.

    Angles are listed most-valuable-first and truncated at `max_queries`,
    which the caller has already capped at `BRIEF_RETRIEVAL_MAX_QUERIES`
    (config.py, AL-501) — this function enforces the cap itself too, so it
    holds even if a caller passes a larger number by mistake. Deduplicated
    (an empty `guidance` would otherwise let two angles collide), preserving
    the angle order above.
    """
    if max_queries < 1:
        msg = f"max_queries must be at least 1; got {max_queries}."
        raise ValueError(msg)

    period = f"since {since.isoformat()}" if since is not None else "in the past week"

    angles = [topic, f"{topic} — latest developments {period}"]
    if guidance:
        angles.append(f"{topic}: {guidance} — {period}")
    angles.extend(
        [
            f"{topic} news {period}",
            f"{topic} announcements {period}",
            f"{topic} analysis and commentary {period}",
        ]
    )

    queries: list[str] = []
    seen: set[str] = set()
    for angle in angles:
        if angle in seen:
            continue
        seen.add(angle)
        queries.append(angle)
        if len(queries) >= max_queries:
            break
    return QueryPlan(queries=tuple(queries))


# --- the Retriever Protocol (D6) ------------------------------------------------


class Retriever(Protocol):
    """The retrieval seam every provider implements.

    Three implementations across the phase (D6): `ExaRetriever` (live,
    AL-523, not built here), `FixtureRetriever` (evals + integration, below),
    and `StubRetriever` (e2e, below). An agent never sees a `Retriever` — it
    receives `RetrievedDocument`s as plain data in its `Deps` (TDD §3).
    """

    async def search(self, queries: Sequence[str]) -> list[RetrievedDocument]:
        """Return whatever documents `queries` finds — no filtering, no cap.

        Every invariant a Brief's retrieved text must satisfy (dedupe,
        dated-only, the character budget) is `retrieve()`'s job, not the
        provider's — a `Retriever` is free to return duplicates, undated
        documents, or documents the budget cannot afford; `retrieve()` is
        what makes that safe for every implementation at once.
        """
        ...


# --- retrieve() — the single entry point that owns every invariant (§5.2) ------


async def retrieve(
    retriever: Retriever,
    plan: QueryPlan,
    *,
    text_budget_chars: int,
) -> list[RetrievedDocument]:
    """search -> dedupe by URL -> drop undated -> apply the character budget.

    **The single entry point that owns every invariant**, so a second
    provider can never ship without them (§5.2):

    - **Dedupe by URL.** One document legitimately answers several of the
      plan's queries; a caller that ran each query in the plan and
      concatenated results would double-count it. Keeps the first occurrence
      seen (the plan's query order).
    - **Drop undated documents.** PRD §4.4 requires a publication date a
      learner can be shown and can reason about; a document without one
      cannot be a Source, so it must never reach a model (TDD §5.2).
    - **Apply the character budget** (D14a). Even shares across the
      surviving documents, then redistributes what short documents leave
      unused to the documents that need it — see `_apply_text_budget` for
      the exact algorithm and why it can never exceed `text_budget_chars`.
      Truncation is a deterministic **prefix** of each document's own text,
      so two runs of one fixture are byte-identical (§11's acceptance test).

    Raises `RetrievalUnavailableError` if `retriever.search()` raises it —
    this function adds no try/except of its own, so the error propagates
    unchanged rather than being caught and degraded to `[]` (§5.2's
    load-bearing rule).
    """
    documents = await retriever.search(plan.queries)
    deduped = _dedupe_by_url(documents)
    dated = [document for document in deduped if document.published_on is not None]
    return _apply_text_budget(dated, text_budget_chars)


def _dedupe_by_url(documents: Sequence[RetrievedDocument]) -> list[RetrievedDocument]:
    """`documents` with every URL after its first occurrence dropped."""
    seen: set[str] = set()
    deduped: list[RetrievedDocument] = []
    for document in documents:
        if document.url in seen:
            continue
        seen.add(document.url)
        deduped.append(document)
    return deduped


def _apply_text_budget(
    documents: Sequence[RetrievedDocument], text_budget_chars: int
) -> list[RetrievedDocument]:
    """Truncate `documents` so their combined text never exceeds the budget.

    **Max-min fair allocation, the classic water-filling algorithm** — the
    same shape that gives every flow its fair share of bandwidth. Documents
    are visited **shortest text first**; each is allocated
    `min(len(text), remaining_budget // remaining_count)` and that amount is
    then subtracted from `remaining_budget` while `remaining_count` drops by
    one. Visiting shortest-first is what makes the redistribution correct: a
    short document only ever consumes exactly what it needs (never its full
    even share), so every character it does not spend is still in
    `remaining_budget` when the next, longer document's share is computed —
    exactly "even shares, then redistribute what short documents leave
    unused" (§5.2).

    **Why the ceiling can never be exceeded.** At every step the amount
    allocated is `min(length, remaining_budget // remaining_count) <=
    remaining_budget` (integer division never exceeds its dividend for
    `remaining_count >= 1`), and `remaining_budget` is decremented by exactly
    that amount before the next step. So `remaining_budget` is a loop
    invariant that never goes negative, and the total allocated across all
    documents is `text_budget_chars - remaining_budget_at_the_end <=
    text_budget_chars`. This holds for any input, including the adversarial
    one (one enormous document plus many tiny ones) — the tiny ones simply
    consume their real (small) length each, and the one large document
    absorbs whatever is left.

    **Truncation is a deterministic prefix.** Each document's allocation is
    applied as `text[:allocation]` — a plain slice, no randomness, no
    ordering-dependent tie-break beyond the length sort (equal-length
    documents are processed in an arbitrary but *stable* relative order
    because `sorted` is stable and ties do not affect either document's own
    allocation, which depends only on `remaining_budget`/`remaining_count` at
    that point). Two calls with the same input therefore always return
    byte-identical text.

    The returned list preserves the **original** (deduped, dated) order —
    the fair-share computation is an internal detail, not something callers
    should see reflected in document ordering.
    """
    if text_budget_chars < 0:
        msg = f"text_budget_chars must not be negative; got {text_budget_chars}."
        raise ValueError(msg)
    if not documents:
        return []

    order = sorted(range(len(documents)), key=lambda index: len(documents[index].text))
    remaining_budget = text_budget_chars
    remaining_count = len(documents)
    allocation: dict[int, int] = {}
    for index in order:
        share = remaining_budget // remaining_count
        take = min(len(documents[index].text), share)
        allocation[index] = take
        remaining_budget -= take
        remaining_count -= 1

    return [
        replace(document, text=document.text[: allocation[index]])
        for index, document in enumerate(documents)
    ]


# --- FixtureRetriever — evals + integration replay (TDD §5.2, §10, D10) --------


def _seed(text: str) -> int:
    """A stable (cross-process) integer seed from `text` (`stub_model.py`'s trick).

    `hash()` is salted per process; SHA-256 keeps `StubRetriever` deterministic
    across the pytest and Playwright/server processes alike.
    """
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


class FixtureRetriever:
    """Replays a recorded retrieval fixture — evals + integration (TDD §10, D10).

    **Keyed on the Beat**: constructed with the Beat's fixture key (`beat`)
    and the directory holding recorded fixtures, it loads
    `{fixtures_dir}/{beat}.yaml` once, at construction. On `search()` it
    **ignores the `queries` argument it is called with** and instead executes
    the fixture's own recorded `queries` list against its `results` mapping
    — D10's correction to D6a: "replay executes the RECORDED queries and
    never re-derives them." Today `build_query_plan` is pure, so a plan built
    live and the fixture's recorded plan are identical strings; this is
    written the way it is anyway so that D6a's named upgrade (a model
    query-proposer) costs no fixture re-record — the recorded queries stay
    frozen at the moment the fixture was captured, whatever a proposer would
    ask for today.

    **The load-bearing behavior: a miss RAISES `RetrievalUnavailableError`,
    never returns `[]`.** Three ways a fixture can miss, all treated
    identically: the fixture file does not exist for this `beat` (a stale or
    mistyped seed-set/integration-test key); the file exists but is keyed for
    a different Beat (a copy-paste mistake); a query the fixture itself
    recorded has no matching entry under `results` (a malformed or
    hand-edited fixture). Downstream, an empty document list and "nothing
    material happened this week" are the same value — the novelty gate would
    find no survivors and the analyst would publish Skipped either way — so a
    miss that quietly returned `[]` would manufacture a Skipped Brief that
    has nothing to do with the subject actually going quiet (§5.2's
    load-bearing rule, exactly as it applies to `RetrievalUnavailableError`
    in production).

    YAML parsing (`pyyaml`) is imported **inside** `_load_fixture`, not at
    module level: `pyyaml` ships with this project's `evals` dev group only
    (`pydantic-evals`'s own dependency) and is deliberately absent from the
    production image (`Dockerfile`'s `uv sync --no-dev`). `FixtureRetriever`
    is a test/eval-only adapter that nonetheless lives in `services/` and
    ships inside the production wheel (`tests/unit/test_packaging.py`), so a
    top-level `import yaml` here would make importing this whole module —
    reachable from production once `services/briefing.py` exists — depend on
    a package production never installs.
    """

    def __init__(self, fixtures_dir: Path, beat: str) -> None:
        self._beat = beat
        self._path = fixtures_dir / f"{beat}.yaml"
        self._queries, self._results = _load_fixture(self._path, beat)

    async def search(self, queries: Sequence[str]) -> list[RetrievedDocument]:
        del queries  # intentionally ignored — see the class docstring (D10)
        documents: list[RetrievedDocument] = []
        for query in self._queries:
            results = self._results.get(query)
            if results is None:
                msg = (
                    f"retrieval fixture {self._path} (beat {self._beat!r}) "
                    f"recorded the query {query!r} but has no matching entry "
                    "under 'results' — raising rather than replaying [] so a "
                    "malformed fixture cannot manufacture a Skipped Brief "
                    "(TDD §5.2)."
                )
                raise RetrievalUnavailableError(msg)
            documents.extend(results)
        return documents


def _load_fixture(
    path: Path, beat: str
) -> tuple[list[str], dict[str, list[RetrievedDocument]]]:
    """Parse `path`'s YAML into `(queries, results)`, raising on any miss.

    See `FixtureRetriever`'s docstring for the three miss cases this guards.
    """
    if not path.is_file():
        msg = (
            f"no retrieval fixture recorded for beat {beat!r} at {path} — "
            "raising rather than returning [] so a stale or mistyped fixture "
            "key cannot silently manufacture a Skipped Brief (TDD §5.2)."
        )
        raise RetrievalUnavailableError(msg)

    import yaml  # local import — see the class docstring's dependency note.

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    recorded_beat = raw.get("beat")
    if recorded_beat != beat:
        msg = (
            f"retrieval fixture {path} is recorded for beat {recorded_beat!r}, "
            f"not the requested beat {beat!r} — refusing to replay a "
            "mismatched fixture rather than silently returning its (wrong) "
            "documents."
        )
        raise RetrievalUnavailableError(msg)

    queries: list[str] = list(raw.get("queries") or [])
    raw_results: Mapping[str, object] = raw.get("results") or {}
    results = {
        str(query): [_document_from_mapping(item) for item in (items or [])]
        for query, items in raw_results.items()
    }
    return queries, results


def _document_from_mapping(item: Mapping[str, object]) -> RetrievedDocument:
    """One `RetrievedDocument` from a fixture's `results[query][i]` mapping."""
    published_on = item.get("published_on")
    return RetrievedDocument(
        url=str(item["url"]),
        publisher=str(item["publisher"]),
        title=str(item["title"]),
        published_on=date.fromisoformat(str(published_on)) if published_on else None,
        text=str(item["text"]),
    )


# --- StubRetriever — deterministic replacement for e2e (TDD §5.2, §11) ---------

# A *query*-text sentinel, on `services/stub_model.py`'s precedent: since
# `build_query_plan` folds the topic into every query it derives, embedding
# the sentinel in a Beat's topic is enough to make it appear in every query
# string this retriever is asked to search — so it fires reliably however the
# plan phrases its angles. Forces W33's branch (retrieval unavailable ->
# `failed`, never Skipped).
FORCE_RETRIEVAL_FAILURE = "[force-retrieval-failure]"

_STUB_PUBLISHERS = (
    "Stub Wire",
    "Northlake Gazette",
    "The Daily Ledger",
    "Beacon Report",
)

# An arbitrary, fixed epoch the deterministic dates are offset from — any
# fixed date works, since only determinism (not calendar realism) is asked
# of the stub.
_STUB_EPOCH = date(2026, 1, 1)


class StubRetriever:
    """Deterministic `Retriever` for e2e, beside `services/stub_model.py`.

    Seeded from each query's text (the same `hashlib.sha256` trick
    `stub_model.py` uses), so the same plan always yields the same documents
    — a real server process behind Playwright, whose retriever happens to be
    this stub, still behaves reproducibly run to run.

    **`[force-retrieval-failure]`** (a *topic* sentinel, TDD §11): present in
    any query, it raises `RetrievalUnavailableError` before returning
    anything — W33's branch. Stripped from the query text before it would
    otherwise appear in a stub document's title, exactly as `stub_model.py`
    strips its own sentinels from generated text.

    **`[force-no-findings]` is deliberately NOT handled here.** Per TDD §11:
    that sentinel must make the *researcher/analyst* pipeline reject every
    finding via the novelty gate, using documents the gate rejects — "not
    zero documents, a stub returning nothing would prove the easier, wrong
    thing." `StubRetriever`'s ordinary behavior already satisfies "not zero
    documents": it always returns real-looking, dated stub documents for
    every query it is given. Making those documents' *findings* look
    already-covered is `agents/researcher.py`'s stub dispatch to build
    (AL-520+), once the researcher/analyst agents exist — there is nothing
    for this retriever to special-case.
    """

    async def search(self, queries: Sequence[str]) -> list[RetrievedDocument]:
        if any(FORCE_RETRIEVAL_FAILURE in query for query in queries):
            msg = f"forced retrieval failure ({FORCE_RETRIEVAL_FAILURE})"
            raise RetrievalUnavailableError(msg)
        return [_build_stub_document(query) for query in queries]


def _build_stub_document(query: str) -> RetrievedDocument:
    """A deterministic, dated `RetrievedDocument` seeded from `query`."""
    seed = _seed(query)
    publisher = _STUB_PUBLISHERS[seed % len(_STUB_PUBLISHERS)]
    published_on = _STUB_EPOCH + timedelta(days=seed % 365)
    return RetrievedDocument(
        url=f"https://example.com/stub-source/{seed % 1_000_000}",
        publisher=publisher,
        title=f"Deterministic stub coverage: {query}",
        published_on=published_on,
        text=(
            f"This is deterministic stub retrieval text for the query "
            f"{query!r}, seeded so the same query always returns the same "
            "text. " * 20
        ),
    )
