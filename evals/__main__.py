"""Eval harness CLI: ``uv run python -m evals`` (or ``just evals``).

Four modes:

- **seed set** (default) — runs ``evals/seed_set.yaml`` through the outline and
  lesson agents against one or more model bindings, scores each case with the
  Layer 1 deterministic pre-filters and (unless disabled) the Layer 2 binary
  judge, and prints a pydantic-evals report table plus a gate summary per
  binding.
- **flashcard drafting** (``--flashcards``) — runs ``evals/
  flashcard_seed_set.yaml`` through the outline, lesson, and flashcard agents
  in sequence (a card is drafted from a freshly generated lesson, TDD D14/§10),
  scored the same two-layer way against the ``flashcard_draft`` rubric
  (``evals/rubric.py``). One binding, not a sweep — see ``--flashcards``' help.
- **brief research/writing** (``--briefs``, Phase 6 TDD §10) — runs ``evals/
  brief_seed_set.yaml`` through the researcher and analyst agents, replaying a
  recorded ``evals/fixtures/retrieval/*.yaml`` fixture instead of a live
  retrieval call (never Exa, live or ``--smoke``), scored against the
  ``brief`` rubric. One binding, not a sweep — see ``--briefs``' help.
- **calibration** (``--agreement``) — runs the judge over
  ``evals/human_labels.yaml`` and reports judge↔human agreement. No generation
  happens; this mode measures the measuring instrument.

See docs/evals.md for the strategy and docs/ci.md for the GitHub Actions wiring.

Exit codes:
    0  ran; every hard floor held and the ≥ 90% pass-rate gate was met
    1  a case failed a hard floor (branch / outline caps / lesson bands / a
       flashcard's structural or non-triviality check / a brief's provenance
       or novelty-gate-branch check / the safety rubric item), the judged pass
       rate fell below the gate, judge↔human agreement fell below the trust
       threshold, or a case errored outright
    2  misconfiguration (no OPENROUTER_API_KEY and not --smoke; --models
       combined with --smoke, --agreement, --flashcards, or --briefs;
       --agreement combined with --no-judge, --flashcards, or --briefs;
       --flashcards combined with --briefs; bad arguments; a ``seed_set.yaml``
       / ``flashcard_seed_set.yaml`` / ``brief_seed_set.yaml`` /
       ``human_labels.yaml`` that does not parse or validate — a broken data
       file says nothing about the models under evaluation, so it must not be
       reported as a failed gate)

Reads ``OPENROUTER_API_KEY`` and the ``MODEL_OUTLINE`` / ``MODEL_LESSON`` /
``MODEL_JUDGE`` / ``MODEL_FLASHCARD`` / ``MODEL_RESEARCH`` / ``MODEL_BRIEF``
slots via ``aleph.config.settings`` (environment or ``.env``) — imported
lazily, so ``--smoke`` needs no configuration at all. ``MODEL_JUDGE`` is read
*here and nowhere else in the repo*: the judge is eval-only and never touches
the request path. ``--briefs`` never reads ``EXA_API_KEY`` at all — retrieval
is always a fixture replay, live and ``--smoke`` alike (see
``_resolve_brief_binding``). When ``$GITHUB_STEP_SUMMARY`` is set (GitHub
Actions), the same tables are appended there as the job summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from evals.agreement import (
    AGREEMENT_TRUST_THRESHOLD,
    HUMAN_LABELS_PATH,
    load_human_labels,
    render_agreement,
    run_agreement,
)
from evals.generation import (
    BRIEF_EVALUATOR_ASSERTIONS,
    BRIEF_HARD_FLOOR_EVALUATORS,
    BRIEF_JUDGE_ASSERTIONS,
    BRIEF_JUDGE_HARD_FLOOR,
    BRIEF_SEED_SET_PATH,
    EVALUATOR_ASSERTIONS,
    FLASHCARD_EVALUATOR_ASSERTIONS,
    FLASHCARD_HARD_FLOOR_EVALUATORS,
    FLASHCARD_JUDGE_ASSERTIONS,
    FLASHCARD_JUDGE_HARD_FLOOR,
    FLASHCARD_SEED_SET_PATH,
    FULL_PATH_LESSONS,
    HARD_FLOOR_EVALUATORS,
    JUDGE_ASSERTIONS,
    JUDGE_BRIEF_SAFETY,
    JUDGE_FLASHCARD_SAFETY,
    JUDGE_HARD_FLOOR,
    JUDGE_SAFETY,
    SEED_SET_PASS_RATE_GATE,
    SEED_SET_PATH,
    BriefRubricJudge,
    FlashcardRubricJudge,
    RubricJudge,
    build_brief_generation_task,
    build_brief_smoke_model,
    build_flashcard_generation_task,
    build_generation_task,
    load_brief_seed_set,
    load_flashcard_seed_set,
    load_seed_set,
    smoke_model,
)
from evals.judge import Judge, build_stub_judge

if TYPE_CHECKING:
    from collections.abc import Collection

    from pydantic_ai.models import Model
    from pydantic_evals.reporting import EvaluationReport, ReportCase

    from evals.agreement import AgreementSummary
    from evals.generation import (
        BriefSample,
        BriefSeedInputs,
        FlashcardSample,
        FlashcardSeedInputs,
        GenerationSample,
        SeedInputs,
        SeedMeta,
    )

    # The concrete report a seed-set run produces. Spelled out rather than left
    # as ``Any`` so the payload builder is type-checked against the sample
    # (``case.output.lesson_slot``) instead of guessing at run time.
    SeedReport = EvaluationReport[SeedInputs, GenerationSample, SeedMeta]
    FlashcardReport = EvaluationReport[FlashcardSeedInputs, FlashcardSample, SeedMeta]
    BriefReport = EvaluationReport[BriefSeedInputs, BriefSample, SeedMeta]
    #: Both report shapes share ``SeedMeta``, and every gate-arithmetic helper
    #: below only ever reads ``case.name``/``case.assertions``/
    #: ``case.evaluator_failures``/``report.failures``/``report.cases`` — none of
    #: which depend on the inputs/output type parameters — so the shared gate
    #: machinery is typed once, generically, rather than duplicated per report
    #: shape.
    AnyEvalReport = EvaluationReport[Any, Any, SeedMeta]
    AnyEvalCase = ReportCase[Any, Any, SeedMeta]

# Wide enough that the report table never wraps mid-cell in CI logs.
_REPORT_WIDTH = 140


@dataclass(frozen=True)
class ModelBinding:
    """One evaluated configuration: a model in each agent slot, plus the judge.

    The generation slots are separate in production (TDD §5.3: ``MODEL_OUTLINE``
    is the once-per-path, unrecoverable call; ``MODEL_LESSON`` is the
    high-volume one that may step *down*), so the harness keeps them separate
    too rather than pretending a run exercises a single model.

    ``judge`` is ``None`` for a Layer-1-only run. It deliberately does **not**
    vary across a ``--models`` sweep: the judge is the measuring instrument, so
    holding it fixed at ``MODEL_JUDGE`` is what makes two swept models
    comparable at all.
    """

    label: str
    outline: Model
    lesson: Model
    judge: Judge | None = None
    # Smoke only: force the expected branch via the stub's sentinel.
    force_expected_branch: bool = False


def _judging_enabled(args: argparse.Namespace) -> bool:
    """Whether Layer 2 runs, given the flags.

    Default **on** for a live run — the judge is the point of the harness, and a
    silently Layer-1-only run would report a green gate that never looked at
    quality — and **off** under ``--smoke``, which is a plumbing check that
    should stay fast and has no real content for a judge to have an opinion
    about. ``--judge`` / ``--no-judge`` override either way; ``--smoke --judge``
    attaches the deterministic stub judge, which is how the whole Layer 2 path
    stays exercisable with no key.
    """
    if args.judge is not None:
        return bool(args.judge)
    return not args.smoke


def _live_judge(model_id: str) -> Judge:
    """A judge bound to ``model_id`` through the app's own resolution seam."""
    from aleph.services.openrouter import resolve_model

    return Judge(model=resolve_model(model_id), label=model_id)


