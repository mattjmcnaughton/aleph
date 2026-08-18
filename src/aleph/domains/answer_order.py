"""Pure option ordering for a single-select MCQ: kill the model's position bias.

A generated **Quick check** (and its non-scoring sibling, a **Tutor check**)
arrives from the model as ``options`` plus a ``correct_index`` into them, and the
model chooses that index. Language models do not choose it uniformly: they park
the correct option in the same slot — in practice the second one, "B" as the card
renders it — lesson after lesson, and almost never in the last one. That is a
property of the generator, not of the material, and it is directly learnable: a
learner who notices can score a path without reading it, which makes every Quick
check worthless as a check on understanding.

Prompting the model to "vary the position" does not fix this — it is a
distributional bias, not an instruction it is disobeying. Re-ordering the options
*after* generation does, and completely: :func:`shuffle_options` permutes the
options and carries ``correct_index`` along with the option it addresses, so the
served position of the correct option is decided here rather than by the model.

**The independence rule, and why it is the whole trick.** The permutation is
derived from the caller's ``seed`` alone — never from ``correct_index``, never
from the option text. That independence is what makes the served position
uniform: whatever the model's own habit is (always index 1, or 1 nine times in
ten), applying a correct-index-blind permutation on top of it spreads the answer
evenly across the slots. A permutation that peeked at which option was correct
could reproduce the very bias it is here to remove, so it does not get to look.

**Deterministic, not random** (``select_daily_queue``'s discipline in
``scheduling.py``, for the same reasons): the order falls out of
``sha256(f"{seed}:{position}")`` rather than ``random``, so a given seed always
yields a given order. Callers seed with something stable, per-check, and
unrelated to the answer — a lesson id, a check's stem — which makes the shuffle
reproducible in tests and in an eval replay while still varying from one check to
the next. It is applied **once**, where the check is persisted or first
delivered, so the stored order is the order everything downstream agrees on:
grading, the tutor's view of the keyed answer, a Revision snapshot and its undo.

Stdlib only, plain data in and out — the ``domains/__init__.py`` contract
verbatim.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class OrderedOptions:
    """One MCQ's options in served order, with the correct one still addressed.

    ``correct_index`` indexes ``options`` — the pair is only ever meaningful
    together, which is why they are returned together rather than as a bare list
    the caller has to re-key by hand.
    """

    options: tuple[str, ...]
    correct_index: int


def shuffle_options(
    options: Sequence[str], correct_index: int, *, seed: str
) -> OrderedOptions:
    """Permute ``options`` by ``seed``, moving ``correct_index`` with its option.

    The returned options are exactly the input options, re-ordered — none added,
    dropped, or rewritten — and ``correct_index`` addresses the same option text
    it addressed on the way in. See the module docstring for why the permutation
    is derived from ``seed`` alone.

    An empty ``options`` is returned untouched: there is nothing to order, and
    the range check below would otherwise reject a caller who has no options to
    key in the first place. Any non-empty input must carry an in-range
    ``correct_index`` — every caller has already run the agent's own validator
    (``correct_index_in_range``), so an out-of-range index here is a bug in this
    app rather than a model output to tolerate, and it fails loudly instead of
    silently re-keying the check to the wrong answer.
    """
    if not options:
        return OrderedOptions(options=(), correct_index=correct_index)
    if not 0 <= correct_index < len(options):
        raise ValueError(
            f"correct_index {correct_index} does not address one of the "
            f"{len(options)} options"
        )

    # The original positions, ordered by the digest of the seed and the position
    # — the option *text* is deliberately not in the key, so the permutation
    # cannot correlate with which option happens to be the correct one.
    order = sorted(
        range(len(options)),
        key=lambda position: hashlib.sha256(f"{seed}:{position}".encode()).hexdigest(),
    )
    return OrderedOptions(
        options=tuple(options[position] for position in order),
        correct_index=order.index(correct_index),
    )
