"""Calibration: judge↔human agreement over ``evals/human_labels.yaml``.

PRD §9: *"The judge is only as good as its agreement with a human. Maintain a
small human-labeled set (≈ 30-50 generations the builder has marked pass/fail)
and measure judge↔human agreement; the judge is trusted as a gate only while
agreement stays high (target ≥ 90%). Re-check after any prompt change to the
judge."* TDD §11 names the file and the mode: ``evals/human_labels.yaml`` and
``just evals --agreement``.

**Why this exists at all.** The ≥ 90% seed-set gate (``generation.py``) is only
as meaningful as the judge behind it. Without a calibration figure, a judge that
passes everything and a judge that reads the rubric carefully produce the same
green run — and the first one is much more likely, because "pass" is the easy
answer. Agreement is the number that tells the two apart, and
:data:`AGREEMENT_TRUST_THRESHOLD` is the line below which the seed-set gate
should not be believed.

**Direction matters more than the rate.** Two disagreements at the same rate
mean opposite things:

- *judge lenient* (human said fail, judge said pass) — the judge would have
  shipped something a human rejected. This is the failure mode that makes a
  green gate worthless, and it is the one to read first.
- *judge strict* (human said pass, judge said fail) — the judge blocks good
  generations. Annoying and expensive, but it fails safe.

So every comparison records its direction and the rendered report totals them
separately rather than reporting one undifferentiated percentage.

**Samples do not vote.** A label marked ``sample: true`` is illustrative — it
shows the schema and exercises the machinery, but its "human" verdict was
written by whoever wrote the file. Such labels are judged and printed like any
other and are then excluded from the *gated* figure
(:attr:`AgreementSummary.real`). This matters most while the file is **mixed**,
which is its expected state for most of its life: real labels land one at a
time, and a rate averaged over both would let agreeable samples dilute a judge
that disagrees with every label a human actually recorded.

**Self-contained by design.** Each label carries the artifact *inline* rather
than pointing at a report file. A calibration set is only useful if it can be
re-run months later against a new judge prompt or a new judge model, so it must
not depend on an artifact store, a CI artifact retention window, or a
regeneration that would produce different text. ``source`` records where the
generation came from; the content is the record.

``yaml`` is imported directly: pydantic-evals requires ``pyyaml>=6.0.2`` and
imports it at module scope for its own dataset loading, so the evals dependency
group already guarantees it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

import yaml
from pydantic import BaseModel, model_validator

from aleph.agents.lesson import LessonContent, PriorPassage

# Runtime imports, not typing-only: they annotate *pydantic model fields* below,
# and pydantic resolves those annotations from the module namespace when it
# builds the model. Under ``from __future__ import annotations`` a TYPE_CHECKING
# import would leave the name unresolvable and fail at import time.
from aleph.agents.outline import Level, PathOutline  # noqa: TC001
from evals.rubric import APPLICABLE_ITEMS, ArtifactKind, RubricItem

if TYPE_CHECKING:
    from collections.abc import Sequence

    from evals.judge import Judge
    from evals.rubric import JudgeVerdict

HUMAN_LABELS_PATH = Path(__file__).resolve().parent / "human_labels.yaml"

#: PRD §9 / TDD §11: the judge is a trusted gate only while judge↔human
#: agreement is at or above this. Below it, a green seed-set run means the judge
#: agreed with itself, nothing more — fix the judge prompt (or the model) and
#: re-measure before believing another gate result.
AGREEMENT_TRUST_THRESHOLD = 0.90

#: A human's verdict, in the human's vocabulary.
Verdict = Literal["pass", "fail"]

#: How a judge verdict relates to the human's. See the module docstring.
Direction = Literal["agree", "judge_lenient", "judge_strict"]


def _verdict(passed: bool) -> Verdict:
    return "pass" if passed else "fail"


# --- the label file ------------------------------------------------------------


class PriorPassageLabel(BaseModel):
    """An earlier lesson's Read passage, as recorded in a lesson label.

    The continuity item is only judgeable with the prior lessons in context, so
    a lesson label that omits them would be scored against a rubric the judge
    cannot apply — and would silently drift towards "continuity always passes".
    """

    unit_title: str
    lesson_title: str
    read_passage: str

    def as_prior(self) -> PriorPassage:
        return PriorPassage(
            unit_title=self.unit_title,
            lesson_title=self.lesson_title,
            read_passage=self.read_passage,
        )


class LessonArtifact(BaseModel):
    """The lesson under review in a ``artifact: lesson`` label, plus its slot."""

    position_in_path: int
    unit_title: str
    lesson_title: str
    content: LessonContent
    prior_passages: list[PriorPassageLabel] = []


class SmokeScript(BaseModel):
    """What the **offline stub judge** should report for this label.

    The exact counterpart of the seed set's ``force_expected_branch`` switch: a
    deterministic ``FunctionModel`` cannot form an opinion about a Read passage,
    so ``--smoke --agreement`` tells it which items to fail. That makes the whole
    agreement path — judging, comparison, direction classification, rendering,
    thresholding — runnable with no key and no network, including cases that
    *disagree* in both directions, which a blanket-pass stub could never produce.

    It is deliberately independent of the human label (scripting the stub to
    reproduce the human would make offline agreement 100% by construction and
    exercise nothing), and it is **never** applied to a live run — where what
    the judge says is the entire measurement.
    """

    judge_fails: list[RubricItem] = []


class HumanLabel(BaseModel):
    """One builder-labeled generation: the artifact, its context, the verdict.

    ``overall`` is the required figure — the pass/fail a human actually recorded
    — and the one the headline agreement rate is computed on. ``items`` is
    optional per-rubric-item detail; supplying it turns a bare disagreement into
    a diagnosis ("we disagree, and it is the continuity item"), which is what
    makes a judge-prompt fix targeted instead of a rewrite.
    """

    id: str
    #: True while this is illustrative rather than a real builder label. Sample
    #: labels are judged and reported like any other, but they are excluded from
    #: the gated agreement figure — a fabricated verdict must never move the
    #: number that decides whether the judge is trusted.
    #:
    #: **Required, with no default**: defaulting it either way is a silent
    #: mistake in one direction or the other (a forgotten `sample: true` would
    #: gate on a fabrication; a forgotten `sample: false` would drop a real
    #: label out of the measurement). Say which one it is.
    sample: bool
    #: Where the generation came from (the "artifact ref"): a seed-set case
    #: name, a report artifact, a hand-written probe.
    source: str
    note: str = ""

    artifact: ArtifactKind
    topic: str
    level: Level
    outline: PathOutline
    lesson: LessonArtifact | None = None

    overall: Verdict
    items: dict[RubricItem, Verdict] = {}
    smoke: SmokeScript = SmokeScript()

    @model_validator(mode="after")
    def _check_coherent(self) -> Self:
        """Reject a label that cannot be judged, or that contradicts itself."""
        if self.artifact == "lesson" and self.lesson is None:
            raise ValueError(f"{self.id}: a lesson label must carry a `lesson` block.")
        if self.artifact == "outline" and self.lesson is not None:
            raise ValueError(
                f"{self.id}: an outline label must not carry a `lesson` block."
            )

        applicable = set(APPLICABLE_ITEMS[self.artifact])
        extra = sorted(set(self.items) - applicable)
        if extra:
            raise ValueError(
                f"{self.id}: items {extra} are not scored for a {self.artifact} "
                f"(applicable: {sorted(applicable)})."
            )
        # A per-item breakdown that disagrees with the headline verdict would
        # make the same label produce two different agreement figures depending
        # on which field was read.
        if self.items:
            implied = _verdict(all(value == "pass" for value in self.items.values()))
            if implied != self.overall:
                raise ValueError(
                    f"{self.id}: per-item labels imply overall={implied!r} but "
                    f"overall is {self.overall!r} (an artifact passes only when "
                    "every item passes)."
                )

        unscorable = sorted(set(self.smoke.judge_fails) - applicable)
        if unscorable:
            raise ValueError(
                f"{self.id}: smoke.judge_fails names {unscorable}, which a "
                f"{self.artifact} is not judged on."
            )
        return self


class HumanLabelSet(BaseModel):
    """The parsed ``human_labels.yaml``."""

    version: int
    labels: list[HumanLabel]

    @model_validator(mode="after")
    def _check_unique_ids(self) -> Self:
        if not self.labels:
            raise ValueError("human_labels.yaml has no labels.")
        seen = [label.id for label in self.labels]
        duplicates = sorted({item for item in seen if seen.count(item) > 1})
        if duplicates:
            raise ValueError(f"duplicate label ids: {duplicates}")
        return self

    @property
    def all_samples(self) -> bool:
        """True while every label is illustrative rather than builder-recorded."""
        return all(label.sample for label in self.labels)


def load_human_labels(path: Path | None = None) -> HumanLabelSet:
    """Load and validate the checked-in human-label set."""
    source = path or HUMAN_LABELS_PATH
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    return HumanLabelSet.model_validate(raw)


# --- comparing one label to one judge verdict ----------------------------------


@dataclass(frozen=True)
class Comparison:
    """One label judged: what the human said, what the judge said, and how far apart."""

    label_id: str
    artifact: ArtifactKind
    human: Verdict
    judge: Verdict
    direction: Direction
    judge_failed_items: tuple[RubricItem, ...]
    #: ``(item, human verdict, judge verdict)`` for every item the human scored
    #: and the judge disagreed on. Empty when the human gave no per-item labels.
    item_disagreements: tuple[tuple[RubricItem, Verdict, Verdict], ...]
    #: How many items were compared item-by-item (0 when none were labeled).
    items_compared: int
    #: Whether the label behind this comparison is illustrative
    #: (:attr:`HumanLabel.sample`). Defaults to False — "a comparison counts
    #: towards the measurement unless it says otherwise" is the safe direction,
    #: since the alternative silently shrinks the gated figure.
    sample: bool = False

    @property
    def agreed(self) -> bool:
        return self.direction == "agree"


def compare(label: HumanLabel, verdict: JudgeVerdict) -> Comparison:
    """Classify one judge verdict against one human label.

    Pure: no model, no I/O. The whole point of splitting it out is that the
    arithmetic below — which decides whether the judge may be trusted as a gate
    — is testable without a provider.
    """
    judged = _verdict(verdict.overall)
    if judged == label.overall:
        direction: Direction = "agree"
    elif label.overall == "fail":
        # The human rejected it and the judge would have let it through.
        direction = "judge_lenient"
    else:
        direction = "judge_strict"

    disagreements: list[tuple[RubricItem, Verdict, Verdict]] = []
    for item, human_item in label.items.items():
        entry = verdict.verdict_for(item)
        if entry is None:
            continue
        judge_item = _verdict(entry.passed)
        if judge_item != human_item:
            disagreements.append((item, human_item, judge_item))

    return Comparison(
        label_id=label.id,
        artifact=label.artifact,
        human=label.overall,
        judge=judged,
        direction=direction,
        judge_failed_items=verdict.failed_items,
        item_disagreements=tuple(disagreements),
        items_compared=sum(
            1 for item in label.items if verdict.verdict_for(item) is not None
        ),
        sample=label.sample,
    )


@dataclass(frozen=True)
class AgreementSummary:
    """The calibration figure and everything needed to act on it.

    Every figure on this class is computed over :attr:`comparisons`, whatever
    those are. The *gated* figure — the one that decides whether the judge may be
    trusted — is :attr:`real`, a summary of the same shape restricted to
    builder-recorded labels; see that property for why the distinction cannot be
    a footnote.
    """

    comparisons: tuple[Comparison, ...]

    @property
    def real(self) -> AgreementSummary:
        """The same figures over builder-recorded labels only — the gated set.

        A calibration file is expected to be *mixed* for most of its life: the
        adoption path in ``human_labels.yaml`` is "clear the ``sample`` flag as
        real labels land", so the file passes through every ratio of fabricated
        to real on its way. Averaging the two together would gate on a number the
        samples dilute — with enough agreeable samples, a judge that disagrees
        with every real label still clears 90% — so the threshold is applied
        here, to the labels a human actually recorded, and never to the mixture.
        """
        return AgreementSummary(
            comparisons=tuple(
                comparison for comparison in self.comparisons if not comparison.sample
            )
        )

    @property
    def samples(self) -> AgreementSummary:
        """The illustrative labels, reported separately and never gated on."""
        return AgreementSummary(
            comparisons=tuple(
                comparison for comparison in self.comparisons if comparison.sample
            )
        )

    @property
    def total(self) -> int:
        return len(self.comparisons)

    @property
    def agreed(self) -> int:
        return sum(1 for comparison in self.comparisons if comparison.agreed)

    @property
    def rate(self) -> float:
        """Headline judge↔human agreement on the overall pass/fail verdict."""
        return self.agreed / self.total if self.total else 0.0

    @property
    def lenient(self) -> tuple[Comparison, ...]:
        """Human said fail, judge said pass — the dangerous direction."""
        return tuple(
            comparison
            for comparison in self.comparisons
            if comparison.direction == "judge_lenient"
        )

    @property
    def strict(self) -> tuple[Comparison, ...]:
        """Human said pass, judge said fail — fails safe, still worth fixing."""
        return tuple(
            comparison
            for comparison in self.comparisons
            if comparison.direction == "judge_strict"
        )

    @property
    def items_compared(self) -> int:
        return sum(comparison.items_compared for comparison in self.comparisons)

    @property
    def items_agreed(self) -> int:
        return self.items_compared - sum(
            len(comparison.item_disagreements) for comparison in self.comparisons
        )

    @property
    def item_rate(self) -> float | None:
        """Per-item agreement, or ``None`` when no label carried item detail."""
        if not self.items_compared:
            return None
        return self.items_agreed / self.items_compared

    @property
    def meets_threshold(self) -> bool:
        """Whether the judge may be trusted as a gate (PRD §9's ≥ 90%).

        Computed over this summary's own comparisons, so the CLI asks it of
        :attr:`real` — the builder-recorded labels — rather than of a mixture
        that samples can dilute.
        """
        return self.total > 0 and self.rate >= AGREEMENT_TRUST_THRESHOLD


def summarize(comparisons: Sequence[Comparison]) -> AgreementSummary:
    """Aggregate per-label comparisons into the calibration summary."""
    return AgreementSummary(comparisons=tuple(comparisons))


# --- running the judge over the label set --------------------------------------


async def judge_label(
    judge: Judge, label: HumanLabel, *, apply_smoke_script: bool = False
) -> JudgeVerdict:
    """Score one labeled artifact with ``judge``, without seeing its label.

    The judge is given the artifact and its context only — never ``overall``,
    ``items``, ``note`` or the label id — because a calibration measurement in
    which the judge can read the answer measures nothing.

    ``apply_smoke_script`` is the ``--smoke`` switch: it appends the offline stub
    judge's ``[judge-fail:<item>]`` sentinels to the topic (the same place the
    seed set appends ``[force-refusal]``), so the deterministic judge produces
    the scripted verdict. Never set for a live run.
    """
    topic = label.topic
    if apply_smoke_script and label.smoke.judge_fails:
        from evals.judge import judge_fail_sentinel

        sentinels = " ".join(
            judge_fail_sentinel(item) for item in label.smoke.judge_fails
        )
        topic = f"{topic} {sentinels}"

    if label.artifact == "outline":
        return await judge.judge_outline(
            topic=topic, level=label.level, outline=label.outline
        )

    # Guaranteed non-None by HumanLabel's validator; asserted for the type
    # checker rather than left to an attribute error at run time.
    lesson = label.lesson
    if lesson is None:  # pragma: no cover - validator prevents this
        raise ValueError(f"{label.id}: lesson label without a lesson block")
    return await judge.judge_lesson(
        topic=topic,
        level=label.level,
        outline=label.outline,
        position_in_path=lesson.position_in_path,
        unit_title=lesson.unit_title,
        lesson_title=lesson.lesson_title,
        lesson=lesson.content,
        prior_passages=[prior.as_prior() for prior in lesson.prior_passages],
    )


async def run_agreement(
    judge: Judge,
    labels: Sequence[HumanLabel],
    *,
    apply_smoke_script: bool = False,
) -> AgreementSummary:
    """Judge every label and summarise judge↔human agreement.

    Sequential rather than concurrent: a calibration set is tens of artifacts,
    the run is opt-in, and a deterministic ordering makes the printed table
    diffable between runs — which is exactly what you want when the question is
    "did my judge-prompt edit move the number, and where".
    """
    comparisons = [
        compare(
            label,
            await judge_label(judge, label, apply_smoke_script=apply_smoke_script),
        )
        for label in labels
    ]
    return summarize(comparisons)


# --- rendering -----------------------------------------------------------------


def render_agreement(summary: AgreementSummary, *, judge_label_text: str) -> str:
    """The printed calibration report: per-label table, rate, and directions.

    The ``kind`` column and the two totals lines exist for the same reason: a
    mixed file's headline rate is *not* its calibration figure, and a reader who
    cannot see which rows are fabricated cannot tell the two apart.
    """
    lines = [
        f"Judge↔human agreement — judge: {judge_label_text}",
        "",
        f"{'label':<40} {'kind':<6} {'artifact':<8} {'human':<6} {'judge':<6} verdict",
        "-" * 96,
    ]
    for comparison in summary.comparisons:
        marker = {
            "agree": "agree",
            "judge_lenient": "DISAGREE (judge lenient)",
            "judge_strict": "DISAGREE (judge strict)",
        }[comparison.direction]
        detail = ""
        if comparison.judge_failed_items:
            detail = f"  judge failed: {', '.join(comparison.judge_failed_items)}"
        kind = "sample" if comparison.sample else "real"
        lines.append(
            f"{comparison.label_id:<40} {kind:<6} {comparison.artifact:<8} "
            f"{comparison.human:<6} {comparison.judge:<6} {marker}{detail}"
        )
        for item, human_item, judge_item in comparison.item_disagreements:
            lines.append(
                f"{'':<40} └─ item {item}: human={human_item} judge={judge_item}"
            )

    real, samples = summary.real, summary.samples
    lines.extend(
        [
            "-" * 96,
            f"agreement (all labels): {summary.agreed}/{summary.total} "
            f"({summary.rate:.1%})",
        ]
    )
    if samples.total:
        # Spelled out even when there are no real labels at all, because "the
        # gated figure is empty" is exactly what the reader needs to know — and
        # a bare 0/0 rendered as "0.0%" would read as total disagreement.
        gated_figure = (
            f"{real.agreed}/{real.total} ({real.rate:.1%}); "
            f"threshold {AGREEMENT_TRUST_THRESHOLD:.0%}"
            if real.total
            else (
                "none recorded yet — nothing is gated (the "
                f"{AGREEMENT_TRUST_THRESHOLD:.0%} threshold applies from the "
                "first one)"
            )
        )
        lines.append(f"  gated — builder-recorded labels only: {gated_figure}")
        lines.append(
            f"  not gated — illustrative samples: {samples.agreed}/{samples.total} "
            f"({samples.rate:.1%})"
        )
    else:
        lines.append(f"  threshold {AGREEMENT_TRUST_THRESHOLD:.0%} (every label gated)")
    lines.append(
        f"disagreements: {len(summary.lenient)} judge-lenient "
        f"(judge would ship what a human rejected), "
        f"{len(summary.strict)} judge-strict"
    )
    item_rate = summary.item_rate
    if item_rate is not None:
        lines.append(
            f"per-item agreement: {summary.items_agreed}/{summary.items_compared} "
            f"({item_rate:.1%})"
        )
    return "\n".join(lines)