def _missing_key_message() -> None:
    print(
        "OPENROUTER_API_KEY is not set. Eval runs call the live provider; "
        "set the key (env or .env), or use --smoke for an offline "
        "plumbing check.",
        file=sys.stderr,
    )


def _resolve_bindings(args: argparse.Namespace) -> list[ModelBinding] | None:
    """The model bindings to evaluate, or None on misconfiguration."""
    judging = _judging_enabled(args)

    if args.smoke:
        stub = smoke_model()
        return [
            ModelBinding(
                label="smoke",
                outline=stub,
                lesson=stub,
                judge=build_stub_judge() if judging else None,
                force_expected_branch=True,
            )
        ]

    from aleph.config import settings

    if not settings.openrouter_api_key:
        _missing_key_message()
        return None

    from aleph.services.openrouter import resolve_model

    judge = _live_judge(settings.model_judge) if judging else None

    sweep = [
        model_id.strip()
        for model_id in (args.models or "").split(",")
        if model_id.strip()
    ]
    if not sweep:
        # Default: the configured slots, exactly as the service would bind them.
        outline_id, lesson_id = settings.model_outline, settings.model_lesson
        label = (
            outline_id
            if outline_id == lesson_id
            else f"outline={outline_id} lesson={lesson_id}"
        )
        return [
            ModelBinding(
                label=label,
                outline=resolve_model(outline_id),
                lesson=resolve_model(lesson_id),
                judge=judge,
            )
        ]
    # A sweep entry binds the same id to both slots: the comparison that matters
    # for the allowlist is "how does this model do at the whole job".
    return [
        ModelBinding(
            label=model_id,
            outline=resolve_model(model_id),
            lesson=resolve_model(model_id),
            judge=judge,
        )
        for model_id in sweep
    ]


@dataclass(frozen=True)
class FlashcardModelBinding:
    """One evaluated configuration for the ``flashcard_draft`` harness.

    A single binding, not a sweep list like :class:`ModelBinding`: ``--models``
    is rejected alongside ``--flashcards`` (``main``) because the flashcard
    harness's whole point is scoring *drafting* quality against the configured
    ``model_flashcard`` slot, and a sweep of the outline/lesson models would be
    answering a question this mode does not ask.
    """

    label: str
    outline: Model
    lesson: Model
    flashcard: Model
    judge: Judge | None = None


def _resolve_flashcard_binding(
    args: argparse.Namespace,
) -> FlashcardModelBinding | None:
    """The flashcard model binding to evaluate, or None on misconfiguration."""
    judging = _judging_enabled(args)

    if args.smoke:
        stub = smoke_model()
        return FlashcardModelBinding(
            label="smoke",
            outline=stub,
            lesson=stub,
            flashcard=stub,
            judge=build_stub_judge() if judging else None,
        )

    from aleph.config import settings

    if not settings.openrouter_api_key:
        _missing_key_message()
        return None

    from aleph.services.openrouter import resolve_model

    judge = _live_judge(settings.model_judge) if judging else None
    return FlashcardModelBinding(
        label=settings.model_flashcard,
        outline=resolve_model(settings.model_outline),
        lesson=resolve_model(settings.model_lesson),
        flashcard=resolve_model(settings.model_flashcard),
        judge=judge,
    )


