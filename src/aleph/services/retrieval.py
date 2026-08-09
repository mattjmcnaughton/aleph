"""The retrieval seam (TDD §3, §5.2, D6/D6a/D10/D14a; tickets AL-512, AL-523).

Four things live here: the `Retriever` `Protocol` every provider implements,
the pure query planner, `retrieve()` — the single entry point that owns every
invariant a Brief's retrieved text must satisfy, so a second provider can
never ship without them — and the three implementations D6 names: `ExaRetriever`
(live, ticket AL-523), `FixtureRetriever` (evals + integration), and
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
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlparse

import httpx

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
    holds even if a caller passes a larger number by mistake.

    **Queries are unique by construction, with no dedupe step** (FIX 6,
    ticket AL-512 review corrected this — an earlier version carried a
    `seen`-set dedupe on the claim that an empty `guidance` would "let two
    angles collide"; it never could, because `if guidance:` already skips the
    one angle `guidance` participates in when it is falsy, and every one of
    the six templates below is glued to `{topic}` by a *different literal
    connector* (`": "`, `" — latest developments "`, `" news "`, `"
    announcements "`, `" analysis and commentary "`). Since every angle
    shares the identical `{topic}` prefix for one call, two angles are equal
    only if their connector-plus-tail suffixes are equal — and those
    connectors are literally distinct strings that no value of `topic`,
    `guidance`, or `since` can bridge (the first character after `{topic}`
    already differs: `":"` vs `" "`). So no `(topic, guidance, since)` can
    ever make two angles collide, with or without a dedupe;
    `tests/unit/test_retrieval.py` pins this directly, including under
    guidance adversarially chosen to mimic another angle's wording.
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

    return QueryPlan(queries=tuple(angles[:max_queries]))


# --- the Retriever Protocol (D6) ------------------------------------------------


class Retriever(Protocol):
    """The retrieval seam every provider implements.

    Three implementations across the phase (D6): `ExaRetriever` (live,
    below), `FixtureRetriever` (evals + integration, below), and
    `StubRetriever` (e2e, below). An agent never sees a `Retriever` — it
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
    max_documents: int,
    text_budget_chars: int,
) -> list[RetrievedDocument]:
    """search -> dedupe -> drop undated/empty-text -> cap -> character budget.

    **The single entry point that owns every invariant**, so a second
    provider can never ship without them (§5.2):

    - **Dedupe by URL.** One document legitimately answers several of the
      plan's queries; a caller that ran each query in the plan and
      concatenated results would double-count it. Keeps the first occurrence
      seen (the plan's query order).
    - **Drop undated documents.** PRD §4.4 requires a publication date a
      learner can be shown and can reason about; a document without one
      cannot be a Source, so it must never reach a model (TDD §5.2).
    - **Drop documents with no original text.** PRD §4.4's third retrieval
      requirement — "enough of the retrieved text to ground a quote" — has no
      other enforcement point: a provider returning a dated stub with
      `text == ""` must not become a citable Source with nothing behind it
      (FIX 3, ticket AL-512 review). Checked on the *original* text, before
      any truncation.
    - **Cap at `max_documents`** (D14a; **a deliberate amendment to §5.2's
      pseudocode signature**, which shows only `text_budget_chars` — see the
      note below). Keeps the first `max_documents` survivors in the order
      established above (deduped, dated, non-empty; effectively the plan's
      query order, most-valuable-angle-first). That selection rule is
      **deterministic and stable**: it depends only on the retriever's own
      result order, which is fixed for every implementation here
      (`FixtureRetriever` replays a recorded order, `StubRetriever` derives
      its output purely from query text) — never on a runtime detail like
      dict iteration or a random tiebreak — so two runs of the same fixture
      keep the same documents, byte-identical (§11).
    - **Apply the character budget** (D14a). Even shares across the
      documents that survive the cap, then redistributes what short
      documents leave unused to the documents that need it — see
      `_apply_text_budget` for the exact algorithm and why it can never
      exceed `text_budget_chars`. Truncation is a deterministic **prefix** of
      each document's own text, so two runs of one fixture are byte-identical
      (§11's acceptance test). Applied **after** the document cap, so the
      budget divides only among the documents that actually survive to be
      read — not diluted across documents that will be dropped anyway.
    - **Drop documents left with zero characters after the budget.** The
      water-filling allocation can legitimately give a document `0` when the
      budget runs out before reaching it (FIX 3). Its URL must not sit in an
      agent's `Deps` when none of its content entered the model's context —
      PRD §4.4: "a URL that was retrieved but whose content never entered the
      model's context is not a Source."

    **On the `max_documents` parameter (not in §5.2's pseudocode).** §5.2
    states the character budget is D14a's "only enforcement point" for cost;
    that is still true for the *dollar* ceiling. But D14a and TDD §13 also
    name `BRIEF_RETRIEVAL_MAX_DOCUMENTS` as a real configured value, and
    nothing enforced it: the character budget alone does not bound it,
    because it counts only `document.text` — `url`, `publisher`, `title`,
    and `published_on` are unbounded and all enter the researcher's prompt.
    A per-query cap at the `Retriever` cannot reconstruct a *total* cap after
    dedupe either. So `max_documents` is enforced here, inside `retrieve()`,
    alongside the character budget it was always meant to sit beside — this
    is a signature amendment the docs sweep should record, not a drift from
    the TDD.

    Raises `RetrievalUnavailableError` if `retriever.search()` raises it —
    this function adds no try/except of its own, so the error propagates
    unchanged rather than being caught and degraded to `[]` (§5.2's
    load-bearing rule).
    """
    documents = await retriever.search(plan.queries)
    deduped = _dedupe_by_url(documents)
    dated = [document for document in deduped if document.published_on is not None]
    grounded = [document for document in dated if document.text]
    capped = grounded[:max_documents]
    budgeted = _apply_text_budget(capped, text_budget_chars)
    return [document for document in budgeted if document.text]


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
    applied as `text[:allocation]` — a plain slice, no randomness. Two
    equal-length documents *can* land on different allocations from each
    other (at `remaining_budget=11`, `remaining_count=2`, one gets `share =
    11 // 2 = 5` and, once `remaining_count` drops to `1`, the other gets
    whatever is left — `6`): **visiting order decides who gets the extra
    character, so ties are not allocation-order-independent.** What makes
    replay byte-identical anyway is narrower and different: `sorted` is
    stable, so two calls over the *same* input list always visit equal-length
    documents in the *same* relative order, and therefore always split any
    such remainder the same way. Two calls with the same input therefore
    always return byte-identical text — but that conclusion rests on
    `sorted`'s stability plus fixed input order, not on ties being harmless.
    A reader who trusted the old (incorrect) claim that ties "don't affect
    either document's own allocation" could swap in an unstable sort
    believing it free; it is not — see FIX 7, ticket AL-512 review.

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


