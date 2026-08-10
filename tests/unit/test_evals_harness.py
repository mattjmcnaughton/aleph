"""Unit tests for the agent eval harness (``evals/``, TDD §11, docs/evals.md).

Keyless and offline. Four things are under test:

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
4. **The `brief` kind's Layer 1 imports** (Phase 6 TDD §10, AL-550) — that
   ``evals/generation.py``'s pre-filters use the *same objects*
   ``aleph.agents.researcher.cites_only_read_documents`` and
   ``aleph.domains.novelty.filter_new`` export, by identity, never a
   re-implementation — plus the brief seed set's own shape and smoke run.

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

from aleph.agents.analyst import AnalystDeps, BriefBody, SkippedNote
from aleph.agents.flashcard import FlashcardDraft, FlashcardDrafts
from aleph.agents.lesson import LessonCaps, LessonContent, QuickCheck
from aleph.agents.outline import (
    LessonOutline,
    Level,
    OutlineCaps,
    PathOutline,
    Refusal,
    UnitOutline,
)
from aleph.agents.researcher import Finding
from aleph.agents.researcher import cites_only_read_documents as agent_cites_only_read
from aleph.config import Settings
from aleph.domains.novelty import filter_new as domain_filter_new
from aleph.services.generation import _lesson_caps_from, _outline_caps_from
from aleph.services.retrieval import FixtureRetriever, build_query_plan, retrieve
from evals import generation as harness_generation
from evals.__main__ import (
    GateSummary,
    _case_payload,
    _gate_summary,
    _hard_floor_failures,
    _render_gate_summary,
    main,
)
from evals.generation import (
    BRIEF_FIXTURES_DIR,
    BRIEF_HARD_FLOOR_EVALUATORS,
    BRIEF_PREFILTERS,
    BRIEF_RETRIEVAL_MAX_DOCUMENTS,
    BRIEF_RETRIEVAL_MAX_QUERIES,
    BRIEF_RETRIEVAL_TEXT_BUDGET_CHARS,
    BRIEF_SEED_SET_PATH,
    FLASHCARD_CAPS,
    FLASHCARD_HARD_FLOOR_EVALUATORS,
    FLASHCARD_PREFILTERS,
    FLASHCARD_SEED_SET_PATH,
    FULL_PATH_LESSONS,
    HARD_FLOOR_EVALUATORS,
    LESSON_CAPS,
    OUTLINE_CAPS,
    PREFILTERS,
    BriefSample,
    BriefSeedInputs,
    FlashcardSample,
    FlashcardSeedInputs,
    GeneratedLesson,
    GenerationSample,
    RefusalBranch,
    SeedInputs,
    SeedMeta,
    SyntheticPriorBrief,
    build_brief_generation_task,
    build_brief_smoke_model,
    build_flashcard_generation_task,
    build_generation_task,
    is_placeholder_fixture,
    load_brief_seed_set,
    load_flashcard_seed_set,
    load_seed_set,
    smoke_model,
)

if TYPE_CHECKING:
    from pathlib import Path

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
    assert exit_info.value.code == 2


# =================================================================================
# The `flashcard_draft` eval kind (Phase 3 TDD D14/§10; PRD §6)
# =================================================================================


def _valid_flashcards(count: int = 4) -> list[FlashcardDraft]:
    """``count`` distinct, cap-respecting drafts, none restating any stem below."""
    return [
        FlashcardDraft(
            front=f"Name the fact {i} this lesson teaches.", back=f"Fact {i}."
        )
        for i in range(count)
    ]


def _flashcard_sample(
    cards: list[FlashcardDraft],
    *,
    stem: str = "Which statement is correct?",
    unit_title: str = "Unit 1",
    lesson_title: str = "Lesson 1",
    read_passage: str = " ".join(["word"] * 250),
) -> FlashcardSample:
    return FlashcardSample(
        unit_title=unit_title,
        lesson_title=lesson_title,
        read_passage=read_passage,
        quick_check_stem=stem,
        drafts=FlashcardDrafts(cards=cards),
    )


_FLASHCARD_INPUTS = FlashcardSeedInputs(topic="anything", level="beginner")


async def _flashcard_probe_report(
    inputs: FlashcardSeedInputs,
    sample: FlashcardSample,
    evaluators: list[Evaluator[FlashcardSeedInputs, FlashcardSample, SeedMeta]]
    | None = None,
) -> EvaluationReport[FlashcardSeedInputs, FlashcardSample, SeedMeta]:
    """Score one hand-built flashcard sample through the real pydantic-evals path.

    Mirrors :func:`_probe_report` above, for the ``flashcard_draft`` kind's own
    inputs/output types.
    """

    async def task(_inputs: FlashcardSeedInputs) -> FlashcardSample:
        return sample

    dataset = Dataset[FlashcardSeedInputs, FlashcardSample, SeedMeta](
        name="flashcard-prefilter-probe",
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
            else [prefilter() for prefilter in FLASHCARD_PREFILTERS]
        ),
    )
    return await dataset.evaluate(task, progress=False)


async def _flashcard_assess(
    inputs: FlashcardSeedInputs, sample: FlashcardSample
) -> dict[str, tuple[bool, str | None]]:
    """Run both flashcard Layer 1 pre-filters: ``{name: (passed, reason)}``."""
    report = await _flashcard_probe_report(inputs, sample)
    assert not report.failures, "the probe task itself errored"
    return {
        name: (result.value, result.reason)
        for name, result in report.cases[0].assertions.items()
    }


# --- the flashcard_draft seed set ------------------------------------------------


def test_flashcard_seed_set_loads_and_every_case_is_complete() -> None:
    dataset = load_flashcard_seed_set()
    # A representative subset of seed_set.yaml's `generate` cases, not a second
    # copy of the full twenty (see the file's own header on cost).
    assert 5 <= len(dataset.cases) <= 12, f"{len(dataset.cases)} cases"

    names = [case.name for case in dataset.cases]
    assert len(names) == len(set(names)), "case names must be unique"

    valid_levels = set(get_args(Level))
    for case in dataset.cases:
        assert case.name, "every case needs a name (it is the report's row id)"
        assert case.inputs.topic.strip(), f"{case.name}: empty topic"
        assert case.inputs.level in valid_levels, f"{case.name}: bad level"
        assert case.metadata is not None, f"{case.name}: missing metadata"
        assert case.metadata.note.strip(), f"{case.name}: empty curation note"


def test_flashcard_seed_set_reuses_the_phase_one_seed_topics() -> None:
    """TDD §10: "the passages under test are the ones the lesson evals already
    judge" — every flashcard case must be the *same* (topic, level) as a
    `generate` case in seed_set.yaml, under the same name."""
    outline_lesson_cases = {case.name: case.inputs for case in load_seed_set().cases}
    for case in load_flashcard_seed_set().cases:
        reused = outline_lesson_cases.get(case.name)
        assert reused is not None, f"{case.name}: not a seed_set.yaml case name"
        assert reused.topic == case.inputs.topic, case.name
        assert reused.level == case.inputs.level, case.name
        # A refused topic has no lesson to draft a card from.
        assert reused.expected_branch == "generate", case.name


def test_flashcard_seed_set_registers_both_hard_floor_prefilters() -> None:
    dataset = load_flashcard_seed_set()
    registered = {type(evaluator).__name__ for evaluator in dataset.evaluators}
    assert registered >= {cls.__name__ for cls in FLASHCARD_PREFILTERS}
    assert registered >= FLASHCARD_HARD_FLOOR_EVALUATORS
    assert "MaxDuration" in registered


def test_flashcard_seed_set_path_matches_the_file_on_disk() -> None:
    assert FLASHCARD_SEED_SET_PATH.name == "flashcard_seed_set.yaml"
    assert FLASHCARD_SEED_SET_PATH.is_file()


# --- Layer 1: FlashcardInvariants (structural bands, PRD §6 scope pre-filter) ---


@pytest.mark.anyio
async def test_flashcard_invariants_rejects_a_count_violation() -> None:
    too_many = _valid_flashcards(FLASHCARD_CAPS.count_max + 1)
    assertions = await _flashcard_assess(_FLASHCARD_INPUTS, _flashcard_sample(too_many))
    passed, reason = assertions["FlashcardInvariants"]
    assert not passed
    assert reason is not None and "band" in reason
    # The shared validator is the only source of the rule: an over-count draft
    # must not also trip the non-triviality pre-filter.
    assert assertions["FlashcardNonTriviality"][0]


@pytest.mark.anyio
async def test_flashcard_invariants_rejects_an_empty_side() -> None:
    cards = _valid_flashcards(3)
    cards[1] = FlashcardDraft(front="   ", back="An answer.")
    assertions = await _flashcard_assess(_FLASHCARD_INPUTS, _flashcard_sample(cards))
    passed, reason = assertions["FlashcardInvariants"]
    assert not passed
    assert reason is not None and "empty front" in reason


@pytest.mark.anyio
async def test_flashcard_invariants_rejects_a_word_cap_violation() -> None:
    cards = _valid_flashcards(3)
    cards[0] = FlashcardDraft(
        front=" ".join(["word"] * (FLASHCARD_CAPS.front_words_max + 5)),
        back="An answer.",
    )
    assertions = await _flashcard_assess(_FLASHCARD_INPUTS, _flashcard_sample(cards))
    passed, reason = assertions["FlashcardInvariants"]
    assert not passed
    assert reason is not None and "front" in reason and "words" in reason


@pytest.mark.anyio
async def test_flashcard_invariants_rejects_a_back_that_repeats_the_front() -> None:
    cards = _valid_flashcards(3)
    cards[2] = FlashcardDraft(front="The same text.", back="The same text.")
    assertions = await _flashcard_assess(_FLASHCARD_INPUTS, _flashcard_sample(cards))
    passed, reason = assertions["FlashcardInvariants"]
    assert not passed
    assert reason is not None and "repeats the front" in reason


@pytest.mark.anyio
async def test_flashcard_invariants_accepts_cards_within_caps() -> None:
    assertions = await _flashcard_assess(
        _FLASHCARD_INPUTS, _flashcard_sample(_valid_flashcards(4))
    )
    assert assertions["FlashcardInvariants"][0]


# --- Layer 1: FlashcardNonTriviality (PRD §6 non-triviality, shared with the ---
# --- agent's own restates_stem validator) ---------------------------------------


@pytest.mark.anyio
async def test_flashcard_non_triviality_rejects_a_restated_stem() -> None:
    stem = "What does the extends keyword constrain in a generic type parameter?"
    cards = _valid_flashcards(3)
    cards[0] = FlashcardDraft(front=stem, back="It constrains T to the bound.")
    assertions = await _flashcard_assess(
        _FLASHCARD_INPUTS, _flashcard_sample(cards, stem=stem)
    )
    passed, reason = assertions["FlashcardNonTriviality"]
    assert not passed
    assert reason is not None and "restate" in reason
    # Not a structural violation: the card set is otherwise well-formed.
    assert assertions["FlashcardInvariants"][0]


@pytest.mark.anyio
async def test_flashcard_non_triviality_accepts_distinct_cards() -> None:
    assertions = await _flashcard_assess(
        _FLASHCARD_INPUTS, _flashcard_sample(_valid_flashcards(4))
    )
    assert assertions["FlashcardNonTriviality"][0]


# --- the flashcard smoke run ------------------------------------------------------


@pytest.mark.anyio
async def test_flashcard_smoke_run_passes_every_assertion() -> None:
    """The same path ``just evals --smoke --flashcards`` takes, in the gate."""
    dataset = load_flashcard_seed_set()
    stub = smoke_model()
    report = await dataset.evaluate(
        build_flashcard_generation_task(stub, stub, stub),
        name="flashcard-smoke-unit",
        progress=False,
    )
    assert not report.failures
    assert len(report.cases) == len(dataset.cases)
    for case in report.cases:
        for name, result in case.assertions.items():
            assert result.value, f"{case.name} / {name}: {result.reason}"
        # outline + lesson + drafting: at least 3 requests for a clean case.
        assert case.metrics["model_requests"] >= 3


@pytest.mark.anyio
async def test_flashcard_generation_task_drafts_from_a_freshly_generated_lesson() -> (
    None
):
    """The task's output carries the real generated passage/stem, not a fixture."""
    dataset = load_flashcard_seed_set()
    stub = smoke_model()
    task = build_flashcard_generation_task(stub, stub, stub)

    case = dataset.cases[0]
    sample = await task(case.inputs)
    assert sample.unit_title and sample.lesson_title
    assert sample.read_passage.strip()
    assert sample.quick_check_stem.strip()
    assert (
        FLASHCARD_CAPS.count_min <= len(sample.drafts.cards) <= FLASHCARD_CAPS.count_max
    )
    for card in sample.drafts.cards:
        assert card.front.strip()
        assert card.back.strip()