@dataclass(frozen=True)
class BriefModelBinding:
    """One evaluated configuration for the ``brief`` harness (Phase 6 TDD §10).

    A single binding, not a sweep, mirroring :class:`FlashcardModelBinding` —
    ``--models`` is rejected alongside ``--briefs`` (``main``) for the same
    reason: this mode scores the configured ``model_research``/``model_brief``
    slots' quality, not a swept model's.
    """

    label: str
    researcher: Model
    brief: Model
    judge: Judge | None = None


def _resolve_brief_binding(args: argparse.Namespace) -> BriefModelBinding | None:
    """The brief model binding to evaluate, or None on misconfiguration.

    **Never reads ``EXA_API_KEY``.** The `brief` harness always replays a
    recorded fixture through `FixtureRetriever` — live and ``--smoke`` alike —
    so retrieval itself needs no key at all; only the researcher/analyst model
    calls do (mirroring the flashcard harness's own `OPENROUTER_API_KEY`-only
    posture).
    """
    judging = _judging_enabled(args)

    if args.smoke:
        stub = build_brief_smoke_model()
        return BriefModelBinding(
            label="smoke",
            researcher=stub,
            brief=stub,
            judge=build_stub_judge() if judging else None,
        )

    from aleph.config import settings

    if not settings.openrouter_api_key:
        _missing_key_message()
        return None

    from aleph.services.openrouter import resolve_model

    judge = _live_judge(settings.model_judge) if judging else None
    return BriefModelBinding(
        label=(
            settings.model_research
            if settings.model_research == settings.model_brief
            else f"research={settings.model_research} brief={settings.model_brief}"
        ),
        researcher=resolve_model(settings.model_research),
        brief=resolve_model(settings.model_brief),
        judge=judge,
    )


def _case_payload(report: SeedReport) -> dict[str, Any]:
    """JSON-friendly per-case results for the --report artifact."""
    return {
        "cases": [
            {
                "name": case.name,
                # "<unit title> / <lesson title>" of the probe lesson, so a
                # report can be read against the outline it came from; None for
                # a refusal case, which has no probe lesson.
                "lesson_slot": case.output.lesson_slot,
                # Every lesson the case generated, in path order: one for an
                # ordinary case, several for a full-path one.
                "lessons": [lesson.slot for lesson in case.output.lessons],
                "assertions": {
                    name: {"value": result.value, "reason": result.reason}
                    for name, result in case.assertions.items()
                },
                # A crashed evaluator produces *no* assertion, so without this
                # the artifact would render an unscored case as a clean pass.
                "evaluator_failures": [
                    {"name": failure.name, "error": failure.error_message}
                    for failure in case.evaluator_failures
                ],
                "metrics": dict(case.metrics),
                "task_duration": case.task_duration,
            }
            for case in report.cases
        ],
        "errors": [failure.name for failure in report.failures],
    }


def _flashcard_case_payload(report: FlashcardReport) -> dict[str, Any]:
    """JSON-friendly per-case results for the --report artifact (flashcard mode).

    Mirrors :func:`_case_payload`'s shape; ``cards`` replaces ``lessons`` since a
    flashcard case's output is a drafted card set, not a generated lesson.
    """
    return {
        "cases": [
            {
                "name": case.name,
                "lesson_slot": f"{case.output.unit_title} / {case.output.lesson_title}",
                "cards": [
                    {"front": card.front, "back": card.back}
                    for card in case.output.drafts.cards
                ],
                "assertions": {
                    name: {"value": result.value, "reason": result.reason}
                    for name, result in case.assertions.items()
                },
                "evaluator_failures": [
                    {"name": failure.name, "error": failure.error_message}
                    for failure in case.evaluator_failures
                ],
                "metrics": dict(case.metrics),
                "task_duration": case.task_duration,
            }
            for case in report.cases
        ],
        "errors": [failure.name for failure in report.failures],
    }


def _brief_case_payload(report: BriefReport) -> dict[str, Any]:
    """JSON-friendly per-case results for the --report artifact (brief mode).

    Mirrors :func:`_flashcard_case_payload`'s shape. A case's ``result`` is
    either a published Brief (``BriefBody``) or a Skipped run
    (``SkippedNote``, ``BriefNoveltyGate`` gates that branch) — both reported,
    distinguished by ``kind``, rather than only ever assuming a Brief was
    written.
    """
    from aleph.agents.analyst import BriefBody

    return {
        "cases": [
            {
                "name": case.name,
                "findings": len(case.output.findings),
                "survivors": len(case.output.survivors),
                "result": (
                    {
                        "kind": "published",
                        "title": case.output.result.title,
                        "cited_urls": case.output.result.cited_urls,
                    }
                    if isinstance(case.output.result, BriefBody)
                    else {"kind": "skipped", "detail": case.output.result.detail}
                ),
                "assertions": {
                    name: {"value": result.value, "reason": result.reason}
                    for name, result in case.assertions.items()
                },
                "evaluator_failures": [
                    {"name": failure.name, "error": failure.error_message}
                    for failure in case.evaluator_failures
                ],
                "metrics": dict(case.metrics),
                "task_duration": case.task_duration,
            }
            for case in report.cases
        ],
        "errors": [failure.name for failure in report.failures],
    }


#: What happened to one gating check on one case. ``missing`` and ``errored``
#: exist separately from ``fail`` because they are *harness* problems rather
#: than quality ones, and they read very differently in a report — but all three
#: are non-passes: an unscored case is never a pass.
CheckState = Literal["pass", "fail", "missing", "errored"]


@dataclass(frozen=True)
class CheckOutcome:
    """One named check on one case: what happened, and why."""

    name: str
    state: CheckState
    detail: str

    @property
    def ok(self) -> bool:
        return self.state == "pass"

    def label(self) -> str:
        """How the gate table names it: bare for a fail, tagged otherwise."""
        if self.state == "fail":
            return self.name
        if self.state == "errored":
            return f"{self.name} (evaluator errored)"
        return f"{self.name} ({self.state})"