# --- ExaRetriever — the live adapter (D6; ticket AL-523) -----------------------

_EXA_BASE_URL = "https://api.exa.ai"
_EXA_SEARCH_PATH = "/search"
# Exa's documented ceiling on `numResults` (CONFIRMED FROM DOCS —
# exa-labs/openapi-spec's `/search` request schema: `numResults` is `1..100`,
# default 10). `retrieve()` applies the real, cross-query cap (`max_documents`,
# D14a); this only bounds what one query asks Exa for, so it is never a
# substitute for that cap and exists purely so a misconfigured
# `BRIEF_RETRIEVAL_MAX_DOCUMENTS` cannot build a request Exa would itself
# reject.
_EXA_MAX_NUM_RESULTS = 100
_DEFAULT_TIMEOUT_SECONDS = 30.0


def _exa_request_payload(
    query: str, *, since: date | None, num_results: int
) -> dict[str, object]:
    """The JSON body for one Exa `/search` call (CONFIRMED FROM DOCS shape —
    exa-labs/openapi-spec's `/search` request schema).

    `contents: {"text": true}` asks for the full extracted page text (PRD
    §4.4: "the full text, NOT a snippet") rather than `highlights`, Exa's
    snippet-shaped alternative. `since` becomes `startPublishedDate` — an
    ISO 8601 **datetime**, not a bare date (CONFIRMED FROM DOCS) — and is
    omitted entirely for a Beat's first-ever run (`since is None`, PRD §3),
    asking Exa for its full history rather than filtering to a period start
    that does not exist yet.
    """
    payload: dict[str, object] = {
        "query": query,
        "numResults": max(1, min(num_results, _EXA_MAX_NUM_RESULTS)),
        "contents": {"text": True},
    }
    if since is not None:
        payload["startPublishedDate"] = f"{since.isoformat()}T00:00:00.000Z"
    return payload