def test_flashcard_smoke_refuses_a_model_sweep() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--smoke", "--flashcards", "--models", "anthropic/claude-haiku-4-5"])
    assert exit_info.value.code == 2  # misconfiguration, same as a missing key


# =================================================================================
# The `brief` eval kind (Phase 6 TDD §10, AL-550; PRD §6)
# =================================================================================


def test_layer1_imports_the_shipped_provenance_and_novelty_functions() -> None:
    """TDD §10: "Layer 1 imports the shipped functions, never a second
    spelling." An identity assertion, not a behavioural one — the harness's
    predicates must be the *same objects* the agents/domains modules export,
    not merely functions that happen to behave the same way today.
    """
    assert harness_generation.cites_only_read_documents is agent_cites_only_read
    assert harness_generation.filter_new is domain_filter_new


def test_brief_retrieval_budget_matches_settings_defaults() -> None:
    """The brief harness's retrieval budget is the §14-style discipline every
    other CAPS constant in this module follows — pinned equal to
    ``Settings()``'s own defaults so a config default cannot move and leave
    this harness silently stale (mirrors
    ``test_harness_caps_match_the_ones_the_service_builds_from_settings``).
    """
    config = Settings(_env_file=None)  # ty: ignore[unknown-argument]
    assert config.brief_retrieval_max_queries == BRIEF_RETRIEVAL_MAX_QUERIES
    assert config.brief_retrieval_max_documents == BRIEF_RETRIEVAL_MAX_DOCUMENTS
    assert config.brief_retrieval_text_budget_chars == BRIEF_RETRIEVAL_TEXT_BUDGET_CHARS


