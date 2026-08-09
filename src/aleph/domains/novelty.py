"""Pure novelty gate: which findings are new enough to publish (TDD D9/§5.4).

**Skipped is computed here and nowhere else** (CONTEXT.md: **Skipped**). PRD
§4.6's rule — a Brief with no survivors is not a laundry slot for "we failed
to run", it is "the analyst found nothing" — is not a `services/` `if`
statement; it is the empty list :func:`filter_new` returns. Every caller that
needs to know whether a research run is Skipped asks this function the same
question, once: ``services/briefing.py`` (AL-521) and the eval harness's
layer-1 pre-filters (AL-550) both import :func:`filter_new` **unchanged** —
two spellings of the novelty gate would be a bug (TDD D9's whole argument for
making this a `domains/` module rather than judge spend).

Stdlib only, frozen inputs, no ORM, no I/O, no session — the
``domains/__init__.py`` contract verbatim.

**Typing note — why this file defines a `Protocol` instead of a frozen input
dataclass.** Every other `domains/` module facing a type it does not own
(``grading.Attempt``, ``engagement.LessonEngagement``, ``scheduling.Candidate``)
defines its own frozen dataclass and lets a service map rows into it, because
those functions *consume* the input and hand back plain values of the
domain's own choosing. :func:`filter_new` cannot follow that precedent
verbatim: its *return value* is the survivors themselves — TDD §5.4's
signature returns ``list[Finding]``, not a converted copy — because
downstream (``agents/analyst.py``'s ``Deps.documents``, the writer's citation
set) needs the survivors' full shape (``detail``, ``happened_on``, …), most of
which this module has no business knowing about. `Finding` itself lives in
`agents/researcher.py` (AL-520, not yet built), and `domains/` imports no
application layer and no third-party model library (`pydantic`) either — so
copying fields into a local dataclass would both lose information the caller
needs back and require a second, hand-maintained mapping that could drift
from the real `Finding`. A :class:`typing.Protocol`, bound to
``filter_new``'s own type parameter (PEP 695 generic syntax, ``def
filter_new[FindingT: _Finding](...)``), is the structural-typing escape hatch
for exactly this shape: it states the two attributes this module actually
reads (``claim``, ``source_urls``) without importing the concrete type, and
the type parameter lets ``filter_new`` return the *same* objects it was given
rather than a narrower stand-in. Both callers (AL-521, AL-550) therefore pass their
own `Finding` objects straight through, unchanged, which is the ticket's
"two callers, one spelling" constraint satisfied at the type level as well as
the value level.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence, Set


class _Finding(Protocol):
    """The two attributes this module reads off a finding — nothing else.

    Structural, not nominal: any object with a **readable** ``claim`` and
    ``source_urls`` satisfies this, `Finding` included, with no import of
    `agents/researcher.py` (a later ticket, and an application-layer module
    `domains/` may never import regardless). Spelled as read-only properties
    rather than plain attribute annotations deliberately: a plain
    ``source_urls: Sequence[str]`` annotation makes the protocol invariant in
    that attribute's type, which would reject `Finding`'s real
    ``source_urls: list[str]`` (or a test double's ``tuple[str, ...]``) as
    "not exactly `Sequence[str]`" even though both are perfectly readable as
    one. This module only ever reads these two fields, never assigns them, so
    read-only is also the honest contract, not just the one that satisfies
    the type checker.
    """

    @property
    def claim(self) -> str: ...

    @property
    def source_urls(self) -> Sequence[str]: ...


def filter_new[FindingT: _Finding](
    findings: Sequence[FindingT],
    prior_urls: Set[str],
    prior_claims: Sequence[str],
) -> list[FindingT]:
    """The survivors of ``findings`` after both novelty mechanisms (TDD §5.4).

    An empty result **is** the Skipped signal (CONTEXT.md) — the caller does
    not compute that separately, it reads it off ``len(result) == 0``.

    Two independent mechanisms, either one enough to drop a finding:

    * **Source-URL overlap.** A finding whose **every** URL was already cited
      by a prior Brief of this Beat is not new — note *every*, not *any*: a
      finding citing one URL this Beat has used before **and** one it has
      not is still new, because the new URL is new evidence even if the
      argument sounds familiar. A finding with *no* URLs at all vacuously
      satisfies "every URL already cited" (there is nothing to point at
      that is not already covered), so it is treated as not new rather than
      let through on a technicality — consistent with D8's downstream rule
      that an uncited claim cannot become a published Brief regardless.
    * **Claim dedup.** ``finding.claim`` is compared against each of
      ``prior_claims`` with :func:`_restates_claim` — the ``restates_stem``
      technique from ``agents/flashcard.py`` (normalized *content-word*
      overlap, scored **asymmetrically over the reference side**, never a
      symmetric ``min``), re-implemented rather than imported because
      `domains/` may not import `agents/`, and pointed at a different pair of
      strings: a candidate claim and a prior Brief's claim (the reference,
      playing ``restates_stem``'s ``stem`` role), instead of a flashcard's
      front and its lesson's Quick-check stem. A claim restating **any**
      prior claim in new words is dropped.

    ``findings`` is **not** deduplicated against itself — two identical (or
    order-shuffled, or repeated) findings in the same call are each judged
    solely against ``prior_urls``/``prior_claims`` and therefore reach the
    same verdict independently, which is what makes the result invariant to
    the caller's ordering and to accidental duplicates in ``findings``
    (`tests/unit/test_novelty.py`). Deduping *within* one research run's
    findings against each other is a different concern this gate does not
    own.

    Order is preserved: survivors come back in ``findings``' own order, a
    stable filter rather than a re-sort, so a caller that already ordered
    findings by significance keeps that order into the Brief.
    """
    return [
        finding
        for finding in findings
        if not _urls_all_prior(finding.source_urls, prior_urls)
        and not _restates_any(finding.claim, prior_claims)
    ]


def _urls_all_prior(source_urls: Sequence[str], prior_urls: Set[str]) -> bool:
    """True iff every url in ``source_urls`` is already in ``prior_urls``.

    Vacuously true for an empty ``source_urls`` — see :func:`filter_new`'s
    docstring for why that is the intended reading, not an edge case slipping
    through.
    """
    return all(url in prior_urls for url in source_urls)


def _restates_any(claim: str, prior_claims: Sequence[str]) -> bool:
    """True iff ``claim`` restates **any** entry of ``prior_claims``."""
    return any(_restates_claim(claim, prior) for prior in prior_claims)


# --- the restates_stem technique, re-implemented for claim-vs-claim ------------
#
# ``agents/flashcard.py``'s ``restates_stem(front, stem)`` cannot be imported
# here (domains/ imports no application layer), so the *technique* — normalized
# content-word overlap, function words dropped — is restated for this module's
# own pair of strings, **including its asymmetric orientation**: the fraction
# is taken over the *reference* side's significant words, never the shorter of
# the two. ``restates_stem(front, stem)`` divides by ``len(stem_tokens)`` — the
# already-published Quick-check question is the reference, and ``front`` (the
# candidate) is only asked "how much of the reference do you cover". Here the
# analogous reference is the **prior** Brief's claim (already published,
# fixed) and the candidate is the new finding's ``claim`` — so the denominator
# is ``len(prior_tokens)``, never ``min(len(claim_tokens), len(prior_tokens))``.
# Taking the ``min`` is strictly more aggressive than either asymmetric form:
# it lets a candidate whose content words are a strict subset of a longer
# prior claim score a perfect 1.0 regardless of how much *more* the prior
# claim said — which drops genuinely new findings ("Ofcom fined Meta £1.5m"
# read against "Ofcom fined Meta £5m in an earlier ruling, one of several")
# and inverts the false-negative bias this whole class of check is required to
# have (`agents/flashcard.py`'s module docstring; TDD D9's "Skipped is
# expensive to manufacture" framing). The constants below intentionally mirror
# flashcard.py's calibration (same threshold, same minimum content-token
# floor, same small generic stopword list) as the starting point; they are a
# **separate** knob from flashcard.py's, free to retune independently once
# real Brief claims are on hand, exactly because they are not the same
# constant shared across an import. The *formula*, unlike the constants, is
# not a free choice — it must mirror ``restates_stem``'s orientation exactly,
# which is what this module now does.

# **Deliberate divergence from `agents/flashcard.py`'s tokenizer.** That
# module's ``[a-z0-9']+`` is fine for Quick-check stems, which rarely turn on
# a figure. Brief claims are frequently *about* figures — "fined €1.5m" vs
# "fined €5m" — and ``[a-z0-9']+`` splits a decimal or thousands-separated
# literal into fragments ("3.5%" -> {"3", "5"}, "€2.4bn" -> {"2", "4bn"}),
# discarding exactly the digits that distinguish one finding from another and
# making two *different* amounts look like shared content words. The pattern
# below keeps a leading numeric literal — with an optional single decimal/
# comma group and optional trailing letters/percent sign ("1.5", "2.4bn",
# "3.5%") — as one token, falling back to a plain run of letters otherwise.
# This is a one-way divergence in the false-negative (safe) direction: it can
# only make two numerically-different claims look *less* alike than the old
# tokenizer did, never more, so it cannot itself manufacture a false-positive
# drop.
_WORD_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)?[a-z]*%?|[a-z][a-z']*")

# Deliberately small and generic (English function words, not domain-specific
# jargon) — see agents/flashcard.py's `_STOPWORDS` for the false-positive this
# guards against (two different questions sharing only their grammatical
# scaffolding).
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "of", "in", "on", "at", "to", "for", "with", "by", "from", "as",
        "and", "or", "but", "if", "then", "than", "that", "this", "these",
        "those", "it", "its", "what", "which", "who", "whom", "whose",
        "when", "where", "why", "how", "does", "do", "did", "can", "could",
        "will", "would", "should", "shall", "may", "might", "must", "not",
        "no", "yes", "you", "your", "we", "our", "i", "my", "he", "she",
        "they", "their", "them", "his", "her",
    }
)  # fmt: skip

# Below this many content words, a claim carries too little signal for
# overlap to mean anything — see agents/flashcard.py's `_MIN_CONTENT_TOKENS`
# for the identical reasoning, pointed at claims instead of stems.
_MIN_CONTENT_TOKENS = 3

# The fraction of the *prior* claim's content words that must also appear in
# the candidate for the pair to count as restating each other — mirroring
# `restates_stem(front, stem)`'s `len(stem_tokens)` denominator exactly, with
# `prior` playing `stem`'s role (the fixed reference) and `claim` playing
# `front`'s (the candidate being checked against it). Asymmetric, deliberately
# — see the block comment above.
_RESTATEMENT_OVERLAP_THRESHOLD = 0.8


def _normalized_tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _content_tokens(text: str) -> set[str]:
    """Significant words only: normalized, then stopwords dropped."""
    return _normalized_tokens(text) - _STOPWORDS


def _restates_claim(claim: str, prior: str) -> bool:
    """True when ``claim`` restates ``prior`` in new words (the shared technique).

    Either side having fewer than :data:`_MIN_CONTENT_TOKENS` content words
    means there is not enough signal to call it a restatement, so the pair is
    never flagged — the same "too little signal" reasoning as
    ``agents/flashcard.py``'s ``restates_stem``.

    **Asymmetric, like ``restates_stem``, and for the same reason.** The
    fraction is taken over ``prior``'s content words (the reference, already
    published) — never the shorter of the two, and never a symmetric ``min``.
    A candidate whose words are a strict subset of a longer prior claim no
    longer scores a free 1.0 for that reason alone; it is judged only against
    how much of *that specific prior claim* it actually covers.
    """
    claim_tokens = _content_tokens(claim)
    prior_tokens = _content_tokens(prior)
    if (
        len(claim_tokens) < _MIN_CONTENT_TOKENS
        or len(prior_tokens) < _MIN_CONTENT_TOKENS
    ):
        return False
    overlap = len(claim_tokens & prior_tokens) / len(prior_tokens)
    return overlap >= _RESTATEMENT_OVERLAP_THRESHOLD
