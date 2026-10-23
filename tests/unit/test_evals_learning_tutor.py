"""Offline plumbing and independent counterexamples, never model-quality evidence."""

import json

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior

from aleph.agents.learning_tutor import LearningTutorOutput, output_violation
from evals.learning_tutor import build_task, load_cases, output_checks


@pytest.fixture(autouse=True)
def disallow_paid_model_requests(monkeypatch):
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)


def test_curated_cases_and_real_predicate():
    cases = load_cases()
    assert output_checks is output_violation
    assert len(cases) >= 20
    assert len({case.name for case in cases}) == len(cases)
    for case in cases:
        assert case.expected and case.forbidden
        assert output_violation(case.smoke, case.deps()) is None
        assert output_checks(case.smoke, case.deps()) is None


@pytest.mark.anyio
async def test_every_case_runs_the_actual_agent_offline():
    for case in load_cases():
        assert await build_task()(case) == case.smoke


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"evidence_keys": ["invented"]}, "Unknown evidence"),
        ({"kind": "beat"}, "Beat recommendations unavailable"),
        ({"topic": "  PYTHON  "}, "Exact Topic is excluded"),
    ],
)
@pytest.mark.anyio
async def test_invalid_candidates_fail_predicate_and_agent(change, reason):
    case = next(c for c in load_cases() if c.name == "foundational-path")
    raw = json.loads(case.smoke.model_dump_json())
    raw["recommendations"][0].update(change)
    candidate = LearningTutorOutput.model_validate(raw)
    assert output_checks(candidate, case.deps()) == reason
    with pytest.raises(UnexpectedModelBehavior):
        await build_task()(case.model_copy(update={"smoke": candidate}))


@pytest.mark.anyio
async def test_over_capacity_and_duplicate_cards_fail():
    case = next(c for c in load_cases() if c.name == "foundational-path")
    candidate = case.smoke.model_copy(
        update={"recommendations": case.smoke.recommendations * 2}
    )
    assert output_checks(candidate, case.deps()) == "Recommendation capacity exceeded"
    with pytest.raises(UnexpectedModelBehavior):
        await build_task()(case.model_copy(update={"smoke": candidate}))
    refresh = case.model_copy(update={"kind": "refresh"})
    candidate = candidate.model_copy(update={"reply": None})
    assert output_checks(candidate, refresh.deps()) == "Exact Topic is excluded"


@pytest.mark.parametrize(
    "item", ["grounding", "utility", "novelty", "privacy", "safety"]
)
@pytest.mark.anyio
async def test_independent_judge_dimensions_and_hard_floors(item):
    from evals.__main__ import _gate_summary, _hard_floor_failures
    from evals.judge import build_stub_judge
    from evals.learning_tutor import (
        ASSERTIONS,
        HARD_FLOORS,
        JUDGE_CHECKS,
        JUDGE_HARD_FLOORS,
        dataset,
    )

    cases = load_cases()
    cases[0] = cases[0].model_copy(
        update={"expected": cases[0].expected + f" [judge-fail:{item}]"}
    )
    report = await dataset(cases, build_stub_judge()).evaluate(
        build_task(), progress=False
    )
    gate = _gate_summary(
        report,
        judged=True,
        hard_floor_evaluators=HARD_FLOORS,
        judge_assertions=JUDGE_CHECKS,
        assertions_map=ASSERTIONS,
        safety_assertion="safety",
    )
    assert gate.pass_rate == 23 / 24
    failures = _hard_floor_failures(
        report, HARD_FLOORS | JUDGE_HARD_FLOORS, assertions_map=ASSERTIONS
    )
    assert bool(failures) == (item in JUDGE_HARD_FLOORS)


def test_cli_smoke_and_report(tmp_path, monkeypatch):
    from evals.__main__ import main

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    for flags in ([], ["--judge"]):
        path = tmp_path / "report.json"
        assert main(["--learning-tutor", "--smoke", *flags, "--report", str(path)]) == 0
        report = json.loads(path.read_text())["learning_tutor"]
        assert report["smoke"] is True
        assert report["passed"] is True
        assert "NOT recommendation quality" in report["caveat"]
        assert len(report["cases"]) == 24


@pytest.mark.parametrize("flags", [["--briefs"], ["--agreement"], ["--models", "fake"]])
def test_cli_rejects_ambiguous_modes(flags):
    from evals.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--learning-tutor", "--smoke", *flags])
    assert exc.value.code == 2