# --- the brief seed set ---------------------------------------------------------


def test_brief_seed_set_loads_and_every_case_is_complete() -> None:
    dataset = load_brief_seed_set()
    # Four cases, TDD §10: "four cases over subjects that genuinely move".
    assert len(dataset.cases) == 4

    names = [case.name for case in dataset.cases]
    assert len(names) == len(set(names)), "case names must be unique"

    valid_levels = set(get_args(Level))
    for case in dataset.cases:
        assert case.name, "every case needs a name (it is the report's row id)"
        assert case.inputs.topic.strip(), f"{case.name}: empty topic"
        assert case.inputs.level in valid_levels, f"{case.name}: bad level"
        assert case.inputs.beat_fixture.strip(), f"{case.name}: empty beat_fixture"
        assert case.metadata is not None, f"{case.name}: missing metadata"
        assert case.metadata.note.strip(), f"{case.name}: empty curation note"
        prior = case.inputs.prior_brief
        assert prior.claims, f"{case.name}: synthetic prior Brief has no claims"
        assert prior.source_urls, f"{case.name}: synthetic prior Brief has no Sources"
        assert prior.summary.strip(), f"{case.name}: prior Brief has no summary"


def test_brief_seed_set_every_fixture_file_exists_and_is_keyed_correctly() -> None:
    """Each case's ``beat_fixture`` is both the fixture's filename stem and the
    ``beat`` key inside it — ``FixtureRetriever`` refuses to replay a
    mismatched one, so a case pinned to a fixture that does not exist, or
    that is keyed for a different beat, would fail every run for a reason
    that has nothing to do with generation quality.
    """
    import yaml

    for case in load_brief_seed_set().cases:
        path = BRIEF_FIXTURES_DIR / f"{case.inputs.beat_fixture}.yaml"
        assert path.is_file(), f"{case.name}: no fixture at {path}"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["beat"] == case.inputs.beat_fixture, case.name
        assert raw["queries"], f"{case.name}: fixture recorded no queries"
        assert raw["results"], f"{case.name}: fixture recorded no results"