def _case_checks(
    case: AnyEvalCase,
    gating: Collection[str],
    *,
    assertions_map: dict[str, frozenset[str]] = EVALUATOR_ASSERTIONS,
) -> tuple[CheckOutcome, ...]:
    """Resolve every ``gating`` assertion on one case to a single outcome.

    The one walk both report views are derived from (:func:`_hard_floor_failures`
    and :func:`_gate_summary`), because the two used to answer the same question
    twice and could disagree about it.

    Three ways a check can be a non-pass, all of which must exit 1:

    1. the assertion is present and false — an ordinary **fail**;
    2. the evaluator that owns the assertion raised, so pydantic-evals kept the
       case in ``report.cases`` and simply omitted the assertion — **errored**.
       A crashing ``RefusalBranch`` would otherwise leave the safety check
       silently unrun and the run green;
    3. the assertion is absent with no crash to explain it — **missing** (an
       evaluator dropped from the dataset, a rename that broke registration).
       The CLI keys its exit code on these names, so an absent name is a harness
       bug, not an implicit pass.

    Which assertions a crashed evaluator owns comes from
    :data:`~evals.generation.EVALUATOR_ASSERTIONS`, **explicitly**, not from the
    coincidence that a Layer 1 evaluator's class name equals its assertion name:
    ``RubricJudge`` emits three assertions under none of its own name, so
    matching on names alone reported a crashed judge as three assertions
    "missing from the report (evaluator not registered?)" — a false and
    misleading diagnosis of a registered evaluator that blew up.

    ``gating`` is a set of *assertion* names for the same reason. A crashed
    evaluator that owns no gating assertion (a soft check such as
    ``MaxDuration``) is still reported, under its own name: a check that did not
    run is never evidence that it would have passed.
    """
    crashed: dict[str, str] = {}
    unowned: list[CheckOutcome] = []
    for failure in case.evaluator_failures:
        owned = assertions_map.get(failure.name, frozenset({failure.name}))
        crashed.update(dict.fromkeys(owned, failure.error_message))
        if not owned & set(gating):
            unowned.append(
                CheckOutcome(
                    name=failure.name,
                    state="errored",
                    detail=f"evaluator errored ({failure.error_message})",
                )
            )

    checks: list[CheckOutcome] = []
    for name in sorted(gating):
        result = case.assertions.get(name)
        state: CheckState
        if result is not None:
            state = "pass" if result.value else "fail"
            detail = result.reason or "assertion is false"
        elif name in crashed:
            state = "errored"
            detail = f"evaluator errored ({crashed[name]})"
        else:
            state = "missing"
            detail = "assertion missing from the report (evaluator not registered?)"
        checks.append(CheckOutcome(name=name, state=state, detail=detail))
    return tuple(checks + unowned)


def _hard_floor_failures(
    report: AnyEvalReport,
    hard_floor: Collection[str] = HARD_FLOOR_EVALUATORS,
    *,
    assertions_map: dict[str, frozenset[str]] = EVALUATOR_ASSERTIONS,
) -> list[str]:
    """``case/check: reason`` strings for every hard-floor violation or error.

    The task erroring outright (``report.failures``) is a fourth way to fail the
    floor on top of the three :func:`_case_checks` classifies: there is no
    generation at all, so there is nothing to score.

    ``hard_floor`` is a set of assertion names because Layer 2 emits three named
    assertions from a single evaluator. Callers union
    :data:`~evals.generation.JUDGE_HARD_FLOOR` in when judging, so a missing or
    crashed ``JudgeSafety`` verdict fails exactly as a missing ``RefusalBranch``
    does. Layer 2's other two assertions are deliberately *not* hard floors —
    they feed the ≥ 90% rate gate instead (:func:`_gate_summary`).
    """
    return [f"{failure.name}: errored" for failure in report.failures] + [
        f"{case.name}/{check.name}: {check.detail}"
        for case in report.cases
        for check in _case_checks(case, hard_floor, assertions_map=assertions_map)
        if not check.ok
    ]


# --- PRD §9's ship gate --------------------------------------------------------


@dataclass(frozen=True)
class CaseGateRow:
    """One row of the printed gate table."""

    name: str
    passed: bool
    failed_checks: tuple[str, ...]


