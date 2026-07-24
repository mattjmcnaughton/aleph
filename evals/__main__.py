"""Eval harness CLI: ``uv run python -m evals`` (or ``just evals``).

Runs the seed set (``evals/seed_set.yaml``) through the outline and lesson
agents against one or more model bindings and prints a pydantic-evals report
table per binding. See docs/evals.md for the strategy and docs/ci.md for the
GitHub Actions wiring.

Exit codes:
    0  ran; every Layer 1 hard-floor assertion passed (soft scores never gate)
    1  a case failed a hard floor (branch / outline caps / lesson bands) or
       errored outright
    2  misconfiguration (no OPENROUTER_API_KEY and not --smoke; --models
       combined with --smoke; bad arguments)

Reads ``OPENROUTER_API_KEY`` and the ``MODEL_OUTLINE`` / ``MODEL_LESSON`` slots
via ``aleph.config.settings`` (environment or ``.env``) — imported lazily, so
``--smoke`` needs no configuration at all. When ``$GITHUB_STEP_SUMMARY`` is set
(GitHub Actions), the same report tables are appended there as the job summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evals.generation import (
    HARD_FLOOR_EVALUATORS,
    build_generation_task,
    load_seed_set,
    smoke_model,
)

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_evals.reporting import EvaluationReport

    from evals.generation import GenerationSample, SeedInputs, SeedMeta

    # The concrete report a seed-set run produces. Spelled out rather than left
    # as ``Any`` so the payload builder is type-checked against the sample
    # (``case.output.lesson_slot``) instead of guessing at run time.
    SeedReport = EvaluationReport[SeedInputs, GenerationSample, SeedMeta]

# Wide enough that the report table never wraps mid-cell in CI logs.
_REPORT_WIDTH = 140


@dataclass(frozen=True)
class ModelBinding:
    """One evaluated configuration: a model in each of the two agent slots.

    The slots are separate in production (TDD §5.3: ``MODEL_OUTLINE`` is the
    once-per-path, unrecoverable call; ``MODEL_LESSON`` is the high-volume one
    that may step *down*), so the harness keeps them separate too rather than
    pretending a run exercises a single model.
    """

    label: str
    outline: Model
    lesson: Model
    # Smoke only: force the expected branch via the stub's sentinel.
    force_expected_branch: bool = False


def _resolve_bindings(args: argparse.Namespace) -> list[ModelBinding] | None:
    """The model bindings to evaluate, or None on misconfiguration."""
    if args.smoke:
        stub = smoke_model()
        return [
            ModelBinding(
                label="smoke", outline=stub, lesson=stub, force_expected_branch=True
            )
        ]

    from aleph.config import settings

    if not settings.openrouter_api_key:
        print(
            "OPENROUTER_API_KEY is not set. Eval runs call the live provider; "
            "set the key (env or .env), or use --smoke for an offline "
            "plumbing check.",
            file=sys.stderr,
        )
        return None

    from aleph.services.openrouter import resolve_model

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
            )
        ]
    # A sweep entry binds the same id to both slots: the comparison that matters
    # for the allowlist is "how does this model do at the whole job".
    return [
        ModelBinding(
            label=model_id,
            outline=resolve_model(model_id),
            lesson=resolve_model(model_id),
        )
        for model_id in sweep
    ]


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


def _hard_floor_failures(report: SeedReport) -> list[str]:
    """``case: reason`` strings for every hard-floor violation or error.

    Three distinct ways a run can fail to clear the floor, all of which must
    exit 1 — an unscored case is never a pass:

    1. the *task* errored (``report.failures``), so there is no generation;
    2. an *evaluator* errored (``case.evaluator_failures``) — pydantic-evals
       keeps the case in ``report.cases`` and simply omits that evaluator's
       assertion, so a crashing ``RefusalBranch`` would otherwise leave the
       safety check silently unrun and the run green;
    3. a hard-floor assertion is missing for any other reason (an evaluator
       dropped from the dataset, a rename that broke the registration) — the
       CLI keys its exit code on these names, so an absent name is a harness
       bug, not an implicit pass.
    """
    failed = [f"{failure.name}: errored" for failure in report.failures]
    for case in report.cases:
        crashed = {failure.name for failure in case.evaluator_failures}
        for failure in case.evaluator_failures:
            failed.append(
                f"{case.name}/{failure.name}: evaluator errored "
                f"({failure.error_message})"
            )
        for name in sorted(HARD_FLOOR_EVALUATORS):
            result = case.assertions.get(name)
            if result is None:
                if name not in crashed:
                    failed.append(
                        f"{case.name}/{name}: hard-floor assertion missing from "
                        "the report (evaluator not registered?)"
                    )
            elif not result.value:
                failed.append(f"{case.name}/{name}: {result.reason}")
    return failed


def _append_step_summary(label: str, report: SeedReport) -> None:
    """Mirror the report table into the GitHub Actions job summary."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write(
            f"## Seed-set evals — {label}\n\n"
            f"```\n{report.render(width=_REPORT_WIDTH, include_reasons=True)}```\n\n"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evals", description="Run the Aleph agent eval harness."
    )
    parser.add_argument(
        "--models",
        default="",
        help="comma-separated OpenRouter model ids to sweep; each is bound to "
        "both the outline and lesson slots (default: the configured "
        "MODEL_OUTLINE / MODEL_LESSON)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="offline plumbing check with the deterministic stub model "
        "(no key, no network)",
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

    bindings = _resolve_bindings(args)
    if bindings is None:
        return 2

    dataset = load_seed_set()
    results: dict[str, Any] = {}
    hard_failures: dict[str, list[str]] = {}
    for binding in bindings:
        report = asyncio.run(
            dataset.evaluate(
                build_generation_task(
                    binding.outline,
                    binding.lesson,
                    force_expected_branch=binding.force_expected_branch,
                ),
                name=f"seed-set ({binding.label})",
                max_concurrency=args.max_concurrency,
            )
        )
        report.print(width=_REPORT_WIDTH, include_reasons=True)
        _append_step_summary(binding.label, report)
        results[binding.label] = _case_payload(report)
        failed_cases = _hard_floor_failures(report)
        if failed_cases:
            hard_failures[binding.label] = failed_cases

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps({"seed_set": results}, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.report}")

    if hard_failures:
        for label, failures in hard_failures.items():
            print(f"HARD FLOOR FAILED [{label}]:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