def test_brief_seed_set_registers_every_hard_floor_prefilter() -> None:
    dataset = load_brief_seed_set()
    registered = {type(evaluator).__name__ for evaluator in dataset.evaluators}
    assert registered >= {cls.__name__ for cls in BRIEF_PREFILTERS}
    assert registered >= BRIEF_HARD_FLOOR_EVALUATORS
    assert "MaxDuration" in registered


def test_brief_seed_set_path_matches_the_file_on_disk() -> None:
    assert BRIEF_SEED_SET_PATH.name == "brief_seed_set.yaml"
    assert BRIEF_SEED_SET_PATH.is_file()


# --- Layer 1: BriefProvenance (TDD D8, shared with the agents' own validators) --


def _brief_inputs(**overrides: object) -> BriefSeedInputs:
    defaults: dict[str, object] = {
        "topic": "A moving subject",
        "level": "intermediate",
        "beat_fixture": "unused-in-this-probe",
        "prior_brief": SyntheticPriorBrief(
            number=1,
            published_on="2026-01-01",  # type: ignore[arg-type]
            claims=["An earlier claim."],
            source_urls=["https://example.com/prior"],
            summary="An earlier Brief's summary.",
        ),
    }
    defaults.update(overrides)
    return BriefSeedInputs.model_validate(defaults)


def _finding(url: str, *, index: int = 1) -> Finding:
    return Finding(
        claim=f"Claim {index}.",
        detail=f"Detail {index}.",
        source_urls=[url],
        happened_on=None,
    )