@dataclass(frozen=True)
class GateSummary:
    """PRD §9's ship gate, computed over one binding's report.

    A case counts as a **pass** when every *gating* assertion it carries is true
    and nothing about it went unscored. Gating assertions are the Layer 1 hard
    floors plus, when Layer 2 ran, the three judge assertions. Soft checks
    (``MaxDuration``) are excluded on purpose: a slow case on a noisy shared CI
    runner is not a quality failure, and letting it move the pass rate would
    quietly turn the ship gate into a latency measurement.
    """

    rows: tuple[CaseGateRow, ...]
    judged: bool
    safety_failures: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def passed(self) -> int:
        return sum(1 for row in self.rows if row.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def meets_gate(self) -> bool:
        """The ≥ 90% pass rate **and** no safety failure (PRD §9).

        The two are separate conditions, not one: 19 of 20 cases passing is a
        95% rate, and if the one failure is a safety-item failure the run still
        must not ship. "Any safety failure is a hard block regardless of the
        aggregate rate" is exactly what the ``and not self.safety_failures``
        clause encodes.
        """
        return (
            self.total > 0
            and self.pass_rate >= SEED_SET_PASS_RATE_GATE
            and not self.safety_failures
        )


def _gate_summary(
    report: AnyEvalReport,
    *,
    judged: bool,
    hard_floor_evaluators: Collection[str] = HARD_FLOOR_EVALUATORS,
    judge_assertions: Collection[str] = JUDGE_ASSERTIONS,
    assertions_map: dict[str, frozenset[str]] = EVALUATOR_ASSERTIONS,
    safety_assertion: str = JUDGE_SAFETY,
) -> GateSummary:
    """Compute the gate figures for one binding's report.

    ``hard_floor_evaluators``/``judge_assertions``/``assertions_map``/
    ``safety_assertion`` default to the outline/lesson seed set's names; the
    flashcard CLI mode passes the ``FLASHCARD_*`` equivalents
    (``evals/generation.py``) so this one function computes both kinds' gates
    rather than each kind carrying its own copy.
    """
    gating: set[str] = set(hard_floor_evaluators)
    if judged:
        gating |= set(judge_assertions)

    rows: list[CaseGateRow] = []
    safety_failures: list[str] = []

    # A task that errored produces no case at all; it is a failure, and leaving
    # it out of the denominator would inflate the rate of a run that half died.
    for failure in report.failures:
        rows.append(
            CaseGateRow(name=failure.name, passed=False, failed_checks=("errored",))
        )

    for case in report.cases:
        checks = _case_checks(case, gating, assertions_map=assertions_map)
        failed_checks = [check.label() for check in checks if not check.ok]
        # Only a real verdict of "unsafe" is a safety *failure*; a safety check
        # that never ran fails the case (above) and the hard floor, but claiming
        # the judge found something unsafe would be a different, false, report.
        safety_failures.extend(
            f"{case.name}: {check.detail}"
            for check in checks
            if check.name == safety_assertion and check.state == "fail"
        )
        rows.append(
            CaseGateRow(
                name=case.name,
                passed=not failed_checks,
                failed_checks=tuple(failed_checks),
            )
        )

    return GateSummary(
        rows=tuple(rows), judged=judged, safety_failures=tuple(safety_failures)
    )


def _render_gate_summary(label: str, summary: GateSummary) -> str:
    """The printed gate block: per-case table, pass rate, safety failures."""
    layer = "Layer 1 + Layer 2 (judge)" if summary.judged else "Layer 1 only (no judge)"
    lines = [
        f"Gate summary — {label} [{layer}]",
        "",
        f"{'case':<44} {'result':<7} failed checks",
        "-" * 100,
    ]
    for row in summary.rows:
        lines.append(
            f"{row.name:<44} {'PASS' if row.passed else 'FAIL':<7} "
            f"{', '.join(row.failed_checks)}"
        )
    lines.append("-" * 100)
    lines.append(
        f"pass rate: {summary.passed}/{summary.total} ({summary.pass_rate:.1%}); "
        f"gate {SEED_SET_PASS_RATE_GATE:.0%}"
    )
    if summary.safety_failures:
        lines.append(f"SAFETY FAILURES (hard block, {len(summary.safety_failures)}):")
        lines.extend(f"  - {failure}" for failure in summary.safety_failures)
    else:
        lines.append("safety failures: none")
    if not summary.judged:
        lines.append(
            "NOTE: the judge did not run, so this rate reflects the Layer 1 "
            "structural floor only — it is not the PRD §9 quality gate."
        )
    return "\n".join(lines)


def _gate_payload(summary: GateSummary) -> dict[str, Any]:
    """JSON-friendly gate figures for the --report artifact."""
    return {
        "judged": summary.judged,
        "total": summary.total,
        "passed": summary.passed,
        "pass_rate": summary.pass_rate,
        "gate": SEED_SET_PASS_RATE_GATE,
        "meets_gate": summary.meets_gate,
        "safety_failures": list(summary.safety_failures),
        "failed_cases": {
            row.name: list(row.failed_checks) for row in summary.rows if not row.passed
        },
    }


def _append_step_summary(label: str, report: AnyEvalReport, gate: GateSummary) -> None:
    """Mirror the report table and the gate block into the Actions job summary."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write(
            f"## Seed-set evals — {label}\n\n"
            f"```\n{report.render(width=_REPORT_WIDTH, include_reasons=True)}```\n\n"
            f"```\n{_render_gate_summary(label, gate)}\n```\n\n"
        )


def _append_agreement_step_summary(text: str) -> None:
    """Mirror the calibration report into the Actions job summary."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write(f"## Judge↔human agreement\n\n```\n{text}\n```\n\n")


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


# --- modes ---------------------------------------------------------------------


def _agreement_payload(
    judge_label: str, summary: AgreementSummary, *, smoke: bool, all_samples: bool
) -> dict[str, Any]:
    """JSON-friendly calibration figures for the --report artifact.

    ``total``/``agreed``/``rate`` cover **every** label; ``gated`` covers the
    builder-recorded ones and is the only block a threshold is applied to (see
    :attr:`~evals.agreement.AgreementSummary.real`). Both are reported so a
    consumer of the artifact cannot mistake the mixture for the measurement.
    """
    gated = summary.real
    return {
        "agreement": {
            "judge": judge_label,
            "smoke": smoke,
            "all_samples": all_samples,
            "total": summary.total,
            "agreed": summary.agreed,
            "rate": summary.rate,
            "threshold": AGREEMENT_TRUST_THRESHOLD,
            "gated": {
                "total": gated.total,
                "agreed": gated.agreed,
                "rate": gated.rate,
                "meets_threshold": gated.meets_threshold,
                "item_rate": gated.item_rate,
            },
            "samples": summary.samples.total,
            "item_rate": summary.item_rate,
            "comparisons": [
                {
                    "label_id": comparison.label_id,
                    "sample": comparison.sample,
                    "artifact": comparison.artifact,
                    "human": comparison.human,
                    "judge": comparison.judge,
                    "direction": comparison.direction,
                    "judge_failed_items": list(comparison.judge_failed_items),
                    "item_disagreements": [
                        {"item": item, "human": human, "judge": judged}
                        for item, human, judged in comparison.item_disagreements
                    ],
                }
                for comparison in summary.comparisons
            ],
        }
    }


#: What a data file raises when it does not parse or does not validate.
#:
#: ``ValueError`` rather than ``ValidationError`` because it covers all three
#: shapes: pydantic's ``ValidationError`` *is* a ``ValueError``, and
#: ``Dataset.from_file`` re-raises one as a plain ``ValueError`` with the path
#: attached. ``yaml.YAMLError`` is the syntax half, and is not a ``ValueError``.
_DATA_FILE_ERRORS = (ValueError, yaml.YAMLError)


def _unreadable_data_file(path: Path, error: Exception) -> None:
    """Report a data file the harness cannot parse or validate (exit 2).

    Misconfiguration, not a failed gate: an unparseable ``seed_set.yaml`` or
    ``human_labels.yaml`` says nothing about the models under evaluation, and
    letting the traceback escape would surface it as exit 1 — the code that
    means "a case failed a hard floor". One line, because the fix is always in
    the file the line names.
    """
    print(
        f"{path}: cannot be loaded — {type(error).__name__}: {error}", file=sys.stderr
    )


def _run_agreement_mode(args: argparse.Namespace) -> int:
    """``--agreement``: judge the human-labeled set and report agreement."""
    try:
        labels = load_human_labels()
    except _DATA_FILE_ERRORS as error:
        _unreadable_data_file(HUMAN_LABELS_PATH, error)
        return 2

    if args.smoke:
        judge = build_stub_judge()
    else:
        from aleph.config import settings

        if not settings.openrouter_api_key:
            _missing_key_message()
            return 2
        judge = _live_judge(settings.model_judge)

    summary = asyncio.run(
        run_agreement(judge, labels.labels, apply_smoke_script=args.smoke)
    )
    rendered = render_agreement(summary, judge_label_text=judge.label)
    print(rendered)
    _append_agreement_step_summary(rendered)

    if args.report is not None:
        _write_report(
            args.report,
            _agreement_payload(
                judge.label, summary, smoke=args.smoke, all_samples=labels.all_samples
            ),
        )

    # Neither of the next two situations is a calibration measurement, so
    # neither may pass or fail a build on the strength of its number.
    if args.smoke:
        print(
            "\n--smoke: the stub judge's verdicts are scripted per label "
            "(`smoke.judge_fails`), so this exercises the agreement machinery "
            "end to end and measures nothing about a real judge. Run without "
            "--smoke, against MODEL_JUDGE, for the real figure."
        )
        return 0
    if labels.all_samples:
        print(
            "\nWARNING: every label in evals/human_labels.yaml is marked "
            "`sample: true`. Those are illustrative, not builder-recorded, so "
            "the rate above is not a calibration measurement and is not gated "
            "on. Record real labels (PRD §9 asks for ~30-50) and clear the "
            "`sample` flag to turn the threshold on.",
            file=sys.stderr,
        )
        return 0

    # The gate is the builder-recorded labels alone. While the file is mixed —
    # its expected state as real labels land one at a time — averaging the
    # samples in would let them dilute the very figure they are excluded from
    # being evidence for.
    gated = summary.real
    if not gated.meets_threshold:
        print(
            f"\nJUDGE↔HUMAN AGREEMENT BELOW THRESHOLD: {gated.rate:.1%} < "
            f"{AGREEMENT_TRUST_THRESHOLD:.0%} over {gated.total} "
            f"builder-recorded label(s) ({summary.samples.total} sample(s) "
            "excluded). The judge is not a trusted gate at this level — fix the "
            "judge prompt or the judge model and re-measure before believing a "
            "seed-set result.",
            file=sys.stderr,
        )
        return 1
    return 0


def _run_seed_set_mode(args: argparse.Namespace) -> int:
    """The default mode: generate the seed set and gate on the results."""
    bindings = _resolve_bindings(args)
    if bindings is None:
        return 2

    results: dict[str, Any] = {}
    hard_failures: dict[str, list[str]] = {}
    gate_failures: list[str] = []

    for binding in bindings:
        # Loaded per binding: `add_evaluator` mutates the dataset, and a shared
        # one would accumulate a judge per swept model.
        try:
            dataset = load_seed_set()
        except _DATA_FILE_ERRORS as error:
            _unreadable_data_file(SEED_SET_PATH, error)
            return 2
        judged = binding.judge is not None
        if binding.judge is not None:
            dataset.add_evaluator(RubricJudge(judge=binding.judge))

        report = asyncio.run(
            dataset.evaluate(
                build_generation_task(
                    binding.outline,
                    binding.lesson,
                    force_expected_branch=binding.force_expected_branch,
                    full_path_lessons=args.full_path_lessons,
                ),
                name=f"seed-set ({binding.label})",
                max_concurrency=args.max_concurrency,
            )
        )
        report.print(width=_REPORT_WIDTH, include_reasons=True)

        gate = _gate_summary(report, judged=judged)
        print()
        print(_render_gate_summary(binding.label, gate))
        _append_step_summary(binding.label, report, gate)

        payload = _case_payload(report)
        payload["gate"] = _gate_payload(gate)
        payload["judge"] = binding.judge.label if binding.judge else None
        results[binding.label] = payload

        hard_floor = set(HARD_FLOOR_EVALUATORS)
        if judged:
            hard_floor |= JUDGE_HARD_FLOOR
        failed_cases = _hard_floor_failures(report, hard_floor)
        if failed_cases:
            hard_failures[binding.label] = failed_cases
        # The rate gate is a *quality* gate and only means anything once the
        # judge has run: Layer 1 alone is already pass/fail at 100%, so applying
        # a 90% threshold to it would license one broken case per ten.
        if judged and not gate.meets_gate:
            gate_failures.append(
                f"{binding.label}: pass rate {gate.pass_rate:.1%} "
                f"(gate {SEED_SET_PASS_RATE_GATE:.0%}), "
                f"{len(gate.safety_failures)} safety failure(s)"
            )

    if args.report is not None:
        _write_report(args.report, {"seed_set": results})

    if hard_failures:
        for label, failures in hard_failures.items():
            print(f"HARD FLOOR FAILED [{label}]:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
    if gate_failures:
        print("SHIP GATE FAILED:", file=sys.stderr)
        for failure in gate_failures:
            print(f"  - {failure}", file=sys.stderr)
    return 1 if (hard_failures or gate_failures) else 0


def _run_flashcard_mode(args: argparse.Namespace) -> int:
    """``--flashcards``: run the ``flashcard_draft`` seed set and gate on it.

    The third eval kind's own mode (TDD D14/§10) — mirrors
    :func:`_run_seed_set_mode` structurally, but over one binding rather than a
    sweep (``--models`` is rejected alongside ``--flashcards``, ``main`` below)
    and reusing the same generic gate machinery (:func:`_gate_summary`,
    :func:`_hard_floor_failures`) with the flashcard evaluator names.
    """
    binding = _resolve_flashcard_binding(args)
    if binding is None:
        return 2

    try:
        dataset = load_flashcard_seed_set()
    except _DATA_FILE_ERRORS as error:
        _unreadable_data_file(FLASHCARD_SEED_SET_PATH, error)
        return 2

    judged = binding.judge is not None
    if binding.judge is not None:
        dataset.add_evaluator(FlashcardRubricJudge(judge=binding.judge))

    report = asyncio.run(
        dataset.evaluate(
            build_flashcard_generation_task(
                binding.outline, binding.lesson, binding.flashcard
            ),
            name=f"flashcard-seed-set ({binding.label})",
            max_concurrency=args.max_concurrency,
        )
    )
    report.print(width=_REPORT_WIDTH, include_reasons=True)

    gate = _gate_summary(
        report,
        judged=judged,
        hard_floor_evaluators=FLASHCARD_HARD_FLOOR_EVALUATORS,
        judge_assertions=FLASHCARD_JUDGE_ASSERTIONS,
        assertions_map=FLASHCARD_EVALUATOR_ASSERTIONS,
        safety_assertion=JUDGE_FLASHCARD_SAFETY,
    )
    print()
    print(_render_gate_summary(binding.label, gate))
    _append_step_summary(f"flashcard_draft — {binding.label}", report, gate)

    if args.report is not None:
        payload = _flashcard_case_payload(report)
        payload["gate"] = _gate_payload(gate)
        payload["judge"] = binding.judge.label if binding.judge else None
        _write_report(args.report, {"flashcard_seed_set": {binding.label: payload}})

    hard_floor = set(FLASHCARD_HARD_FLOOR_EVALUATORS)
    if judged:
        hard_floor |= FLASHCARD_JUDGE_HARD_FLOOR
    failed_cases = _hard_floor_failures(
        report, hard_floor, assertions_map=FLASHCARD_EVALUATOR_ASSERTIONS
    )
    if failed_cases:
        print(f"HARD FLOOR FAILED [{binding.label}]:", file=sys.stderr)
        for failure in failed_cases:
            print(f"  - {failure}", file=sys.stderr)

    # Same rule as the outline/lesson gate: the rate only means anything once
    # the judge has run.
    gate_failed = judged and not gate.meets_gate
    if gate_failed:
        print("SHIP GATE FAILED:", file=sys.stderr)
        print(
            f"  - {binding.label}: pass rate {gate.pass_rate:.1%} "
            f"(gate {SEED_SET_PASS_RATE_GATE:.0%}), "
            f"{len(gate.safety_failures)} safety failure(s)",
            file=sys.stderr,
        )
    return 1 if (failed_cases or gate_failed) else 0


def _run_brief_mode(args: argparse.Namespace) -> int:
    """``--briefs``: run the ``brief`` seed set and gate on it (Phase 6 TDD §10).

    The fourth eval kind's own mode — mirrors :func:`_run_flashcard_mode`
    structurally: one binding rather than a sweep (``--models`` is rejected
    alongside ``--briefs``, ``main`` below), and the same generic gate
    machinery (:func:`_gate_summary`, :func:`_hard_floor_failures`) with the
    brief evaluator names. Retrieval is always a recorded fixture replay
    (`FixtureRetriever`) — this mode never touches Exa, live or ``--smoke``.
    """
    binding = _resolve_brief_binding(args)
    if binding is None:
        return 2

    try:
        dataset = load_brief_seed_set()
    except _DATA_FILE_ERRORS as error:
        _unreadable_data_file(BRIEF_SEED_SET_PATH, error)
        return 2

    judged = binding.judge is not None
    if binding.judge is not None:
        dataset.add_evaluator(BriefRubricJudge(judge=binding.judge))

    report = asyncio.run(
        dataset.evaluate(
            build_brief_generation_task(binding.researcher, binding.brief),
            name=f"brief-seed-set ({binding.label})",
            max_concurrency=args.max_concurrency,
        )
    )
    report.print(width=_REPORT_WIDTH, include_reasons=True)

    gate = _gate_summary(
        report,
        judged=judged,
        hard_floor_evaluators=BRIEF_HARD_FLOOR_EVALUATORS,
        judge_assertions=BRIEF_JUDGE_ASSERTIONS,
        assertions_map=BRIEF_EVALUATOR_ASSERTIONS,
        safety_assertion=JUDGE_BRIEF_SAFETY,
    )
    print()
    print(_render_gate_summary(binding.label, gate))
    _append_step_summary(f"brief — {binding.label}", report, gate)

    if args.report is not None:
        payload = _brief_case_payload(report)
        payload["gate"] = _gate_payload(gate)
        payload["judge"] = binding.judge.label if binding.judge else None
        _write_report(args.report, {"brief_seed_set": {binding.label: payload}})

    hard_floor = set(BRIEF_HARD_FLOOR_EVALUATORS)
    if judged:
        hard_floor |= BRIEF_JUDGE_HARD_FLOOR
    failed_cases = _hard_floor_failures(
        report, hard_floor, assertions_map=BRIEF_EVALUATOR_ASSERTIONS
    )
    if failed_cases:
        print(f"HARD FLOOR FAILED [{binding.label}]:", file=sys.stderr)
        for failure in failed_cases:
            print(f"  - {failure}", file=sys.stderr)

    # Same rule as the outline/lesson gate: the rate only means anything once
    # the judge has run.
    gate_failed = judged and not gate.meets_gate
    if gate_failed:
        print("SHIP GATE FAILED:", file=sys.stderr)
        print(
            f"  - {binding.label}: pass rate {gate.pass_rate:.1%} "
            f"(gate {SEED_SET_PASS_RATE_GATE:.0%}), "
            f"{len(gate.safety_failures)} safety failure(s)",
            file=sys.stderr,
        )
    return 1 if (failed_cases or gate_failed) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evals", description="Run the Aleph agent eval harness."
    )
    parser.add_argument(
        "--models",
        default="",
        help="comma-separated OpenRouter model ids to sweep; each is bound to "
        "both the outline and lesson slots (default: the configured "
        "MODEL_OUTLINE / MODEL_LESSON). The judge always stays on MODEL_JUDGE.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="offline plumbing check with the deterministic stub model "
        "(no key, no network)",
    )
    parser.add_argument(
        "--judge",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="run the Layer 2 binary judge (MODEL_JUDGE). Default: on for a "
        "live run, off under --smoke, where --judge attaches the deterministic "
        "stub judge instead.",
    )
    parser.add_argument(
        "--agreement",
        action="store_true",
        help="calibration mode: judge evals/human_labels.yaml and report "
        "judge↔human agreement instead of running the seed set",
    )
    parser.add_argument(
        "--flashcards",
        action="store_true",
        help="flashcard_draft mode: run evals/flashcard_seed_set.yaml (draft "
        "cards from a freshly generated lesson per case) instead of the "
        "outline/lesson seed set. The judge stays MODEL_JUDGE, generation "
        "stays the configured MODEL_OUTLINE/MODEL_LESSON/MODEL_FLASHCARD.",
    )
    parser.add_argument(
        "--briefs",
        action="store_true",
        help="brief mode (Phase 6): run evals/brief_seed_set.yaml (research + "
        "write a Brief per case, replaying a recorded evals/fixtures/"
        "retrieval/*.yaml fixture — never a live Exa call) instead of the "
        "outline/lesson seed set. The judge stays MODEL_JUDGE, generation "
        "stays the configured MODEL_RESEARCH/MODEL_BRIEF.",
    )
    parser.add_argument(
        "--full-path-lessons",
        type=int,
        default=FULL_PATH_LESSONS,
        help="lessons generated sequentially for a full_path seed case "
        f"(default: {FULL_PATH_LESSONS})",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="also write the results as JSON to this path",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=4,
        help="concurrent cases per binding (default: 4)",
    )
    args = parser.parse_args(argv)

    if args.smoke and args.models:
        # Silently dropping --models here would read as "swept those models
        # offline", which is exactly the wrong conclusion to hand someone.
        parser.error(
            "--models cannot be combined with --smoke: a smoke run always uses "
            "the deterministic stub model. Drop --smoke to sweep real models."
        )
    if args.agreement and args.models:
        parser.error(
            "--models cannot be combined with --agreement: calibration judges a "
            "fixed set of already-generated artifacts, so there is no "
            "generation model to sweep."
        )
    if args.agreement and args.judge is False:
        parser.error(
            "--no-judge cannot be combined with --agreement: agreement mode "
            "exists to measure the judge."
        )
    if args.full_path_lessons < 1:
        parser.error("--full-path-lessons must be at least 1.")
    if args.flashcards and args.agreement:
        parser.error(
            "--flashcards cannot be combined with --agreement: they are two "
            "different modes, and each already replaces the default seed-set "
            "run on its own."
        )
    if args.flashcards and args.models:
        parser.error(
            "--models cannot be combined with --flashcards: the flashcard "
            "harness always scores drafting quality against the configured "
            "MODEL_OUTLINE/MODEL_LESSON/MODEL_FLASHCARD, not a swept model."
        )
    if args.briefs and args.agreement:
        parser.error(
            "--briefs cannot be combined with --agreement: they are two "
            "different modes, and each already replaces the default seed-set "
            "run on its own."
        )
    if args.briefs and args.models:
        parser.error(
            "--models cannot be combined with --briefs: the brief harness "
            "always scores research/writing quality against the configured "
            "MODEL_RESEARCH/MODEL_BRIEF, not a swept model."
        )
    if args.briefs and args.flashcards:
        parser.error(
            "--briefs cannot be combined with --flashcards: they are two "
            "different modes, and each already replaces the default seed-set "
            "run on its own."
        )

    if args.agreement:
        return _run_agreement_mode(args)
    if args.flashcards:
        return _run_flashcard_mode(args)
    if args.briefs:
        return _run_brief_mode(args)
    return _run_seed_set_mode(args)


if __name__ == "__main__":
    sys.exit(main())
