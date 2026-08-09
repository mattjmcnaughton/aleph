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


# --- FIX 1 regression: subset-of-a-longer-prior-claim must SURVIVE -------------
#
# Before the fix, `_restates_claim` divided by `min(len(claim_tokens),
# len(prior_tokens))`, which is strictly more aggressive than either
# asymmetric form: any candidate whose content words are a subset of a
# longer prior claim scored a perfect 1.0 and was dropped, no matter how
# much *more* the prior claim said. These three pairs are the concrete
# failure cases named in the fix and are the most valuable tests in this
# file — each candidate reports a genuinely different fact from its prior
# and must not be gated away as "already reported".


def test_different_fine_amount_survives_despite_being_a_word_subset() -> None:
    """A later, different fine is not "the same finding, fewer words"."""
    finding = _Finding(
        claim="Ofcom fined Meta 1.5 million.",
        source_urls=("https://example.com/new-fine",),
    )
    prior_claims = ["Ofcom fined Meta 5 million in an earlier ruling, 1 of several."]

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == [finding]


def test_different_rate_cut_survives_despite_being_a_word_subset() -> None:
    finding = _Finding(
        claim="Rates cut to 3.5%.",
        source_urls=("https://example.com/new-cut",),
    )
    prior_claims = ["Rates were cut on 3 March by 5 basis points."]

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == [finding]


def test_short_claim_survives_against_a_longer_prior_containing_its_words() -> None:
    """The candidate's words are a strict subset of the prior's — and the
    prior is nonetheless not "about the same development" closely enough to
    count as a restatement once judged against its own (larger) word count.
    """
    finding = _Finding(
        claim="Interest rates rose sharply.",
        source_urls=("https://example.com/new-rates",),
    )
    prior_claims = [
        "The central bank said interest rates rose sharply in March "
        "after inflation data."
    ]

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == [finding]


# --- FIX 3: pinning the threshold, the stopword subtraction, the guard --------


def test_overlap_just_below_threshold_survives() -> None:
    """4 shared / 4 prior-content-words = 0.75, just under the 0.8 bar."""
    finding = _Finding(
        claim="Alpha beta gamma epsilon.",
        source_urls=("https://example.com/near-threshold-low",),
    )
    prior_claims = ["Alpha beta gamma delta."]

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == [finding]


def test_overlap_at_threshold_is_dropped() -> None:
    """4 shared / 5 prior-content-words = 0.80, exactly the (inclusive) bar."""
    finding = _Finding(
        claim="Alpha beta gamma delta zeta.",
        source_urls=("https://example.com/at-threshold",),
    )
    prior_claims = ["Alpha beta gamma delta epsilon."]

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == []


def test_claims_sharing_only_stopwords_are_not_restatements() -> None:
    """Grammatical scaffolding alone is not a restatement — both claims are
    entirely function words once normalized, so there is no real content to
    compare and the pair must never be flagged."""
    finding = _Finding(
        claim="This is that when.",
        source_urls=("https://example.com/stopwords-only",),
    )
    prior_claims = ["When is this that?"]

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == [finding]


def test_min_content_token_guard_applies_to_the_prior_side_too() -> None:
    """A prior claim below `_MIN_CONTENT_TOKENS` carries too little signal to
    gate on, however large the candidate is — the guard must not be narrowed
    to check only the candidate's side, or a tiny prior claim's small word
    count as the (new, asymmetric) denominator would inflate the ratio to a
    false 1.0 and wrongly drop a genuinely new finding."""
    finding = _Finding(
        claim="Rates rose sharply today across markets.",
        source_urls=("https://example.com/guard-prior-side",),
    )
    prior_claims = ["Rates rose."]  # only 2 content words, below the floor of 3

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == [finding]


def test_claim_that_fully_covers_a_shorter_prior_is_still_dropped() -> None:
    """A candidate carrying **every** one of a shorter prior claim's content
    words, plus extra elaboration, is a restatement with padding — not new
    evidence. Pins the denominator as ``len(prior_tokens)`` specifically:
    scoring over ``max(len(claim_tokens), len(prior_tokens))`` instead would
    let the extra elaboration dilute the ratio below the bar and wrongly let
    the claim through."""
    finding = _Finding(
        claim="Alpha beta gamma delta epsilon zeta.",
        source_urls=("https://example.com/full-coverage",),
    )
    prior_claims = ["Alpha beta gamma."]

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == []


def test_decimal_literal_is_not_confused_with_a_different_integer() -> None:
    """FIX 2 regression guard: the tokenizer must keep "3.5%" as one token,
    not split it into "3" and "5" — otherwise a claim about 3.5% and a prior
    claim about 5% would spuriously share the token "5"."""
    finding = _Finding(
        claim="Rates rose to 3.5%.",
        source_urls=("https://example.com/decimal-literal",),
    )
    prior_claims = ["Rates rose to 5%."]

    survivors = filter_new([finding], prior_urls=set(), prior_claims=prior_claims)

    assert survivors == [finding]


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