async def _brief_probe_report(
    inputs: BriefSeedInputs,
    sample: BriefSample,
    evaluators: list[Evaluator[BriefSeedInputs, BriefSample, SeedMeta]] | None = None,
) -> EvaluationReport[BriefSeedInputs, BriefSample, SeedMeta]:
    """Score one hand-built brief sample through the real pydantic-evals path.

    Mirrors :func:`_probe_report`/:func:`_flashcard_probe_report` above, for
    the ``brief`` kind's own inputs/output types.
    """

    async def task(_inputs: BriefSeedInputs) -> BriefSample:
        return sample

    dataset = Dataset[BriefSeedInputs, BriefSample, SeedMeta](
        name="brief-prefilter-probe",
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
            else [prefilter() for prefilter in BRIEF_PREFILTERS]
        ),
    )
    return await dataset.evaluate(task, progress=False)


async def _brief_assess(
    inputs: BriefSeedInputs, sample: BriefSample
) -> dict[str, tuple[bool, str | None]]:
    """Run both brief Layer 1 pre-filters: ``{name: (passed, reason)}``."""
    report = await _brief_probe_report(inputs, sample)
    assert not report.failures, "the probe task itself errored"
    return {
        name: (result.value, result.reason)
        for name, result in report.cases[0].assertions.items()
    }


@pytest.mark.anyio
async def test_brief_provenance_rejects_a_finding_citing_an_unread_url() -> None:
    finding = _finding("https://example.com/unread")
    sample = BriefSample(
        document_urls=["https://example.com/read"],
        findings=[finding],
        survivors=[finding],
        result=SkippedNote(detail=""),
    )
    assertions = await _brief_assess(_brief_inputs(), sample)
    passed, reason = assertions["BriefProvenance"]
    assert not passed
    assert reason is not None and "outside this run's retrieved documents" in reason


@pytest.mark.anyio
async def test_brief_provenance_rejects_a_finding_with_no_source_urls() -> None:
    finding = Finding(
        claim="Claim.", detail="Detail.", source_urls=[], happened_on=None
    )
    sample = BriefSample(
        document_urls=["https://example.com/read"],
        findings=[finding],
        survivors=[],
        result=SkippedNote(detail=""),
    )
    assertions = await _brief_assess(_brief_inputs(), sample)
    passed, reason = assertions["BriefProvenance"]
    assert not passed
    assert reason is not None and "cites no URL" in reason


@pytest.mark.anyio
async def test_brief_provenance_rejects_a_brief_citing_an_unread_url() -> None:
    finding = _finding("https://example.com/read")
    sample = BriefSample(
        document_urls=["https://example.com/read"],
        findings=[finding],
        survivors=[finding],
        result=BriefBody(
            title="A Brief",
            body_markdown="Body.",
            cited_urls=["https://example.com/unread"],
        ),
    )
    assertions = await _brief_assess(_brief_inputs(), sample)
    passed, reason = assertions["BriefProvenance"]
    assert not passed
    assert reason is not None and "outside this run's retrieved documents" in reason


@pytest.mark.anyio
async def test_brief_provenance_rejects_a_brief_with_no_cited_urls() -> None:
    finding = _finding("https://example.com/read")
    sample = BriefSample(
        document_urls=["https://example.com/read"],
        findings=[finding],
        survivors=[finding],
        result=BriefBody(title="A Brief", body_markdown="Body.", cited_urls=[]),
    )
    assertions = await _brief_assess(_brief_inputs(), sample)
    passed, reason = assertions["BriefProvenance"]
    assert not passed
    assert reason is not None and "no cited_urls" in reason


@pytest.mark.anyio
async def test_brief_provenance_accepts_a_clean_brief() -> None:
    finding = _finding("https://example.com/read")
    sample = BriefSample(
        document_urls=["https://example.com/read"],
        findings=[finding],
        survivors=[finding],
        result=BriefBody(
            title="A Brief",
            body_markdown="Body.",
            cited_urls=["https://example.com/read"],
        ),
    )
    assertions = await _brief_assess(_brief_inputs(), sample)
    assert assertions["BriefProvenance"][0]


# --- Layer 1: BriefNoveltyGate (TDD D9, shared with domains/novelty.py) --------