def _exa_publisher(author: object, url: str) -> str:
    """The Source's `publisher` — Exa's response has **no such field**
    (INFERRED, not in the documented schema).

    exa-labs/openapi-spec's `/search` response documents `author` (nullable)
    and `url` on every result, but no `publisher`/site-name field at all.
    When `author` is a non-empty string it is the best available byline;
    otherwise the document's own domain (`url`'s netloc, `www.` stripped)
    stands in. `brief_sources.publisher` is `NOT NULL` (TDD §4), so leaving
    a Source with no publisher label at all is not an option this adapter
    can defer to a later layer.
    """
    if isinstance(author, str) and author.strip():
        return author.strip()
    netloc = urlparse(url).netloc
    return netloc.removeprefix("www.") if netloc else "unknown"


def _exa_published_on(raw: object) -> date | None:
    """Parse Exa's `publishedDate` into a `date`, or `None` on anything
    absent or unparseable.

    Documented format is a bare `YYYY-MM-DD`, and the field is nullable
    (CONFIRMED FROM DOCS). **Deliberately lenient, not a raise**:
    `retrieve()` is the one place that drops an undated document (§5.2's
    "one owner" rule) — this adapter must not duplicate that decision, so a
    `null`/missing/malformed date maps to `None` and the document still
    comes back from `search()` for `retrieve()` to filter. Only the first 10
    characters are read, so a full ISO datetime (were Exa ever to send one
    instead of its documented bare date) still parses.
    """
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _document_from_exa_result(item: Mapping[str, object]) -> RetrievedDocument:
    """One `RetrievedDocument` from a single `results[i]` object (CONFIRMED
    FROM DOCS field names — exa-labs/openapi-spec's `ResultWithContent`
    schema: `url`, `title`, `publishedDate`, `author`, `text`).

    `url` is the only field treated as required: its absence (`KeyError`) or
    `item` not being a mapping at all (`TypeError`) means the response does
    not match Exa's documented contract, so it raises rather than fabricate
    a document with no address — the caller (`ExaRetriever._search_one`)
    maps both to `RetrievalUnavailableError`. `title` and `text` fall back to
    `""` when absent: Exa's own schema does not guarantee either is
    populated for every URL (a page can fail content extraction and still be
    a real, listed result), and an empty-text document is `retrieve()`'s to
    drop (§5.2's "drop documents with no original text" rule), not this
    adapter's.
    """
    url = str(item["url"])
    title = str(item.get("title") or "")
    text = str(item.get("text") or "")
    return RetrievedDocument(
        url=url,
        publisher=_exa_publisher(item.get("author"), url),
        title=title,
        published_on=_exa_published_on(item.get("publishedDate")),
        text=text,
    )