@pytest.mark.parametrize("content", ["", "{}", "[]", "null"])
def test_broken_seed_data_is_configuration_failure(tmp_path, content):
    path = tmp_path / "bad.yaml"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_cases(path)


def test_judge_prompt_contains_evidence_and_criteria_not_script():
    from evals.learning_tutor import judge_prompt

    case = load_cases()[0]
    sentinel = "SCRIPT_MUST_NOT_LEAK"
    case = case.model_copy(update={"smoke": LearningTutorOutput(reply=sentinel)})
    prompt = judge_prompt(case, LearningTutorOutput())
    assert sentinel not in prompt
    assert case.expected in prompt and case.forbidden in prompt
    assert json.loads(prompt.split("\n", 1)[1])["context"] == json.loads(
        case.deps().context
    )


@pytest.mark.anyio
async def test_missing_checks_fail_closed():
    from evals.__main__ import _hard_floor_failures
    from evals.learning_tutor import ASSERTIONS, HARD_FLOORS, JUDGE_HARD_FLOORS, dataset

    report = await dataset(load_cases()).evaluate(build_task(), progress=False)
    # A judged report without judge assertions cannot pass the hard floors.
    assert (
        len(
            _hard_floor_failures(
                report, HARD_FLOORS | JUDGE_HARD_FLOORS, assertions_map=ASSERTIONS
            )
        )
        == 72
    )
    report.cases[0].assertions.clear()
    assert _hard_floor_failures(report, HARD_FLOORS, assertions_map=ASSERTIONS)


@pytest.mark.anyio
async def test_bad_schema_and_unknown_target_error_the_agent():
    case = load_cases()[0]
    bad = LearningTutorOutput(
        existing_work=[{"kind": "path", "supplied_target_key": "unknown"}]
    )
    with pytest.raises(UnexpectedModelBehavior):
        await build_task()(case.model_copy(update={"smoke": bad}))
    card = next(c for c in load_cases() if c.name == "foundational-path").smoke
    bad = card.model_copy(update={"recommendations": card.recommendations * 4})
    with pytest.raises(UnexpectedModelBehavior):
        await build_task()(case.model_copy(update={"smoke": bad}))


@pytest.mark.anyio
async def test_agent_failure_is_not_dropped_from_rate_denominator():
    from evals.__main__ import _gate_summary, _hard_floor_failures
    from evals.learning_tutor import ASSERTIONS, HARD_FLOORS, dataset

    cases = load_cases()
    cases[0] = cases[0].model_copy(
        update={"smoke": LearningTutorOutput(reply="Invalid refresh")}
    )
    report = await dataset(cases).evaluate(build_task(), progress=False)
    assert len(report.failures) == 1
    gate = _gate_summary(
        report,
        judged=False,
        hard_floor_evaluators=HARD_FLOORS,
        assertions_map=ASSERTIONS,
    )
    assert gate.total == 24 and gate.pass_rate == 23 / 24
    assert _hard_floor_failures(report, HARD_FLOORS, assertions_map=ASSERTIONS) == [
        "empty-refresh: errored"
    ]


@pytest.mark.parametrize("bad_items", [[], ["grounding"] * 5])
def test_learning_judge_rejects_missing_or_duplicate_dimensions(bad_items):
    from pydantic_ai import ModelRetry

    from evals.rubric import LEARNING_ITEMS, JudgeVerdict, validate_verdict

    verdict = JudgeVerdict.model_validate(
        {
            "items": [
                {"item": item, "passed": True, "reason": "Synthetic verdict"}
                for item in bad_items
            ]
        }
    )
    with pytest.raises(ModelRetry):
        validate_verdict(LEARNING_ITEMS, verdict)


@pytest.mark.parametrize("item", ["grounding", "privacy", "safety"])
def test_cli_critical_failure_blocks_even_above_rate_threshold(
    item, tmp_path, monkeypatch
):
    from evals.__main__ import main

    cases = load_cases()
    cases[0] = cases[0].model_copy(
        update={"expected": cases[0].expected + f" [judge-fail:{item}]"}
    )
    monkeypatch.setattr("evals.learning_tutor.load_cases", lambda: cases)
    path = tmp_path / "failed.json"
    assert main(["--learning-tutor", "--smoke", "--judge", "--report", str(path)]) == 1
    report = json.loads(path.read_text())["learning_tutor"]
    assert report["passed"] is False
    assert report["hard_floor_failures"]
