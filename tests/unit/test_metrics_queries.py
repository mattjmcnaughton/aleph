"""Metric-coverage verification: every saved query is computable from the events.

This is the "computable is verified, not assumed" gate (AL-070, PRD §5.7 / §7).
It parses the checked-in Logfire SQL (``queries/logfire/*.sql``) and asserts, for
every query, that:

1. each ``span_name`` it filters on is a **known product event**
   (``events.EVENT_FIELDS``); and
2. every ``attributes ->> 'field'`` it references is in the union of the fields
   those events actually emit — i.e. the emitted attribute set ⊇ the columns each
   query needs.

Combined with ``test_events.py`` (which anchors ``EVENT_FIELDS`` to what the
emitters really log), this proves the §7 metrics are computable from the events as
emitted — not merely asserted to be. It also pins that every §7 metric has a query
and that ``docs/metrics.md`` maps each one, so the doc cannot silently drift from
the query set the human imports into Logfire (AL-103).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aleph import events

# ``tests/unit/test_metrics_queries.py`` → repo root is three parents up.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_QUERIES_DIR = _REPO_ROOT / "queries" / "logfire"
_METRICS_DOC = _REPO_ROOT / "docs" / "metrics.md"

# ``span_name = 'x'`` and ``span_name IN ('a', 'b', ...)`` — the event filters.
_SPAN_EQ = re.compile(r"span_name\s*=\s*'([a-z_]+)'")
_SPAN_IN = re.compile(r"span_name\s+IN\s*\(([^)]*)\)", re.IGNORECASE)
_QUOTED = re.compile(r"'([a-z_]+)'")
# ``attributes ->> 'field'`` — the attribute references.
_ATTR = re.compile(r"attributes\s*->>\s*'([a-z_]+)'")
# Every JSON field-extraction operator, so the coverage check cannot fail open:
# each ``->>`` must be accounted for by an ``_ATTR`` match (see the parse test).
_ARROW = re.compile(r"->>")

# The §7 metrics that MUST have a saved query (the ticket's required set + the
# guardrails shipped alongside). Filenames are the contract docs/metrics.md maps.
_REQUIRED_QUERIES = {
    # Phase 1 (AL-070).
    "activation_rate.sql",
    "first_lesson_activation.sql",
    "path_start_rate.sql",
    "continuation.sql",
    "return_rate.sql",
    "breadth.sql",
    "cost_per_path.sql",
    # Phase 2 — the tutor (AL-240, Phase 2 PRD §7 / TDD §9).
    "tutor_assisted_continuation.sql",
    "tutor_adoption.sql",
    "tutor_repeat_use.sql",
    "tutor_depth.sql",
    "tutor_entry_mix.sql",
    "tutor_check_uptake.sql",
    "tutor_completion_guardrail.sql",
    "tutor_reply_failure_latency.sql",
    # Phase 2B — shaping (AL-340, Phase 2B PRD §7 / TDD §9).
    "shaping_yield.sql",
    "shaping_adoption.sql",
    "proposal_acceptance.sql",
    "edit_shape_mix.sql",
    "undo_rate.sql",
    "depth_to_proposal.sql",
    "shaped_path_completion_guardrail.sql",
    "shaping_reply_failure_latency.sql",
    # Phase 3 — flashcards & spaced repetition (Phase 3 PRD §5 / TDD §9).
    "flashcard_keep_rate.sql",
    "review_queue_completion.sql",
    "review_recall_by_rung.sql",
    "flashcard_return.sql",
}


def _query_files() -> list[Path]:
    files = sorted(_QUERIES_DIR.glob("*.sql"))
    assert files, f"no saved queries found under {_QUERIES_DIR}"
    return files


def _events_referenced(sql: str) -> set[str]:
    events_ref = set(_SPAN_EQ.findall(sql))
    for group in _SPAN_IN.findall(sql):
        events_ref.update(_QUOTED.findall(group))
    return events_ref


def _attributes_referenced(sql: str) -> set[str]:
    return set(_ATTR.findall(sql))


@pytest.mark.parametrize("query", _query_files(), ids=lambda p: p.name)
def test_query_only_references_known_events(query: Path) -> None:
    sql = query.read_text(encoding="utf-8")
    referenced = _events_referenced(sql)
    assert referenced, f"{query.name} filters on no product event"
    unknown = referenced - set(events.EVENT_FIELDS)
    assert not unknown, f"{query.name} references unknown events: {sorted(unknown)}"


@pytest.mark.parametrize("query", _query_files(), ids=lambda p: p.name)
def test_query_attributes_are_all_emitted(query: Path) -> None:
    """Every attribute a query reads is emitted by an event that query filters on.

    The core "computable is verified" assertion: emitted attribute set (union over
    the query's events) ⊇ the attributes the query references.

    Limitation (documented, accepted): coverage is checked against the *union* of
    the query's events' fields, not attributed per event alias. A query that reads
    ``c.attributes ->> 'x'`` where ``x`` lives on a different event it also filters
    on would still pass. Per-alias attribution would need to resolve each
    ``records AS <alias>`` to the event its ``span_name`` predicate pins — more
    machinery than the guarantee is worth; the union bound plus the executed
    replay test (``tests/integration/test_metrics_replay.py``) covers it in
    practice."""
    sql = query.read_text(encoding="utf-8")
    referenced_events = _events_referenced(sql)
    available = set().union(
        *(events.EVENT_FIELDS[event] for event in referenced_events)
    )
    referenced_attrs = _attributes_referenced(sql)
    missing = referenced_attrs - available
    assert not missing, (
        f"{query.name} references attributes no emitted event provides: "
        f"{sorted(missing)} (available from {sorted(referenced_events)}: "
        f"{sorted(available)})"
    )


@pytest.mark.parametrize("query", _query_files(), ids=lambda p: p.name)
def test_every_attribute_reference_is_parsed(query: Path) -> None:
    """Fail loudly if any ``->>`` reference escapes the attribute regex.

    The coverage check is only honest if ``_ATTR`` captures *every* attribute
    reference; a ``->>`` it misses fails **open** (a silently uncovered field).
    Assert one ``attributes ->> 'field'`` match per ``->>`` operator, so an
    unparseable reference (odd spacing, a field name outside ``[a-z_]``) trips this
    instead of slipping through the coverage gate."""
    sql = query.read_text(encoding="utf-8")
    arrows = len(_ARROW.findall(sql))
    parsed = len(_ATTR.findall(sql))
    assert arrows == parsed, (
        f"{query.name}: {arrows} '->>' operators but {parsed} parsed as "
        f"attributes ->> 'field' — an attribute reference is escaping the check"
    )


def test_every_required_metric_has_a_query() -> None:
    present = {path.name for path in _query_files()}
    missing = _REQUIRED_QUERIES - present
    assert not missing, f"missing saved queries for §7 metrics: {sorted(missing)}"


def test_metrics_doc_maps_every_query() -> None:
    """docs/metrics.md maps each saved query, so the human import list stays honest."""
    doc = _METRICS_DOC.read_text(encoding="utf-8")
    for query in _query_files():
        assert query.name in doc, f"{query.name} is not mapped in docs/metrics.md"