class ExaRetriever:
    """The live `Retriever` (D6; ticket AL-523) — Exa's Search API over plain
    `httpx`, not the `exa-py` SDK.

    **Why plain `httpx` over `exa-py`.** `services/openrouter.py` wraps an
    SDK (pydantic-ai's `OpenRouterProvider`) because pydantic-ai already owns
    that whole protocol end to end — the model-calling machinery, not just
    the HTTP request. Exa is a single REST endpoint with no comparable
    runtime dependency anywhere else in this codebase, so a thin HTTP call is
    the smaller surface, and it is also what keeps this seam testable with an
    in-process fake **transport** (`httpx.MockTransport`) rather than an
    SDK-shaped mock — CLAUDE.md's "Fakes over mocks", applied one level below
    the `Retriever` fakes `test_retrieval.py` already uses for `retrieve()`.
    `httpx` itself is not a new dependency at runtime — it already resolves
    transitively via `logfire[httpx]`/`pydantic-ai` — but this ticket
    promotes it to a direct dependency in `pyproject.toml`, on the same
    reasoning `_load_fixture`'s pyyaml note below records: a top-level
    `import httpx` reachable from production should not depend on some other
    package's extra staying exactly as configured.

    **Every transport, auth, quota, rate-limit, or malformed-response error
    becomes `RetrievalUnavailableError`** (§5.7's first row — this ticket's
    whole reason to exist, never a Skipped entry or an uncited Brief).
    `httpx.HTTPError` is the base of every `httpx` failure this call can
    raise: connection-level failures (DNS, TLS, timeout, refused connection —
    every `httpx.RequestError` subclass) *and* HTTP status failures
    (`response.raise_for_status()`, so 401 auth, 402/403 quota, 429
    rate-limit, and 5xx all land here alike — Exa's OpenAPI spec documents no
    per-code error body worth distinguishing, CONFIRMED FROM DOCS: "the
    specification does not include explicit error response schemas for 401,
    429, or 500"). A response that parses as JSON but does not match the
    documented shape — no `results` key, `results` not a list, an item
    missing `url` — raises `KeyError`/`TypeError`; a body that is not valid
    JSON at all raises `ValueError` (`json.JSONDecodeError`, a `ValueError`
    subclass). All of these are caught in one `except` and re-raised as
    `RetrievalUnavailableError`, so nothing here can surface a bare
    `httpx`/`json` exception past this class.

    **Undated and empty-text documents are NOT dropped here** — `retrieve()`
    owns both (§5.2's "one owner" rule; see `_exa_published_on` and
    `_document_from_exa_result`'s docstrings).

    `since` and `max_documents` are bound at construction (matching
    `FixtureRetriever`'s `beat`): the `Retriever.search(queries)` Protocol
    takes only query strings, so anything a call needs beyond that must live
    on the instance. `transport` exists solely for tests
    (`httpx.MockTransport`) — production leaves it `None` and gets `httpx`'s
    real transport.
    """

    def __init__(
        self,
        api_key: str,
        *,
        since: date | None,
        max_documents: int,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._since = since
        self._max_documents = max_documents
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def search(self, queries: Sequence[str]) -> list[RetrievedDocument]:
        """One Exa `/search` call per query, sequentially, concatenated.

        Sequential rather than fanned out with `asyncio.gather`: a plan is
        capped at `BRIEF_RETRIEVAL_MAX_QUERIES` (6, config.py) queries, and
        either way the first failure becomes the same
        `RetrievalUnavailableError` — concurrency here would buy latency, not
        correctness, at the cost of a `TaskGroup`/`return_exceptions` dance
        for a loop this short.
        """
        documents: list[RetrievedDocument] = []
        async with httpx.AsyncClient(
            base_url=_EXA_BASE_URL,
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            for query in queries:
                documents.extend(await self._search_one(client, query))
        return documents

    async def _search_one(
        self, client: httpx.AsyncClient, query: str
    ) -> list[RetrievedDocument]:
        """One Exa `/search` round trip, mapped to `RetrievedDocument`s or
        raised as `RetrievalUnavailableError` — see the class docstring for
        the full error-mapping table this `except` clause implements."""
        payload = _exa_request_payload(
            query, since=self._since, num_results=self._max_documents
        )
        try:
            response = await client.post(
                _EXA_SEARCH_PATH,
                json=payload,
                headers={"x-api-key": self._api_key},
            )
            response.raise_for_status()
            data = response.json()
            results = data["results"]
            if not isinstance(results, list):
                msg = f"'results' is not a list (got {type(results).__name__})"
                raise TypeError(msg)
            return [_document_from_exa_result(item) for item in results]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            msg = f"Exa search failed for query {query!r}: {exc}"
            raise RetrievalUnavailableError(msg) from exc


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
    never returns `[]`.** Five ways a fixture can miss, all treated
    identically: the fixture file does not exist for this `beat` (a stale or
    mistyped seed-set/integration-test key); the file exists but is keyed for
    a different Beat (a copy-paste mistake); the fixture recorded no queries
    at all — an empty, missing, or misspelled `queries` key (`build_query_plan`
    always emits at least one query, so this can never be a legitimate
    recording); the fixture's `results` mapping is itself missing, `null`, or
    empty (no query has anything recorded for it); or a query the fixture
    *did* record has no matching entry under `results` (a malformed or
    hand-edited fixture missing just that one key). Downstream, an empty
    document list and "nothing material happened this week" are the same
    value — the novelty gate would find no survivors and the analyst would
    publish Skipped either way — so a miss that quietly returned `[]` would
    manufacture a Skipped Brief that has nothing to do with the subject
    actually going quiet (§5.2's load-bearing rule, exactly as it applies to
    `RetrievalUnavailableError` in production).

    **The one shape that is NOT a miss:** an explicit `results: {"some
    query": []}` entry. That is an affirmative recording — a query that was
    actually executed and genuinely returned nothing — and replays as an
    empty list for that query, same as it would live. A missing/absent key is
    not a statement about anything; an explicit `[]` is. Do not "fix" that
    distinction into a raise (FIX 1, ticket AL-512 review).

    YAML parsing (`pyyaml`) is imported **inside** `_load_fixture`, not at
    module level. **This is not because pyyaml is absent from the production
    image** — verified via `uv export --no-dev --frozen | grep -i pyyaml`:
    `pyyaml==6.0.3` IS resolved into the production dependency set today, a
    transitive dependency of `pydantic-settings` and `uvicorn[standard]`,
    both direct `[project].dependencies` (not the `evals` dev group). A
    top-level `import yaml` here would work in production as shipped right
    now. The import is still deferred, deliberately: depending on a
    transitive extra of two other packages — neither of which owes this
    fixture parser a YAML dependency — is fragile in a way a direct
    dependency is not. Either package could drop pyyaml between versions
    (`uvicorn`'s `standard` extra in particular is exactly the kind of grab
    bag that trims sub-dependencies across majors) with nothing in `just
    gate` noticing, since `FixtureRetriever` is eval/integration-only and not
    exercised there. Keeping the import local means that if pyyaml ever does
    disappear from the resolved set, only `FixtureRetriever` (and only when
    actually used) breaks loudly with an `ImportError` — not this whole
    module's importability from `services/briefing.py`, which is reachable
    from production. `FixtureRetriever` is a test/eval-only adapter that
    nonetheless lives in `services/` and ships inside the production wheel
    (`tests/unit/test_packaging.py`), which is what makes that blast radius
    worth containing.
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

    See `FixtureRetriever`'s docstring for the miss cases this guards. Two
    rules worth restating here, at the point they are enforced:

    - **Zero recorded queries is always corrupt.** `build_query_plan` rejects
      `max_queries < 1` and always emits at least one query, so an empty,
      missing, or misspelled `queries` key can never be a legitimate
      recording — whatever produced it, it raises.
    - **An explicit `results: {"q": []}` entry is legitimate** and is
      replayed as `[]` for that query. Only a `results` mapping that is
      itself missing, `null`, or empty (`{}`) is corrupt — a fixture where no
      query has anything recorded. Do not collapse this into a raise; it is
      the one case this function must NOT treat as a miss.
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

    queries_raw = raw.get("queries")
    if not queries_raw:
        msg = (
            f"retrieval fixture {path} (beat {beat!r}) recorded no queries — "
            "the 'queries' key is missing, misspelled, or empty. "
            "`build_query_plan` always emits at least one query, so this "
            "fixture cannot be a legitimate recording; raising rather than "
            "replaying [] so a malformed fixture cannot manufacture a "
            "Skipped Brief (TDD §5.2)."
        )
        raise RetrievalUnavailableError(msg)
    # FIX 9 (ticket AL-512 review): normalize the same way `results` keys
    # are normalized below, so an unquoted numeric query (`- 2026`) does not
    # raise a spurious str/int mismatch against its `results` entry.
    queries: list[str] = [str(query) for query in queries_raw]

    raw_results: Mapping[str, object] = raw.get("results") or {}
    if not raw_results:
        msg = (
            f"retrieval fixture {path} (beat {beat!r}) has no 'results' "
            "mapping — it is missing, null, or empty, so no query has "
            "anything recorded for it. Raising rather than replaying [] so "
            "a malformed fixture cannot manufacture a Skipped Brief (TDD "
            '§5.2). (An explicit `results: {"some query": []}` entry is '
            "different and legitimate — see this function's docstring.)"
        )
        raise RetrievalUnavailableError(msg)
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

# Every `[force-*]` bracket sentinel this stub might be asked to search,
# stripped from generated text — `stub_model.py`'s rule (`_SENTINEL_RE` /
# `clean_topic`), reimplemented locally rather than imported so this module
# does not pull in `stub_model.py`'s heavier `pydantic_ai.models.function`
# machinery for one regex. Generic (`[force-<anything>]`) rather than an
# enumerated list, on purpose: it must catch every *topic* sentinel that can
# ride through `build_query_plan` into a query string, including ones this
# module never inspects by name — `[force-no-findings]` (TDD §11) lives in
# the Beat topic and therefore in every planned query, exactly like
# `FORCE_RETRIEVAL_FAILURE` does, even though only the latter is acted on
# below.
_SENTINEL_RE = re.compile(r"\[force-[a-z0-9-]+\]")


def _strip_sentinels(text: str) -> str:
    """`text` with every `[force-*]` sentinel removed and whitespace collapsed.

    Matches `stub_model.py`'s `clean_topic`: no sentinel should reach
    generated prose. Collapsing whitespace after the strip avoids leaving a
    double space where the bracket used to sit.
    """
    return " ".join(_SENTINEL_RE.sub("", text).split())


class StubRetriever:
    """Deterministic `Retriever` for e2e, beside `services/stub_model.py`.

    Seeded from each query's text (the same `hashlib.sha256` trick
    `stub_model.py` uses), so the same plan always yields the same documents
    — a real server process behind Playwright, whose retriever happens to be
    this stub, still behaves reproducibly run to run.

    **`[force-retrieval-failure]`** (a *topic* sentinel, TDD §11): present in
    any query, it raises `RetrievalUnavailableError` before returning
    anything — W33's branch.

    **Every `[force-*]` sentinel is stripped from the query text before it
    would otherwise appear in a stub document's title or text**, exactly as
    `stub_model.py` strips its own sentinels from generated text (see
    `_strip_sentinels`). `[force-retrieval-failure]` itself never reaches
    that step (the branch above raises first), but TDD §11's
    `[force-no-findings]` lives in the Beat topic and therefore in every
    planned query — without the strip it would flow verbatim into a stub
    document's title (`Deterministic stub coverage: My Topic
    [force-no-findings] news since …`), which is not stripped anywhere else
    in this pipeline.

    **`[force-no-findings]` is deliberately NOT otherwise handled here.** Per
    TDD §11: that sentinel must make the *researcher/analyst* pipeline reject
    every finding via the novelty gate, using documents the gate rejects —
    "not zero documents, a stub returning nothing would prove the easier,
    wrong thing." `StubRetriever`'s ordinary behavior already satisfies "not
    zero documents": it always returns real-looking, dated stub documents for
    every query it is given (with the sentinel scrubbed from their title and
    text, per above). Making those documents' *findings* look
    already-covered is `agents/researcher.py`'s stub dispatch to build
    (AL-520+), once the researcher/analyst agents exist — there is nothing
    more for this retriever to special-case.
    """

    async def search(self, queries: Sequence[str]) -> list[RetrievedDocument]:
        if any(FORCE_RETRIEVAL_FAILURE in query for query in queries):
            msg = f"forced retrieval failure ({FORCE_RETRIEVAL_FAILURE})"
            raise RetrievalUnavailableError(msg)
        return [_build_stub_document(query) for query in queries]


def _build_stub_document(query: str) -> RetrievedDocument:
    """A deterministic, dated `RetrievedDocument` seeded from `query`.

    Seeded from the **raw** `query` (sentinels and all) so the seed — and
    therefore the publisher/date/URL — stays stable regardless of stripping;
    only the learner-visible title and text go through `_strip_sentinels`.
    """
    seed = _seed(query)
    publisher = _STUB_PUBLISHERS[seed % len(_STUB_PUBLISHERS)]
    published_on = _STUB_EPOCH + timedelta(days=seed % 365)
    clean_query = _strip_sentinels(query)
    return RetrievedDocument(
        url=f"https://example.com/stub-source/{seed % 1_000_000}",
        publisher=publisher,
        title=f"Deterministic stub coverage: {clean_query}",
        published_on=published_on,
        text=(
            f"This is deterministic stub retrieval text for the query "
            f"{clean_query!r}, seeded so the same query always returns the "
            "same text. " * 20
        ),
    )
