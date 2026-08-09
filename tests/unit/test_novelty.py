"""Unit tests for the pure novelty gate (TDD D9/§5.4, §11's table).

Pure domain — no fakes, no I/O, no session, and no `Finding` import: the
gate is structurally typed (see `domains/novelty.py`'s module docstring), so
these tests exercise it against a small local stand-in with exactly the two
attributes the gate reads, `claim` and `source_urls` — the same shape
`agents/researcher.py`'s real `Finding` (AL-520, not yet built) will have.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aleph.domains.novelty import filter_new


@dataclass(frozen=True)
class _Finding:
    """A minimal stand-in for `agents/researcher.py`'s `Finding` (AL-520).

    Only `claim` and `source_urls` exist because `filter_new` reads only
    those two — matching the Protocol in `domains/novelty.py` structurally,
    with no import of the real (not-yet-built) type.
    """

    claim: str
    source_urls: tuple[str, ...] = field(default_factory=tuple)


# --- Source-URL overlap: every URL already cited -> dropped -------------------


def test_finding_with_every_url_already_cited_is_dropped() -> None:
    finding = _Finding(
        claim="The regulator opened a new consultation on emissions rules.",
        source_urls=("https://example.com/a", "https://example.com/b"),
    )
    prior_urls = {"https://example.com/a", "https://example.com/b"}

    survivors = filter_new([finding], prior_urls, prior_claims=[])

    assert survivors == []


def test_finding_with_no_urls_at_all_is_dropped() -> None:
    """Vacuously "every url already cited" — nothing to point at as new evidence."""
    finding = _Finding(claim="Something happened, unsourced.", source_urls=())

    survivors = filter_new([finding], prior_urls=set(), prior_claims=[])

    assert survivors == []


# --- Source-URL overlap: one new URL -> survives -------------------------------


def test_finding_with_one_new_url_survives_even_with_one_old_url() -> None:
    """ "Every", not "any": one new URL alongside a stale one is still new evidence."""
    finding = _Finding(
        claim="The regulator opened a new consultation on emissions rules.",
        source_urls=("https://example.com/a", "https://example.com/new"),
    )
    prior_urls = {"https://example.com/a"}

    survivors = filter_new([finding], prior_urls, prior_claims=[])

    assert survivors == [finding]


def test_finding_with_a_wholly_new_url_survives() -> None:
    finding = _Finding(
        claim="The regulator opened a new consultation on emissions rules.",
        source_urls=("https://example.com/new",),
    )

    survivors = filter_new([finding], prior_urls=set(), prior_claims=[])

    assert survivors == [finding]


# --- claim dedup: a restated prior claim -> dropped ----------------------------


def test_finding_restating_a_prior_claim_in_new_words_is_dropped() -> None:
    """Near-verbatim, one content word swapped — the shape restates_stem's own
    docstring says is the technique's real (honest, narrow) catch: a genuine
    paraphrase with several words changed slips past it, by design."""
    finding = _Finding(
        claim="The central bank increased interest rates by half a percentage point.",
        source_urls=("https://example.com/new",),  # a new URL alone does not save it
    )
    prior_claims = [
        "The central bank raised interest rates by half a percentage point."
    ]

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == []


def test_finding_with_an_unrelated_claim_survives_against_prior_claims() -> None:
    finding = _Finding(
        claim="A separate court ruling struck down the merger on antitrust grounds.",
        source_urls=("https://example.com/new",),
    )
    prior_claims = [
        "The central bank raised interest rates by half a percentage point."
    ]

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == [finding]


def test_short_claims_are_never_flagged_as_restated() -> None:
    """Too little content-word signal to mean anything (mirrors restates_stem)."""
    finding = _Finding(claim="It happened.", source_urls=("https://example.com/new",))
    prior_claims = ["It happened again today."]

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == [finding]


# --- empty input -> empty output: the Skipped signal ---------------------------


def test_empty_findings_is_empty_output() -> None:
    assert filter_new([], prior_urls=set(), prior_claims=[]) == []


def test_all_findings_dropped_is_the_skipped_signal() -> None:
    """No survivors at all, not just an empty findings list, reads as Skipped too."""
    findings = [
        _Finding(claim="Same old news restated.", source_urls=("https://a.example",)),
    ]
    prior_urls = {"https://a.example"}

    assert filter_new(findings, prior_urls, prior_claims=[]) == []


# --- ordering and duplicate findings cannot change the outcome ----------------


def test_ordering_does_not_change_which_findings_survive() -> None:
    new_finding = _Finding(
        claim="A fresh development nobody has reported.",
        source_urls=("https://example.com/new",),
    )
    stale_finding = _Finding(
        claim="Old news.", source_urls=("https://example.com/old",)
    )
    prior_urls = {"https://example.com/old"}

    forward = filter_new([new_finding, stale_finding], prior_urls, prior_claims=[])
    backward = filter_new([stale_finding, new_finding], prior_urls, prior_claims=[])

    assert forward == [new_finding]
    assert backward == [new_finding]


def test_duplicate_findings_all_survive_or_all_drop_together() -> None:
    """filter_new does not dedup findings against each other — only against
    prior history — so a duplicate's fate is decided independently and lands
    the same way every time, regardless of how many copies are present."""
    finding = _Finding(
        claim="A fresh development nobody has reported.",
        source_urls=("https://example.com/new",),
    )

    survivors = filter_new(
        [finding, finding, finding], prior_urls=set(), prior_claims=[]
    )

    assert survivors == [finding, finding, finding]


def test_duplicate_stale_findings_all_drop_together() -> None:
    finding = _Finding(claim="Old news.", source_urls=("https://example.com/old",))
    prior_urls = {"https://example.com/old"}

    survivors = filter_new([finding, finding], prior_urls, prior_claims=[])

    assert survivors == []


def test_survivor_order_matches_input_order_not_a_resort() -> None:
    first = _Finding(
        claim="First fresh development, quite distinct.",
        source_urls=("https://example.com/1",),
    )
    second = _Finding(
        claim="Second fresh development, entirely separate.",
        source_urls=("https://example.com/2",),
    )

    survivors = filter_new([second, first], prior_urls=set(), prior_claims=[])

    assert survivors == [second, first]
