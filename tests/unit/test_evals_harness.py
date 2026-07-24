"""Unit tests for the agent eval harness (``evals/``, TDD §11, docs/evals.md).

Keyless and offline. Three things are under test:

1. **The seed set** — that ``evals/seed_set.yaml`` loads, that every case is
   complete and well-typed, and that it still carries the PRD §9 spread (~20
   topic × level cases across ordinary breadth, sensitive-but-legitimate topics
   that must generate, and over-the-boundary topics that must refuse).
2. **The Layer 1 pre-filter wiring** — that each pre-filter actually rejects the
   thing it claims to (a cap-violating outline, a band-violating lesson, and a
   wrong-branch result in *both* directions), and that a full smoke run over the
   real agents driven by the deterministic stub model is green end to end.
3. **The CLI's exit-code classification** (``evals/__main__.py``) — that a case
   whose hard-floor check never produced a verdict (the evaluator raised, or was
   never registered) exits 1 rather than passing by omission.

The pre-filters are exercised through the public pydantic-evals surface (an
inline ``Dataset`` plus a task returning a hand-built sample) rather than by
constructing an ``EvaluatorContext`` by hand: the fake is small, and the test
then proves the same path a real run takes.

The stub model performs no network I/O, so nothing here can reach a provider —
``just evals --smoke`` runs this same plumbing from the CLI.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, get_args

import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator

from aleph.agents.lesson import LessonCaps, LessonContent, QuickCheck
from aleph.agents.outline import (
    LessonOutline,
    Level,
    OutlineCaps,
    PathOutline,
    Refusal,
    UnitOutline,
)
from aleph.config import Settings
from aleph.services.generation import _lesson_caps_from, _outline_caps_from
from evals.__main__ import _case_payload, _hard_floor_failures, main
from evals.generation import (
    FULL_PATH_LESSONS,
    HARD_FLOOR_EVALUATORS,
    LESSON_CAPS,
    OUTLINE_CAPS,
    PREFILTERS,
    GeneratedLesson,
    GenerationSample,
    RefusalBranch,
    SeedInputs,
    SeedMeta,
    build_generation_task,
    load_seed_set,
    smoke_model,
)

if TYPE_CHECKING:
    from pydantic_evals.evaluators import EvaluatorContext
    from pydantic_evals.reporting import EvaluationReport


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- helpers -------------------------------------------------------------------


def _valid_outline(units: int = 2, lessons_per_unit: int = 3) -> PathOutline:
    """A cap-respecting outline with globally unique lesson titles."""
    number = 0
    built: list[UnitOutline] = []
    for unit in range(units):
        lessons: list[LessonOutline] = []
        for _ in range(lessons_per_unit):
            number += 1
            lessons.append(LessonOutline(title=f"Lesson {number}"))
        built.append(
            UnitOutline(
                title=f"Unit {unit + 1}", summary="A unit summary.", lessons=lessons
            )
        )
    return PathOutline(units=built)


def _valid_lesson() -> LessonContent:
    """A lesson inside the §14 word band with a well-formed Quick check."""
    return LessonContent(
        read_passage=" ".join(["word"] * 250),
        quick_check=QuickCheck(
            stem="Which statement is correct?",
            options=["First", "Second", "Third"],
            correct_index=1,
            explanation="Because the passage says so.",
        ),
    )


def _sample(
    outline: PathOutline | Refusal,
    lesson: LessonContent | None = None,
    *,
    unit_title: str = "Unit 1",
    lesson_title: str = "Lesson 1",
) -> GenerationSample:
    """A sample carrying zero or one generated lesson.

    ``GenerationSample`` holds a *list* of lessons (a full-path case generates
    several), so the single-lesson shape most of these probes want is built
    here rather than repeated at every call site.
    """
    lessons = (
        []
        if lesson is None
        else [
            GeneratedLesson(
                position_in_path=1,
                unit_title=unit_title,
                lesson_title=lesson_title,
                content=lesson,
            )
        ]
    )
    return GenerationSample(outline=outline, lessons=lessons)


async def _probe_report(
    inputs: SeedInputs,
    sample: GenerationSample,
    evaluators: list[Evaluator[SeedInputs, GenerationSample, SeedMeta]] | None = None,
) -> EvaluationReport[SeedInputs, GenerationSample, SeedMeta]:
    """Score one hand-built sample through the real pydantic-evals machinery.

    An inline dataset plus a task that ignores its inputs and returns ``sample``
    — so results come back the same way a real run's do, including how the
    library represents an evaluator that raised.
    """

    async def task(_inputs: SeedInputs) -> GenerationSample:
        return sample

    dataset = Dataset[SeedInputs, GenerationSample, SeedMeta](
        name="prefilter-probe",
        cases=[
            Case(
                name="probe",
                inputs=inputs,
                metadata=SeedMeta(category="technical", note="unit-test probe"),
            )
        ],
        evaluators=(
            evaluators
            if evaluators is not None
            else [prefilter() for prefilter in PREFILTERS]
        ),
    )
    return await dataset.evaluate(task, progress=False)


async def _assess(
    inputs: SeedInputs, sample: GenerationSample
) -> dict[str, tuple[bool, str | None]]:
    """Run every Layer 1 pre-filter over one sample: ``{name: (passed, reason)}``."""
    report = await _probe_report(inputs, sample)
    assert not report.failures, "the probe task itself errored"
    return {
        name: (result.value, result.reason)
        for name, result in report.cases[0].assertions.items()
    }


# --- the seed set --------------------------------------------------------------


def test_seed_set_loads_and_every_case_is_complete() -> None:
    dataset = load_seed_set()
    # PRD §9 gates on "a seed set of ~20 representative topic × level pairs".
    assert 18 <= len(dataset.cases) <= 24, f"{len(dataset.cases)} cases"

    names = [case.name for case in dataset.cases]
    assert len(names) == len(set(names)), "case names must be unique"

    valid_levels = set(get_args(Level))
    for case in dataset.cases:
        assert case.name, "every case needs a name (it is the report's row id)"
        assert case.inputs.topic.strip(), f"{case.name}: empty topic"
        assert case.inputs.level in valid_levels, f"{case.name}: bad level"
        assert case.inputs.expected_branch in {"generate", "refuse"}
        # Metadata is the curation record; an unexplained case is unmaintainable.
        assert case.metadata is not None, f"{case.name}: missing metadata"
        assert case.metadata.note.strip(), f"{case.name}: empty curation note"


def test_seed_set_covers_the_prd_spread() -> None:
    dataset = load_seed_set()
    branches = {case.inputs.expected_branch for case in dataset.cases}
    assert branches == {"generate", "refuse"}, "both branches must be represented"

    categories = {case.metadata.category for case in dataset.cases if case.metadata}
    # PRD §9: technical, non-technical, sensitive-but-legitimate, plus the
    # over-the-boundary cases §10 says must be refused.
    assert categories == {"technical", "non-technical", "sensitive", "boundary"}

    levels = {case.inputs.level for case in dataset.cases}
    assert levels == set(get_args(Level)), "every level must appear"

    # The exact spread docs/evals.md publishes (its category table and its
    # "7 beginner / 8 intermediate / 5 advanced" line). Pinned so trimming the
    # sensitive or boundary buckets — the halves that carry the safety signal —
    # cannot happen quietly while the doc keeps claiming the old shape.
    category_counts = Counter(
        case.metadata.category for case in dataset.cases if case.metadata
    )
    assert category_counts == Counter(
        {"technical": 5, "non-technical": 5, "sensitive": 6, "boundary": 4}
    )
    assert Counter(case.inputs.level for case in dataset.cases) == Counter(
        {"beginner": 7, "intermediate": 8, "advanced": 5}
    )

    by_category: dict[str, set[str]] = {}
    for case in dataset.cases:
        assert case.metadata is not None
        by_category.setdefault(case.metadata.category, set()).add(
            case.inputs.expected_branch
        )
    # The two safety buckets are only meaningful as a pair: sensitive topics
    # must generate, boundary topics must refuse. A `sensitive` case marked
    # `refuse` would quietly turn an over-refusal into an expected result.
    assert by_category["sensitive"] == {"generate"}
    assert by_category["boundary"] == {"refuse"}


def test_harness_caps_match_the_ones_the_service_builds_from_settings() -> None:
    """The harness's caps are the production caps, asserted rather than commented.

    ``evals/generation.py`` constructs ``OutlineCaps()`` / ``LessonCaps()``
    directly so a run is reproducible from the repo alone — but that is only
    honest while the dataclass defaults still equal what
    ``services/generation.py`` builds from ``Settings``. If a §14 default moves
    in config only, the harness would silently evaluate against the old numbers.
    ``_env_file=None`` keeps an untracked ``.env`` or ambient CI env out of it.
    """
    config = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert _outline_caps_from(config) == OUTLINE_CAPS
    assert _lesson_caps_from(config) == LESSON_CAPS
    # And the harness really is on the plain defaults, not a bespoke set.
    assert OutlineCaps() == OUTLINE_CAPS
    assert LessonCaps() == LESSON_CAPS


def test_seed_set_registers_every_hard_floor_prefilter() -> None:
    dataset = load_seed_set()
    registered = {type(evaluator).__name__ for evaluator in dataset.evaluators}
    assert registered >= {cls.__name__ for cls in PREFILTERS}
    # The CLI's exit-code logic keys on these names being present in the report.
    assert registered >= HARD_FLOOR_EVALUATORS
    # The soft response-time budget (pydantic-evals built-in) rides along.
    assert "MaxDuration" in registered


# --- Layer 1 pre-filters -------------------------------------------------------


@pytest.mark.anyio
async def test_outline_prefilter_rejects_a_cap_violation() -> None:
    over_cap = _valid_outline(units=OUTLINE_CAPS.max_units + 1, lessons_per_unit=1)
    assertions = await _assess(
        SeedInputs(topic="anything", level="beginner", expected_branch="generate"),
        _sample(over_cap, _valid_lesson()),
    )
    passed, reason = assertions["OutlineInvariants"]
    assert not passed
    assert reason is not None and str(OUTLINE_CAPS.max_units) in reason
    # The shared validator is the only source of the rule: a wrong-shaped
    # outline must not also trip the branch or lesson pre-filters.
    assert assertions["RefusalBranch"][0]
    assert assertions["LessonInvariants"][0]


@pytest.mark.anyio
async def test_outline_prefilter_accepts_an_outline_within_caps() -> None:
    assertions = await _assess(
        SeedInputs(topic="anything", level="beginner", expected_branch="generate"),
        _sample(_valid_outline(), _valid_lesson()),
    )
    assert all(passed for passed, _ in assertions.values()), assertions


@pytest.mark.anyio
async def test_lesson_prefilter_rejects_a_band_violation() -> None:
    too_few_options = LessonContent(
        read_passage=" ".join(["word"] * 250),
        quick_check=QuickCheck(
            stem="Which statement is correct?",
            options=["Only one"],
            correct_index=0,
            explanation="Because.",
        ),
    )
    assertions = await _assess(
        SeedInputs(topic="anything", level="beginner", expected_branch="generate"),
        _sample(_valid_outline(), too_few_options),
    )
    passed, reason = assertions["LessonInvariants"]
    assert not passed
    assert reason is not None and "options" in reason


@pytest.mark.anyio
async def test_lesson_prefilter_rejects_a_short_read_passage() -> None:
    short = LessonContent(
        read_passage="Too short to teach anything.",
        quick_check=_valid_lesson().quick_check,
    )
    assertions = await _assess(
        SeedInputs(topic="anything", level="beginner", expected_branch="generate"),
        _sample(_valid_outline(), short),
    )
    passed, reason = assertions["LessonInvariants"]
    assert not passed
    assert reason is not None and "words" in reason


@pytest.mark.anyio
async def test_refusal_branch_prefilter_catches_under_refusal() -> None:
    """A boundary case that outlined instead of refusing: a safety failure."""
    assertions = await _assess(
        SeedInputs(topic="anything", level="beginner", expected_branch="refuse"),
        _sample(_valid_outline(), _valid_lesson()),
    )
    passed, reason = assertions["RefusalBranch"]
    assert not passed
    assert reason is not None and "UNDER-REFUSAL" in reason


@pytest.mark.anyio
async def test_refusal_branch_prefilter_catches_over_refusal() -> None:
    """A legitimate case the agent declined: the other direction, also a block."""
    assertions = await _assess(
        SeedInputs(topic="anything", level="beginner", expected_branch="generate"),
        _sample(Refusal(message="Sorry, I can't help with that.")),
    )
    passed, reason = assertions["RefusalBranch"]
    assert not passed
    assert reason is not None and "OVER-REFUSAL" in reason
    # A refusal carries no lesson, and that is the correct outcome, not a gap.
    assert assertions["LessonInvariants"][0]


@pytest.mark.anyio
async def test_refusal_branch_prefilter_accepts_an_expected_refusal() -> None:
    assertions = await _assess(
        SeedInputs(topic="anything", level="beginner", expected_branch="refuse"),
        _sample(Refusal(message="Sorry, I can't help with that.")),
    )
    assert all(passed for passed, _ in assertions.values()), assertions


@pytest.mark.anyio
async def test_outline_prefilter_rejects_an_empty_refusal_message() -> None:
    """The refusal branch is validated too — a bare refusal is not graceful (W7)."""
    assertions = await _assess(
        SeedInputs(topic="anything", level="beginner", expected_branch="refuse"),
        _sample(Refusal(message="   ")),
    )
    assert not assertions["OutlineInvariants"][0]


# --- the smoke run ------------------------------------------------------------


@pytest.mark.anyio
async def test_smoke_run_passes_every_assertion() -> None:
    """The same path ``just evals --smoke`` takes, in the gate.

    Real agents, real prompts, real output validators, real seed set — only the
    model is the deterministic stub. Harness breakage is therefore caught by
    ``just gate`` while real eval runs stay opt-in and offline-free.
    """
    dataset = load_seed_set()
    stub = smoke_model()
    report = await dataset.evaluate(
        build_generation_task(stub, stub, force_expected_branch=True),
        name="smoke-unit",
        progress=False,
    )
    assert not report.failures
    assert _hard_floor_failures(report) == [], "a clean run must exit 0"
    assert len(report.cases) == len(dataset.cases)
    for case in report.cases:
        for name, result in case.assertions.items():
            assert result.value, f"{case.name} / {name}: {result.reason}"
        # One outline request, plus one lesson request for a generated path.
        assert case.metrics["model_requests"] >= 1


@pytest.mark.anyio
async def test_smoke_run_generates_a_probe_lesson_for_generate_cases() -> None:
    """The probe lesson is real, not skipped — both agents run per generate case."""
    dataset = load_seed_set()
    stub = smoke_model()
    task = build_generation_task(stub, stub, force_expected_branch=True)

    generated = next(
        case for case in dataset.cases if case.inputs.expected_branch == "generate"
    )
    sample = await task(generated.inputs)
    assert isinstance(sample.outline, PathOutline)
    assert sample.lessons, "a generate case must produce at least the probe lesson"
    assert sample.lessons[0].position_in_path == 1
    assert sample.lesson_slot == sample.lessons[0].slot

    refused = next(
        case for case in dataset.cases if case.inputs.expected_branch == "refuse"
    )
    refusal = await task(refused.inputs)
    assert isinstance(refusal.outline, Refusal)
    assert refusal.lesson_slot is None
    assert refusal.lessons == []


# --- full-path (sequential) cases ----------------------------------------------


def test_seed_set_marks_exactly_the_documented_full_path_cases() -> None:
    """docs/evals.md and the seed set's own header name three; pin them.

    Full-path cases are the expensive ones (three lesson calls plus three judge
    calls each instead of one), so quietly flipping a fourth case on — or
    dropping one and leaving the continuity item without evidence — should not
    be possible while the docs claim this shape.
    """
    dataset = load_seed_set()
    full_path = {case.name for case in dataset.cases if case.inputs.full_path}
    assert full_path == {
        "typescript-for-javascript-devs",
        "fall-of-the-roman-republic",
        "home-network-security",
    }
    # One per non-boundary bucket, and never a refusal case (which has no path).
    for case in dataset.cases:
        if case.inputs.full_path:
            assert case.inputs.expected_branch == "generate", case.name


@pytest.mark.anyio
async def test_full_path_case_generates_lessons_sequentially_with_priors() -> None:
    """Lesson N carries the real Read passages of lessons 1..N-1 (TDD §5.2/§11).

    This is what makes the rubric's continuity item falsifiable: the generator
    is given what it must build on, and the judge is given the same text to
    check it against. A probe-lesson-only harness can assert neither.
    """
    dataset = load_seed_set()
    stub = smoke_model()
    task = build_generation_task(stub, stub, force_expected_branch=True)

    case = next(case for case in dataset.cases if case.inputs.full_path)
    sample = await task(case.inputs)
    assert isinstance(sample.outline, PathOutline)
    assert len(sample.lessons) == FULL_PATH_LESSONS

    # Path order, 1-based and contiguous — the same total-order numbering the
    # orchestrator assigns (TDD §4).
    assert [lesson.position_in_path for lesson in sample.lessons] == list(
        range(1, FULL_PATH_LESSONS + 1)
    )
    ordered_titles = [
        lesson.title for unit in sample.outline.units for lesson in unit.lessons
    ]
    assert [lesson.lesson_title for lesson in sample.lessons] == ordered_titles[
        :FULL_PATH_LESSONS
    ]

    # Lesson 1 has no priors; lesson 2 has lesson 1; lesson 3 has both, in order.
    assert sample.priors_for(0) == ()
    priors_for_second = sample.priors_for(1)
    assert len(priors_for_second) == 1
    assert priors_for_second[0].read_passage == sample.lessons[0].content.read_passage
    assert priors_for_second[0].lesson_title == sample.lessons[0].lesson_title
    assert len(sample.priors_for(2)) == 2


@pytest.mark.anyio
async def test_full_path_depth_is_configurable_and_ordinary_cases_are_unaffected() -> (
    None
):
    """``--full-path-lessons`` is the cost knob; it moves only full-path cases."""
    dataset = load_seed_set()
    stub = smoke_model()
    task = build_generation_task(
        stub, stub, force_expected_branch=True, full_path_lessons=2
    )

    full_path = next(case for case in dataset.cases if case.inputs.full_path)
    assert len((await task(full_path.inputs)).lessons) == 2

    ordinary = next(
        case
        for case in dataset.cases
        if case.inputs.expected_branch == "generate" and not case.inputs.full_path
    )
    assert len((await task(ordinary.inputs)).lessons) == 1


# --- exit-code classification (evals/__main__.py) ------------------------------


@dataclass(repr=False)
class _CrashingBranchCheck(Evaluator[SeedInputs, GenerationSample, SeedMeta]):
    """Stands in for a hard-floor evaluator that raises instead of scoring.

    Plausible causes in practice: an output-schema change the predicate does not
    expect, or a shared validator raising something other than ``ModelRetry``.
    """

    def evaluate(
        self, ctx: EvaluatorContext[SeedInputs, GenerationSample, SeedMeta]
    ) -> EvaluationReason:
        raise RuntimeError("predicate exploded")


async def _report_with_a_crashed_branch_check() -> EvaluationReport[
    SeedInputs, GenerationSample, SeedMeta
]:
    """A real report shaped exactly like a crashed ``RefusalBranch``.

    The case is the worst one to leave unscored: a ``refuse`` case that
    *outlined*, i.e. the under-refusal ``RefusalBranch`` exists to catch. The
    two surviving pre-filters both pass it happily — the outline is well-formed
    and so is the lesson — so nothing but the crash handling stands between this
    and a green run.
    """
    return await _probe_report(
        SeedInputs(topic="anything", level="beginner", expected_branch="refuse"),
        _sample(_valid_outline(), _valid_lesson()),
        evaluators=[
            _CrashingBranchCheck(),
            *(
                prefilter()
                for prefilter in PREFILTERS
                if prefilter is not RefusalBranch
            ),
        ],
    )


@pytest.mark.anyio
async def test_a_crashed_evaluator_fails_the_hard_floor() -> None:
    """An unscored safety check must exit 1, never 0."""
    report = await _report_with_a_crashed_branch_check()

    # How pydantic-evals represents it: the case survives, the crash lands in
    # `evaluator_failures`, and the assertion simply is not there. Nothing on
    # the ordinary pass/fail path notices, which is the whole trap.
    assert not report.failures
    assert "RefusalBranch" not in report.cases[0].assertions
    assert all(result.value for result in report.cases[0].assertions.values())
    assert report.cases[0].evaluator_failures

    failures = _hard_floor_failures(report)
    assert any(
        "_CrashingBranchCheck" in failure and "evaluator errored" in failure
        for failure in failures
    ), failures
    assert any(
        "RefusalBranch" in failure and "missing" in failure for failure in failures
    ), failures


@pytest.mark.anyio
async def test_a_dropped_hard_floor_evaluator_fails_the_hard_floor() -> None:
    """An unregistered pre-filter is a harness bug, not an implicit pass."""
    report = await _probe_report(
        SeedInputs(topic="anything", level="beginner", expected_branch="refuse"),
        _sample(_valid_outline(), _valid_lesson()),
        evaluators=[
            prefilter()
            for prefilter in PREFILTERS
            if prefilter is not RefusalBranch  # e.g. dropped in a refactor
        ],
    )
    assert not report.cases[0].evaluator_failures
    assert any("RefusalBranch" in failure for failure in _hard_floor_failures(report))


@pytest.mark.anyio
async def test_the_report_artifact_records_crashes_and_the_lesson_slot() -> None:
    """The JSON artifact must not render an unscored case as a clean pass."""
    payload = _case_payload(await _report_with_a_crashed_branch_check())
    (case,) = payload["cases"]
    assert [failure["name"] for failure in case["evaluator_failures"]] == [
        "_CrashingBranchCheck"
    ]
    assert "predicate exploded" in case["evaluator_failures"][0]["error"]
    # Which probe lesson the case actually generated, so a report row can be
    # read against the outline it came from.
    assert case["lesson_slot"] == "Unit 1 / Lesson 1"


def test_smoke_refuses_a_model_sweep() -> None:
    """``--smoke --models …`` would look like an offline sweep of those models."""
    with pytest.raises(SystemExit) as exit_info:
        main(["--smoke", "--models", "anthropic/claude-haiku-4-5"])
    assert exit_info.value.code == 2  # misconfiguration, same as a missing key