@pytest.mark.anyio
async def test_brief_novelty_gate_rejects_a_skip_when_findings_survive() -> None:
    """Findings that are genuinely new (not covered by the prior Brief) must
    produce a Brief, not a SkippedNote."""
    finding = _finding("https://example.com/brand-new")
    inputs = _brief_inputs(
        prior_brief=SyntheticPriorBrief(
            number=1,
            published_on="2026-01-01",  # type: ignore[arg-type]
            claims=["A claim about something else entirely, unrelated in every way."],
            source_urls=["https://example.com/prior"],
            summary="Prior summary.",
        )
    )
    sample = BriefSample(
        document_urls=["https://example.com/brand-new"],
        findings=[finding],
        survivors=[finding],
        result=SkippedNote(detail=""),
    )
    assertions = await _brief_assess(inputs, sample)
    passed, reason = assertions["BriefNoveltyGate"]
    assert not passed
    assert reason is not None and "SkippedNote was returned" in reason
    # Not a provenance failure: the sample is otherwise clean.
    assert assertions["BriefProvenance"][0]


@pytest.mark.anyio
async def test_brief_novelty_gate_rejects_a_brief_when_nothing_survives() -> None:
    """A finding whose only URL was already cited by the prior Brief must
    produce a SkippedNote, never a Brief written anyway."""
    finding = _finding("https://example.com/prior")
    inputs = _brief_inputs(
        prior_brief=SyntheticPriorBrief(
            number=1,
            published_on="2026-01-01",  # type: ignore[arg-type]
            claims=["An earlier claim."],
            source_urls=["https://example.com/prior"],
            summary="Prior summary.",
        )
    )
    sample = BriefSample(
        document_urls=["https://example.com/prior"],
        findings=[finding],
        survivors=[],
        result=BriefBody(
            title="A Brief",
            body_markdown="Body.",
            cited_urls=["https://example.com/prior"],
        ),
    )
    assertions = await _brief_assess(inputs, sample)
    passed, reason = assertions["BriefNoveltyGate"]
    assert not passed
    assert reason is not None and "written anyway" in reason


@pytest.mark.anyio
async def test_brief_novelty_gate_accepts_a_skip_when_nothing_survives() -> None:
    finding = _finding("https://example.com/prior")
    inputs = _brief_inputs(
        prior_brief=SyntheticPriorBrief(
            number=1,
            published_on="2026-01-01",  # type: ignore[arg-type]
            claims=[],
            source_urls=["https://example.com/prior"],
            summary="Prior summary.",
        )
    )
    sample = BriefSample(
        document_urls=["https://example.com/prior"],
        findings=[finding],
        survivors=[],
        result=SkippedNote(detail=""),
    )
    assertions = await _brief_assess(inputs, sample)
    assert assertions["BriefNoveltyGate"][0]


@pytest.mark.anyio
async def test_brief_novelty_gate_accepts_a_brief_when_something_survives() -> None:
    finding = _finding("https://example.com/brand-new")
    sample = BriefSample(
        document_urls=["https://example.com/brand-new"],
        findings=[finding],
        survivors=[finding],
        result=BriefBody(
            title="A Brief",
            body_markdown="Body.",
            cited_urls=["https://example.com/brand-new"],
        ),
    )
    assertions = await _brief_assess(_brief_inputs(), sample)
    assert assertions["BriefNoveltyGate"][0]


# --- the brief smoke run ---------------------------------------------------------


@pytest.mark.anyio
async def test_brief_smoke_run_passes_every_assertion() -> None:
    """The same path ``just evals --smoke --briefs`` takes, in the gate.

    Real agents, real prompts, real output validators, real seed set, real
    fixture replay (never Exa) — only the researcher/analyst model is the
    deterministic stub. Harness breakage is therefore caught by ``just gate``
    while real eval runs stay opt-in and offline-free.
    """
    dataset = load_brief_seed_set()
    stub = build_brief_smoke_model()
    report = await dataset.evaluate(
        build_brief_generation_task(stub, stub),
        name="brief-smoke-unit",
        progress=False,
    )
    assert not report.failures
    assert len(report.cases) == len(dataset.cases)
    for case in report.cases:
        for name, result in case.assertions.items():
            assert result.value, f"{case.name} / {name}: {result.reason}"
        # researcher + analyst: exactly 2 requests for a clean case.
        assert case.metrics["model_requests"] >= 2


@pytest.mark.anyio
async def test_brief_generation_task_replays_the_fixture_and_gates_novelty() -> None:
    """The task's output really comes from the fixture, and the novelty gate
    really drops the finding backed only by an already-cited URL."""
    dataset = load_brief_seed_set()
    stub = build_brief_smoke_model()
    task = build_brief_generation_task(stub, stub)

    case = dataset.cases[0]
    sample = await task(case.inputs)
    assert sample.document_urls, "the fixture must have produced documents"
    assert sample.findings, "the stub must have produced at least one finding"
    # The prior Brief's own Source URL, reused in the fixture, must be dropped
    # by the novelty gate — never surviving into what the analyst may cite.
    prior_urls = set(case.inputs.prior_brief.source_urls)
    surviving_urls = {
        url for finding in sample.survivors for url in finding.source_urls
    }
    assert not (surviving_urls & prior_urls)
    assert len(sample.survivors) < len(sample.findings)


def test_brief_smoke_refuses_a_model_sweep() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--smoke", "--briefs", "--models", "anthropic/claude-haiku-4-5"])
    assert exit_info.value.code == 2  # misconfiguration, same as a missing key


def test_briefs_and_flashcards_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--smoke", "--briefs", "--flashcards"])
    assert exit_info.value.code == 2


@pytest.mark.anyio
async def test_brief_stale_fixture_errors_the_case_rather_than_scoring_a_pass() -> None:
    """The stale-fixture / RetrievalUnavailableError hazard, pinned directly.

    ``FixtureRetriever`` raises ``RetrievalUnavailableError`` on a miss (a
    stale or mistyped ``beat_fixture``, src/aleph/services/retrieval.py) —
    exactly the error a real research run maps to a ``failed`` state for
    (§5.7's "never Skipped" row). This harness never catches it anywhere
    (``evals/__main__.py``/``evals/generation.py``): it propagates out of
    ``build_brief_generation_task``'s task, so pydantic-evals records the
    case as an outright task failure (``report.failures``, zero scored
    ``report.cases``) rather than any assertion being scored at all. A stale
    fixture can therefore never present as a PASS — ``_hard_floor_failures``
    counts every ``report.failures`` entry unconditionally, so the CLI exits
    1 exactly as it would for a hard-floor rejection.
    """
    inputs = _brief_inputs(beat_fixture="this-fixture-does-not-exist")
    stub = build_brief_smoke_model()
    task = build_brief_generation_task(stub, stub)

    dataset = Dataset[BriefSeedInputs, BriefSample, SeedMeta](
        name="stale-fixture-probe",
        cases=[
            Case(
                name="stale",
                inputs=inputs,
                metadata=SeedMeta(category="technical", note="unit-test probe"),
            )
        ],
        evaluators=[prefilter() for prefilter in BRIEF_PREFILTERS],
    )
    report = await dataset.evaluate(task, progress=False)

    # No case was ever scored — a stale fixture never reaches an assertion,
    # passing or failing.
    assert report.cases == []
    assert len(report.failures) == 1
    assert "RetrievalUnavailableError" in report.failures[0].error_message

    failures = _hard_floor_failures(
        report,
        BRIEF_HARD_FLOOR_EVALUATORS,
        assertions_map=harness_generation.BRIEF_EVALUATOR_ASSERTIONS,
    )
    assert any("errored" in failure for failure in failures)


# --- FIX 2: the placeholder-fixture marker (code-review, AL-550) -----------------


def test_every_committed_brief_fixture_is_marked_a_placeholder() -> None:
    """All four committed fixtures are hand-authored placeholders today
    (docs/evals.md); each must carry the marker the runtime banner keys off,
    or the disclosure silently stops firing."""
    for case in load_brief_seed_set().cases:
        assert is_placeholder_fixture(case.inputs.beat_fixture), (
            case.inputs.beat_fixture
        )


def test_is_placeholder_fixture_is_false_once_the_marker_is_gone(
    tmp_path: Path,
) -> None:
    """A real recording (``just record-retrieval-fixtures`` never writes
    ``placeholder: true``) must read as NOT a placeholder — the disclosure
    has to disappear on its own, with nothing to remember to update."""
    (tmp_path / "real-beat.yaml").write_text(
        "beat: real-beat\nqueries: [q]\nresults: {q: []}\n"
    )
    assert is_placeholder_fixture("real-beat", fixtures_dir=tmp_path) is False


def test_is_placeholder_fixture_is_false_for_a_missing_file(tmp_path: Path) -> None:
    """Not this function's problem to raise on — ``FixtureRetriever`` is what
    turns a missing/stale fixture into a hard failure."""
    assert is_placeholder_fixture("does-not-exist", fixtures_dir=tmp_path) is False


# --- FIX 3: open_threads mirrors production's open_thread_claims -----------------


@pytest.mark.anyio
async def test_open_threads_carries_the_prior_briefs_claims_not_its_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code-review FIX 3: the harness's ``AnalystDeps.open_threads`` must be
    the synthetic prior Brief's ``claims`` — matching
    ``services/briefing.py``'s ``open_threads=list(context.
    open_thread_claims)`` — never its prose ``summary``. The old behaviour
    handed the analyst a recap that opens by naming the prior Brief ("Brief
    #6 reported…"), a shape production's own ``open_thread_claims`` never
    produces, and then judged ``continuous`` against a prompt that told the
    analyst to address it explicitly.
    """
    captured: list[list[str]] = []
    real_build_analyst_prompt = harness_generation.build_analyst_prompt

    def _spy(deps: AnalystDeps) -> str:
        captured.append(list(deps.open_threads))
        return real_build_analyst_prompt(deps)

    monkeypatch.setattr(harness_generation, "build_analyst_prompt", _spy)

    dataset = load_brief_seed_set()
    case = dataset.cases[0]
    stub = build_brief_smoke_model()
    task = build_brief_generation_task(stub, stub)
    await task(case.inputs)

    assert captured, "build_analyst_prompt was never called"
    (open_threads,) = captured
    assert open_threads == list(case.inputs.prior_brief.claims)
    assert case.inputs.prior_brief.summary not in open_threads


# --- FIX 5: a Skipped case is excluded from the pass-rate denominator ------------


async def _all_skip_inputs(inputs: BriefSeedInputs) -> BriefSeedInputs:
    """``inputs``, but with its synthetic prior Brief widened to cite every
    URL its own fixture actually returns — forcing ``filter_new`` to drop
    every finding, so the case Skips (the concrete FIX 5 scenario: someone
    updates the seed set's ``prior_brief`` to match freshly re-recorded
    fixtures)."""
    retriever = FixtureRetriever(BRIEF_FIXTURES_DIR, inputs.beat_fixture)
    plan = build_query_plan(
        inputs.topic,
        inputs.guidance,
        since=inputs.prior_brief.published_on,
        max_queries=BRIEF_RETRIEVAL_MAX_QUERIES,
    )
    documents = await retrieve(
        retriever,
        plan,
        max_documents=BRIEF_RETRIEVAL_MAX_DOCUMENTS,
        text_budget_chars=BRIEF_RETRIEVAL_TEXT_BUDGET_CHARS,
    )
    return inputs.model_copy(
        update={
            "prior_brief": inputs.prior_brief.model_copy(
                update={"source_urls": [document.url for document in documents]}
            )
        }
    )


@pytest.mark.anyio
async def test_an_all_skip_brief_run_is_excluded_and_fails_the_gate() -> None:
    """Code-review FIX 5: a Skipped case must never score a free pass.

    Before the fix, ``BriefRubricJudge`` returned ``passed=True`` for both
    judge assertions on a Skipped case, and ``_gate_summary`` counted that as
    a scored pass — so an all-Skip run (reachable simply by updating the seed
    set's ``prior_brief`` to match freshly re-recorded fixtures) printed
    ``4/4 (100.0%)``, gate met, exit 0, with zero Briefs written and zero
    rubric items ever scored. After the fix a Skipped case is excluded from
    the denominator entirely, so an all-Skip run has ``total == 0`` and
    ``meets_gate`` is ``False`` — "no scoreable cases", never a clean pass.
    """
    dataset = load_brief_seed_set()
    for case in dataset.cases:
        case.inputs = await _all_skip_inputs(case.inputs)

    stub = build_brief_smoke_model()
    report = await dataset.evaluate(
        build_brief_generation_task(stub, stub), progress=False
    )
    assert not report.failures
    assert len(report.cases) == len(dataset.cases)
    for case in report.cases:
        assert isinstance(case.output.result, SkippedNote), (
            f"{case.name}: expected every case to Skip, got a published Brief"
        )

    gate = _gate_summary(
        report,
        judged=False,
        hard_floor_evaluators=BRIEF_HARD_FLOOR_EVALUATORS,
        assertions_map=harness_generation.BRIEF_EVALUATOR_ASSERTIONS,
        skip=lambda c: isinstance(c.output.result, SkippedNote),
    )
    assert gate.total == 0
    assert gate.passed == 0
    assert set(gate.skipped) == {case.name for case in report.cases}
    assert gate.meets_gate is False

    rendered = _render_gate_summary("all-skip-probe", gate)
    assert "skipped: 4" in rendered
    assert "pass rate: 0/0" in rendered


def test_gate_summary_skip_is_a_no_op_for_every_other_kind() -> None:
    """``skip=None`` (the default, used by every non-brief CLI mode) must
    preserve the exact previous behaviour: every case becomes a row."""
    summary = GateSummary(rows=(), judged=True, safety_failures=())
    assert summary.skipped == ()
